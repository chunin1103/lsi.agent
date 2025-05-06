"""
LSI Keyword API – v2.0  (async / streaming)
-------------------------------------------
* First keyword arrives in ≈ 0.5 s (Autosuggest RTT).
* Google‑Ads Planner is fetched in a worker thread so it
  never blocks the event‑loop.
* /keywords/stream streams a JSON array that grows while
  the crawler is still running.
"""
from __future__ import annotations

import asyncio
import json
import logging
import pathlib
import time
from collections import deque
from typing import AsyncGenerator, Dict, List

import httpx
import yaml
from fastapi import FastAPI, Query, Request
from fastapi.responses import PlainTextResponse, StreamingResponse
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from google.ads.googleads.client import GoogleAdsClient
from google.ads.googleads.errors import GoogleAdsException
from pydantic import BaseModel
from urllib.parse import quote

# ───────── State of running crawlers ──────────────────────────────
# seed  ->  {"suggest_done": bool, "planner_done": bool}
_STATUS: dict[str, dict[str, bool]] = {}
_STATUS_LOCK = asyncio.Lock()

# ------------------------------------------------------------------
# Logging
# ------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("lsi-api")

# ------------------------------------------------------------------
# Config
# ------------------------------------------------------------------
_DESC_PATH = pathlib.Path(__file__).with_name("api_swagger.md")
_DEF_TOTAL = 100000          # hard limit returned to any client
_TTL       = 3600         # cache freshness (s)
_SLEEP     = 1            # polite delay between Suggest calls (s)
_MAX_DEPTH = 1            # BFS depth for Suggest crawl

try:
    OPENAPI_DESC = _DESC_PATH.read_text(encoding="utf-8")
except FileNotFoundError:
    OPENAPI_DESC = "Vietnamese LSI keyword micro‑service."

