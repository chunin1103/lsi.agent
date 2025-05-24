"""
Keep this here to run uvicorn lsi_keyword_api:app --reload
LSI Keyword API – v2.2  (async / SSE streaming - Corrected)
-------------------------------------------
* /keywords/stream now uses Server-Sent Events (SSE)
  by yielding dictionaries to EventSourceResponse.
"""
from __future__ import annotations

import asyncio
import json
import logging
import pathlib
import time
from collections import deque
from typing import AsyncGenerator, Dict, List, Any # Ensure Any is imported

import httpx
import yaml
from fastapi import FastAPI, Query, Request 
from fastapi.responses import PlainTextResponse
from sse_starlette.sse import EventSourceResponse 

from google.ads.googleads.client import GoogleAdsClient
from google.ads.googleads.errors import GoogleAdsException
from pydantic import BaseModel

# (Keep all other existing code like _STATUS, Logging, Config, Google Ads, Autosuggest, Cache, _crawl_suggest, _planner_task, _ensure_tasks the same as in your version from lsi_keyword_api_sse)
# ... (all your existing code from the previous FastAPI/SSE version) ...
# Make sure all imports and global variables (_STATUS, _CACHE, locks, etc.) are present.
# The following is just the _sse_event_stream and the endpoint that uses it.
# Assume all other functions (_crawl_suggest, _planner_task, _update_cache, _ensure_tasks, etc.)
# and imports from your previous lsi_keyword_api_sse.py are still here.

# ───────── State of running crawlers ──────────────────────────────
# seed  ->  {"suggest_done": bool, "planner_done": bool}
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
_DEF_TOTAL = 100000      # hard limit returned to any client (overall)
_TTL       = 3600        # cache freshness (s)
_SLEEP     = 1           # polite delay between Suggest calls (s)
_MAX_DEPTH = 1           # BFS depth for Suggest crawl

try:
    OPENAPI_DESC = _DESC_PATH.read_text(encoding="utf-8")
except FileNotFoundError:
    OPENAPI_DESC = "Vietnamese LSI keyword micro‑service (SSE enabled)."