# ------------------------------------------------------------------
# Google Ads Planner (runs in a worker thread)
# ------------------------------------------------------------------
def _load_ads_client(path: str | pathlib.Path = "./google-ads.yaml"):
    """Reads YAML and returns (client, customer_id)."""
    with open(path, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    return GoogleAdsClient.load_from_dict(cfg), cfg.get("my_customer_id")


def keyword_planner_ideas(seed: str, max_results: int) -> List[str]:
    """Blocking gRPC call – run with asyncio.to_thread()."""
    try:
        client, cid = _load_ads_client()
        svc = client.get_service("KeywordPlanIdeaService")
        req = client.get_type("GenerateKeywordIdeasRequest")
        req.customer_id = cid
        req.keyword_seed.keywords.append(seed)
        req.language = "languageConstants/1040"          # Vietnamese
        req.geo_target_constants.append("geoTargetConstants/2704")  # Vietnam
        req.keyword_plan_network = client.enums.KeywordPlanNetworkEnum.GOOGLE_SEARCH
        out: List[str] = []
        for idea in svc.generate_keyword_ideas(request=req).results:
            out.append(idea.text)
            if len(out) >= max_results:
                break
        log.info("Planner ➜ %d kw for '%s'", len(out), seed)
        return out
    except (GoogleAdsException, FileNotFoundError) as ex:
        log.warning("Planner skipped '%s': %s", seed, ex)
        return []

# ------------------------------------------------------------------
# Google Autosuggest (non‑blocking, httpx async)
# ------------------------------------------------------------------
_HEADERS = {"User-Agent": "Mozilla/5.0"}

async def google_autosuggest(q: str, lang: str = "vi", ctr: str = "VN",
                             timeout: float = 8.0) -> List[str]:
    url = "https://suggestqueries.google.com/complete/search"
    params = {"client": "chrome", "hl": lang, "gl": ctr, "q": q, "num": 20}
    async with httpx.AsyncClient(timeout=timeout) as client:
        r = await client.get(url, params=params, headers=_HEADERS)
        r.raise_for_status()
        return r.json()[1]

# ------------------------------------------------------------------
# Cache
# ------------------------------------------------------------------
# seed → {kw: list[str], ts: float, suggest: int, planner: int}
_CACHE: Dict[str, Dict] = {}
_CACHE_LOCK = asyncio.Lock()

def _merge_unique(base: List[str], extra: List[str]) -> List[str]:
    seen, merged = set(base), list(base)
    for kw in extra:
        if kw not in seen:
            seen.add(kw)
            merged.append(kw)
            if len(merged) >= _DEF_TOTAL:
                break
    return merged

async def _update_cache(seed: str, new_kw: List[str], source: str):
    """source='suggest'|'planner'; updates stats & timestamp."""
    async with _CACHE_LOCK:
        d = _CACHE.get(seed, {"kw": [], "ts": 0, "suggest": 0, "planner": 0})
        before = len(d["kw"])
        d["kw"] = _merge_unique(d["kw"], new_kw)
        added = len(d["kw"]) - before
        d[source] += added
        d["ts"] = time.time()
        _CACHE[seed] = d
        if added:
            log.info("Cache[%s] +%d (%s) ⇒ %d", seed, added, source, len(d["kw"]))

# ------------------------------------------------------------------
# Async workers
# ------------------------------------------------------------------
async def _crawl_suggest(seed: str, queue: asyncio.Queue | None = None):
    async with _STATUS_LOCK:
        _STATUS[seed]["suggest_done"] = False
    """
    Breadth‑first crawl of Google Autosuggest.

    The crawler explores every suggestionit finds, layer by layer,
    until `depth == _MAX_DEPTH` **or** the global `_DEF_TOTAL` cap
    is reached.  Each keyword discovered is pushed to `queue`
    (for the streaming API) and merged into the cache.
    """
    log.info("[Suggest] start '%s'", seed)

    visited: set[str] = set()           # queries we already fetched
    dq: deque[tuple[str, int]] = deque([(seed, 0)])
    collected: list[str] = []

    while dq and len(collected) < _DEF_TOTAL * 2:
        query, depth = dq.popleft()
        if query in visited:
            continue

        visited.add(query)
        try:
            suggestions = await google_autosuggest(query)
        except Exception as exc:
            log.warning("Suggest failed '%s': %s", query, exc)
            suggestions = []

        # Keep only genuinely new keywords for cache / upstream UI
        new_kw = [s for s in suggestions if s not in collected]
        if new_kw:
            collected.extend(new_kw)
            await _update_cache(seed, new_kw, "suggest")
            if queue:
                for kw in new_kw:
                    await queue.put(kw)

        # En‑queue children for the next depth level
        if depth < _MAX_DEPTH:
            for child in suggestions:
                if child not in visited:
                    dq.append((child, depth + 1))

        # Politeness delay between outward requests
        await asyncio.sleep(_SLEEP)

    log.info("[Suggest] done '%s' total=%d", seed, len(collected))
    async with _STATUS_LOCK:
        _STATUS[seed]["suggest_done"] = True
    if queue:
        await queue.put(None)           # sentinel – producer finished

async def _planner_task(seed: str, queue: asyncio.Queue | None = None):
    async with _STATUS_LOCK:
        _STATUS[seed]["planner_done"] = False
    kw = await asyncio.to_thread(keyword_planner_ideas, seed, _DEF_TOTAL // 2)
    if kw:
        await _update_cache(seed, kw, "planner")
        if queue:
            for w in kw:
                await queue.put(w)
    async with _STATUS_LOCK:
        _STATUS[seed]["planner_done"] = True
    if queue:
        await queue.put(None)          # sentinel

async def _ensure_tasks(seed: str, planner: bool):
    async with _STATUS_LOCK:
        if seed not in _STATUS or                      \
           _STATUS[seed].get("suggest_done") and       \
           (not planner or _STATUS[seed].get("planner_done")):
            # brand‑new or previous run finished ➜ reset flags
            _STATUS[seed] = {"suggest_done": False,
                             "planner_done": not planner}
    async with _CACHE_LOCK:
        fresh = seed in _CACHE and (time.time() - _CACHE[seed]["ts"] < _TTL)
        need_planner = planner and (not fresh or _CACHE[seed]["planner"] == 0)
    # Always launch Suggest crawler if cache is stale/missing
    if not fresh:
        asyncio.create_task(_crawl_suggest(seed))
    # Launch Planner separately
    if need_planner:
        asyncio.create_task(_planner_task(seed))

# ------------------------------------------------------------------
# FastAPI
# ------------------------------------------------------------------
app = FastAPI(
    title="LSI Keyword API",
    version="2.0.0",
    description=OPENAPI_DESC,
)

class KeywordPage(BaseModel):
    seed: str
    total_available: int
    page: int
    page_size: int
    keywords: List[str]
    
class CrawlStatus(BaseModel):
    seed: str
    total: int
    finished: bool

# ---------- Non‑streaming snapshot --------------------------------
@app.get("/keywords", response_model=KeywordPage, summary="Paginated keywords")
async def get_keywords(
    seed: str = Query(..., min_length=1, description="Seed keyword"),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=100),
    planner: bool = Query(True, description="Also fetch Google Ads planner?"),
):
    await _ensure_tasks(seed, planner)
    d = _CACHE.get(seed)
    if not d:
        return KeywordPage(seed=seed, total_available=0,
                           page=page, page_size=page_size, keywords=[])
    start = (page - 1) * page_size
    return KeywordPage(
        seed=seed,
        total_available=len(d["kw"]),
        page=page,
        page_size=page_size,
        keywords=d["kw"][start:start + page_size],
    )

# ---------- Streaming endpoint ------------------------------------
def _content_disposition(seed: str) -> str:
    """
    Build a Content‑Disposition header that survives Vietnamese characters
    by falling back to RFC 5987 UTF‑8 encoding.
    """
    try:                          # plain ASCII filename works directly
        seed.encode("ascii")
        return f'inline; filename="{seed}.json"'
    except UnicodeEncodeError:    # non‑ASCII → UTF‑8 percent‑encoded
        encoded = quote(f"{seed}.json", encoding="utf-8")
        return f"inline; filename=keywords.json; filename*=UTF-8''{encoded}"

async def _json_stream(
    queue: asyncio.Queue,
    expected: int,                    # 1 = Suggest only, 2 = Suggest+Planner
) -> AsyncGenerator[bytes, None]:
    """
    Stream a JSON array incrementally, flushing after every keyword.
    A newline is appended to each element so most HTTP clients render
    it immediately instead of buffering small chunks.
    """
    # Send the opening bracket right away
    yield b"["
    first = True
    finished = 0

    while True:
        item = await queue.get()

        # -- handle producer sentinel ----------------------------------
        if item is None:
            finished += 1
            if finished == expected:      # all producers done
                break
            continue

        # -- normal keyword --------------------------------------------
        prefix = b"" if first else b","
        first = False
        yield prefix + json.dumps(item).encode() + b"\n"   # flush hint

    yield b"]"                          # close JSON array

@app.get("/keywords/stream", summary="Incremental keyword stream")
async def stream_keywords(
    seed: str = Query(..., min_length=1, description="Seed keyword"),
    planner: bool = Query(True),
):
    await _ensure_tasks(seed, planner)
    q = asyncio.Queue = asyncio.Queue()      # unlimited size is fine
    # launch producers
    asyncio.create_task(_crawl_suggest(seed, q))
    if planner:
        asyncio.create_task(_planner_task(seed, q))

    expected = 2 if planner else 1          # how many producers to wait for
    headers = {"Content-Disposition": _content_disposition(seed)}
    return StreamingResponse(
        _json_stream(q, expected),          # <-- pass expected
        media_type="application/json",
        headers=headers,
    )

# ---------- Diagnostics -------------------------------------------
@app.get("/status", response_model=CrawlStatus,
         summary="Is the crawler finished for this seed?")
async def crawl_status(
    seed: str = Query(...),
    planner: bool = Query(True, description="Also wait for planner?"),
):
    # kick‑off background work if it hasn’t started yet
    await _ensure_tasks(seed, planner)
    async with _CACHE_LOCK:
        cached = _CACHE.get(seed)
    async with _STATUS_LOCK:
        s = _STATUS.get(seed, {})
    total = len(cached["kw"]) if cached else 0
    finished = s.get("suggest_done", False) and s.get("planner_done", False)
    return CrawlStatus(seed=seed, total=total, finished=finished)

@app.get("/clear_cache", response_class=PlainTextResponse)
async def clear_cache():
    async with _CACHE_LOCK:
        _CACHE.clear()
    return "cache cleared"

@app.get("/", response_class=PlainTextResponse)
async def root():
    return OPENAPI_DESC.split("\n", 1)[0] + "\nDocs: /docs"