# ------------------------------------------------------------------
# Google Ads Planner (runs in a worker thread)
# ------------------------------------------------------------------
def _load_ads_client(path: str | pathlib.Path = "./google-ads.yaml"):
    """Reads YAML and returns (client, customer_id)."""
    with open(path, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    customer_id = cfg.get("my_customer_id")
    if customer_id is not None:
        customer_id = str(customer_id)
    return GoogleAdsClient.load_from_dict(cfg), customer_id


def keyword_planner_ideas(seed: str, max_results: int) -> List[str]:
    """Blocking gRPC call – run with asyncio.to_thread()."""
    try:
        client, cid = _load_ads_client()
        if not cid:
            log.warning("Planner skipped '%s': Missing 'my_customer_id' in google-ads.yaml", seed)
            return []
        svc = client.get_service("KeywordPlanIdeaService")
        req = client.get_type("GenerateKeywordIdeasRequest")
        req.customer_id = cid
        if not req.keyword_seed.keywords:
             req.keyword_seed.keywords.append(seed)
        else: 
            req.keyword_seed.keywords = [seed]
        req.language = "languageConstants/1040"
        if not req.geo_target_constants:
            req.geo_target_constants.append("geoTargetConstants/2704")
        else:
            req.geo_target_constants = ["geoTargetConstants/2704"]
        req.keyword_plan_network = client.enums.KeywordPlanNetworkEnum.GOOGLE_SEARCH
        out: List[str] = []
        response_iterator = svc.generate_keyword_ideas(request=req)
        for idea_result in response_iterator.results:
            out.append(idea_result.text)
            if len(out) >= max_results:
                break
        log.info("Planner ➜ %d kw for '%s'", len(out), seed)
        return out
    except GoogleAdsException as ex:
        log.warning("Planner GoogleAdsException for '%s': %s", seed, ex)
        for error in ex.failure.errors: # type: ignore
            log.warning("  Error: %s, Message: %s", error.error_code, error.message)
            if error.trigger:
                log.warning("    Trigger: %s", error.trigger.string_value)
        return []
    except FileNotFoundError:
        log.warning("Planner skipped '%s': google-ads.yaml not found.", seed)
        return []
    except Exception as ex:
        log.error("Planner unexpected error for '%s': %s", seed, ex, exc_info=True)
        return []

# ------------------------------------------------------------------
# Google Autosuggest (non‑blocking, httpx async)
# ------------------------------------------------------------------
_HEADERS = {"User-Agent": "Mozilla/5.0"}

async def google_autosuggest(q: str, lang: str = "vi", ctr: str = "VN",
                             timeout: float = 8.0) -> List[str]:
    url = "https://suggestqueries.google.com/complete/search"
    params = {"client": "chrome", "hl": lang, "gl": ctr, "q": q, "num": "20"}
    async with httpx.AsyncClient(timeout=timeout) as client:
        try:
            r = await client.get(url, params=params, headers=_HEADERS)
            r.raise_for_status()
            response_json = r.json()
            if isinstance(response_json, list) and len(response_json) > 1 and isinstance(response_json[1], list):
                return [str(item) for item in response_json[1]]
            else:
                log.warning("Suggest unexpected JSON structure for '%s': %s", q, response_json)
                return []
        except httpx.HTTPStatusError as exc:
            log.warning("Suggest HTTPStatusError for '%s': %s - %s", q, exc.response.status_code, exc.response.text)
            return []
        except httpx.RequestError as exc:
            log.warning("Suggest RequestError for '%s': %s", q, str(exc))
            return []
        except json.JSONDecodeError as exc:
            log.warning("Suggest JSONDecodeError for '%s': %s", q, str(exc))
            return []
        except Exception as exc:
            log.error("Suggest unexpected error for '%s': %s", q, exc, exc_info=True)
            return []

# ------------------------------------------------------------------
# Cache
# ------------------------------------------------------------------
_CACHE: Dict[str, Dict[str, Any]] = {} 
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
    if not new_kw: 
        return
    async with _CACHE_LOCK:
        d = _CACHE.get(seed, {"kw": [], "ts": 0.0, "suggest": 0, "planner": 0})
        before = len(d["kw"])
        d["kw"] = _merge_unique(d["kw"], new_kw)
        added = len(d["kw"]) - before
        if source not in d:
            d[source] = 0
        d[source] += added # type: ignore
        d["ts"] = time.time()
        _CACHE[seed] = d
        if added:
            log.info("Cache[%s] +%d (%s) ⇒ %d", seed, added, source, len(d["kw"]))

# ------------------------------------------------------------------
# Async workers
# ------------------------------------------------------------------
async def _crawl_suggest(seed: str, queue: asyncio.Queue | None = None):
    log.info("[Suggest] start '%s'%s", seed, " (for stream)" if queue else " (for cache)")
    async with _STATUS_LOCK:
        if seed not in _STATUS:
            _STATUS[seed] = {"suggest_done": False, "planner_done": True} 
        _STATUS[seed]["suggest_done"] = False
    visited: set[str] = set()
    dq: deque[tuple[str, int]] = deque([(seed, 0)])
    collected_for_this_run: list[str] = []
    try:
        while dq: 
            query, depth = dq.popleft()
            if query in visited:
                continue
            visited.add(query)
            suggestions = []
            try:
                suggestions = await google_autosuggest(query)
            except Exception as exc: 
                log.warning("Suggest crawl failed for query '%s': %s", query, exc)
            new_kw_from_suggestions = [s for s in suggestions if s not in collected_for_this_run and s not in _CACHE.get(seed, {}).get("kw", [])]
            if new_kw_from_suggestions:
                collected_for_this_run.extend(new_kw_from_suggestions)
                await _update_cache(seed, new_kw_from_suggestions, "suggest")
                if queue:
                    for kw_to_send in new_kw_from_suggestions:
                        await queue.put(kw_to_send)
            if depth < _MAX_DEPTH:
                for child_suggestion in suggestions:
                    if child_suggestion not in visited:
                        dq.append((child_suggestion, depth + 1))
            await asyncio.sleep(_SLEEP)
    except Exception as e:
        log.error(f"[Suggest] Unhandled error during crawl for '{seed}': {e}", exc_info=True)
        if queue: 
            await queue.put({"error": "Suggest crawler failed", "detail": str(e)}) 
    finally:
        log.info("[Suggest] done '%s', collected in this run: %d", seed, len(collected_for_this_run))
        async with _STATUS_LOCK:
            if seed in _STATUS: 
                 _STATUS[seed]["suggest_done"] = True
        if queue:
            await queue.put(None) 

async def _planner_task(seed: str, queue: asyncio.Queue | None = None):
    log.info("[Planner] start '%s'%s", seed, " (for stream)" if queue else " (for cache)")
    async with _STATUS_LOCK:
        if seed not in _STATUS: 
            _STATUS[seed] = {"suggest_done": True, "planner_done": False} 
        _STATUS[seed]["planner_done"] = False
    try:
        planner_keywords = await asyncio.to_thread(keyword_planner_ideas, seed, _DEF_TOTAL // 2)
        if planner_keywords:
            await _update_cache(seed, planner_keywords, "planner")
            if queue:
                for kw_to_send in planner_keywords:
                    await queue.put(kw_to_send)
    except Exception as e:
        log.error(f"[Planner] Unhandled error during task for '{seed}': {e}", exc_info=True)
        if queue: 
            await queue.put({"error": "Planner task failed", "detail": str(e)}) 
    finally:
        log.info("[Planner] done '%s'", seed)
        async with _STATUS_LOCK:
            if seed in _STATUS: 
                _STATUS[seed]["planner_done"] = True
        if queue:
            await queue.put(None) 

async def _ensure_tasks(seed: str, planner: bool):
    async with _STATUS_LOCK:
        if seed not in _STATUS or \
           (_STATUS[seed].get("suggest_done", False) and \
            (not planner or _STATUS[seed].get("planner_done", False))):
            log.info("Resetting task status for seed '%s' in _ensure_tasks.", seed)
            _STATUS[seed] = {
                "suggest_done": False,
                "planner_done": not planner 
            }
    async with _CACHE_LOCK:
        cache_entry = _CACHE.get(seed)
        is_cache_fresh = cache_entry and (time.time() - cache_entry["ts"] < _TTL)
        needs_planner_run_for_cache = planner and (
            not is_cache_fresh or cache_entry.get("planner", 0) == 0 # type: ignore
        )
    if seed in _STATUS and not _STATUS[seed].get("suggest_done"):
        if not is_cache_fresh: 
            log.info("Cache stale/missing for '%s'. Ensuring background Suggest task via _ensure_tasks.", seed)
            asyncio.create_task(_crawl_suggest(seed)) 
        else:
            log.info("Suggest cache fresh for '%s'. Background task not re-initiated by _ensure_tasks.", seed)
            if is_cache_fresh and cache_entry and cache_entry.get("suggest",0) > 0:
                 async with _STATUS_LOCK:
                      if seed in _STATUS: _STATUS[seed]["suggest_done"] = True
    if planner and seed in _STATUS and not _STATUS[seed].get("planner_done"):
        if needs_planner_run_for_cache:
            log.info("Planner task needed for '%s'. Ensuring background Planner task via _ensure_tasks.", seed)
            asyncio.create_task(_planner_task(seed)) 
        elif is_cache_fresh and cache_entry and cache_entry.get("planner",0) > 0 : 
             async with _STATUS_LOCK:
                 if seed in _STATUS: _STATUS[seed]["planner_done"] = True
        else:
            log.info("Planner results exist/fresh or not requested for '%s'. Background task not re-initiated by _ensure_tasks.", seed)

# ------------------------------------------------------------------
# FastAPI App
# ------------------------------------------------------------------
app = FastAPI(
    title="LSI Keyword API (SSE)", 
    version="2.2.0", # Incremented version for this fix
    description=OPENAPI_DESC,
)

class CrawlStatus(BaseModel):
    seed: str
    total_cached_keywords: int 
    suggest_task_complete: bool
    planner_task_complete: bool 

# ---------- SSE Streaming endpoint (Corrected) --------------------
async def _sse_event_stream(
    request: Request,
    seed_keyword: str,
    queue: asyncio.Queue,
    expected_producers: int,
) -> AsyncGenerator[Dict[str, Any], None]: # Return type is now Dict for sse-starlette
    """
    Async generator for Server-Sent Events.
    Yields dictionaries with "event", "data", "id", "retry" fields.
    EventSourceResponse will format these into valid SSE messages.
    Handles client disconnections.
    """
    finished_producers = 0
    event_id_counter = 0 

    try:
        while True:
            # Check for client disconnection before waiting for queue item
            if await request.is_disconnected():
                log.info("SSE client disconnected for seed '%s'. Stopping stream.", seed_keyword)
                break 

            try:
                # Wait for an item from the queue with a timeout
                item = await asyncio.wait_for(queue.get(), timeout=1.0)
                queue.task_done() 
            except asyncio.TimeoutError:
                # Timeout occurred, loop back to check for disconnection
                continue

            event_id_counter += 1
            current_event_id_str = str(event_id_counter) # ID should be a string

            if item is None:  # Sentinel from a producer
                finished_producers += 1
                log.debug("Producer finished for '%s'. Total finished: %d/%d", seed_keyword, finished_producers, expected_producers)
                if finished_producers >= expected_producers:
                    # Yield a dictionary for the 'done' event
                    yield {
                        "id": current_event_id_str,
                        "event": "done",
                        "data": json.dumps({ # Data must be a string
                            "seed": seed_keyword,
                            "message": "All keyword generation tasks complete."
                        })
                    }
                    log.info("SSE stream 'done' event yielded for seed '%s'", seed_keyword)
                    break  # End of stream
                continue  # More producers expected

            elif isinstance(item, dict) and "error" in item: # Error object from producer
                 # Yield a dictionary for the 'error' event
                yield {
                    "id": current_event_id_str,
                    "event": "error",
                    "data": json.dumps(item) # Data must be a string
                }
                log.warning("SSE stream 'error' event yielded for seed '%s': %s", seed_keyword, item.get("detail"))
                finished_producers +=1 
                if finished_producers >= expected_producers:
                    log.info("All producers (including errored ones) finished for '%s'.", seed_keyword)
                    break 
                continue

            # Regular keyword string
            else:
                # Yield a dictionary for the 'keyword' event
                yield {
                    "id": current_event_id_str,
                    "event": "keyword",
                    "data": json.dumps({"keyword": str(item)}) # Data must be a string
                }
                log.debug("SSE stream 'keyword' event yielded for seed '%s': %s", seed_keyword, item)

    except asyncio.CancelledError:
        log.info("SSE stream cancelled for seed '%s'. Client likely disconnected.", seed_keyword)
    except Exception as e:
        log.error(f"Unexpected error in SSE event stream for seed '{seed_keyword}': {e}", exc_info=True)
        # Try to send a final error event to the client if possible
        if not await request.is_disconnected():
            try:
                 # Yield a dictionary for the final 'error' event
                yield {
                    "id": str(event_id_counter + 1), # Use next ID
                    "event": "error",
                    "data": json.dumps({ # Data must be a string
                        "message": "An unexpected error occurred in the SSE stream.",
                        "detail": str(e)
                    })
                }
            except Exception as send_err:
                log.error(f"Could not send final error event for '{seed_keyword}': {send_err}")
    finally:
        log.info("SSE event stream generator for seed '%s' is concluding.", seed_keyword)


@app.get("/keywords/stream", summary="Real-time keyword stream using SSE")
async def stream_keywords_sse(
    request: Request, 
    seed: str = Query(..., min_length=1, description="Seed keyword"),
    planner: bool = Query(True, description="Include Google Ads Planner results?"),
):
    log.info("SSE stream request for seed '%s', planner: %s", seed, planner)
    await _ensure_tasks(seed, planner)
    sse_queue = asyncio.Queue()
    active_producers = 0
    
    log.debug("Creating Suggest task for SSE stream '%s'", seed)
    asyncio.create_task(_crawl_suggest(seed, sse_queue))
    active_producers += 1

    if planner:
        log.debug("Creating Planner task for SSE stream '%s'", seed)
        asyncio.create_task(_planner_task(seed, sse_queue))
        active_producers += 1
    
    log.info("SSE stream for '%s' expecting %d producers.", seed, active_producers)

    # Pass the generator that yields dictionaries
    return EventSourceResponse(
        _sse_event_stream(request, seed, sse_queue, active_producers),
        media_type="text/event-stream"
    )

# ---------- Diagnostics -------------------------------------------
@app.get("/status", response_model=CrawlStatus,
         summary="Check current keyword generation status for a seed")
async def crawl_status(
    seed: str = Query(..., min_length=1, description="Seed keyword to check status for"),
    planner: bool = Query(True, description="Is planner expected to run for this seed?"),
):
    await _ensure_tasks(seed, planner)
    total_keywords = 0
    async with _CACHE_LOCK:
        cached_data = _CACHE.get(seed)
        if cached_data:
            total_keywords = len(cached_data.get("kw", []))
    suggest_done_status = False
    planner_done_status = not planner 
    async with _STATUS_LOCK:
        status_data = _STATUS.get(seed)
        if status_data:
            suggest_done_status = status_data.get("suggest_done", False)
            if planner: 
                planner_done_status = status_data.get("planner_done", False)
    return CrawlStatus(
        seed=seed,
        total_cached_keywords=total_keywords,
        suggest_task_complete=suggest_done_status,
        planner_task_complete=planner_done_status,
    )

@app.get("/clear_cache", response_class=PlainTextResponse, summary="Flush all in-memory keyword caches")
async def clear_cache():
    async with _CACHE_LOCK:
        _CACHE.clear()
    async with _STATUS_LOCK:
        _STATUS.clear()
    log.info("Cache and task statuses cleared.")
    return "Cache and task statuses cleared."

@app.get("/", response_class=PlainTextResponse, include_in_schema=False)
async def root():
    return OPENAPI_DESC.split("\n", 1)[0] + "\nAPI Documentation available at: /docs"

