# ---------------- 1. google_autosuggest ----------------
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
import time


def google_autosuggest(query: str,
                       language: str = "vi",
                       country: str = "VN",
                       vertical: str | None = None,
                       timeout: int = 60,
                       max_retries: int = 3,
                       backoff: float = 0.5) -> list[str]:
    """Return Google-Suggest phrases for *query* (undocumented API)."""
    url = "https://suggestqueries.google.com/complete/search"
    params = {
        "client": "chrome",     # JSON output
        "hl": language,
        "gl": country,
        "q": query,
        "num": 20
    }
    if vertical:
        params["ds"] = vertical

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/124 Safari/537.36")
    }

    sess = requests.Session()
    sess.mount(
        "https://",
        HTTPAdapter(max_retries=Retry(
            total=max_retries,
            backoff_factor=backoff,
            status_forcelist=[429, 500, 502, 503, 504],
            allowed_methods=["GET"],
            respect_retry_after_header=True))
    )

    r = sess.get(url, params=params, headers=headers, timeout=timeout)
    r.raise_for_status()
    return r.json()[1]           # list of suggestions


# ---------------- 2. crawl_autosuggest_to_files --------
import time
from pathlib import Path
from collections import deque, defaultdict
from typing import Dict, List, Set, Tuple

def crawl_autosuggest_to_files(seed_query: str,
                               max_depth: int = 2,
                               sleep_sec: float = 0.8,
                               out_dir: str | Path = "suggest_output"
                               ) -> None:
    """
    Breadth-first crawl of Google-Suggest.
    Writes suggestions of each depth to depth<N>.txt in *out_dir*.
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    # one set per depth so we don’t write duplicates
    seen_at_depth: defaultdict[int, Set[str]] = defaultdict(set)
    visited: Set[str] = set()
    queue: deque[Tuple[str, int]] = deque([(seed_query, 0)])

    # write seed to depth0.txt (comment out if not desired)
    (out_dir / "depth0.txt").write_text(seed_query + "\n", encoding="utf-8")
    seen_at_depth[0].add(seed_query)

    while queue:
        query, depth = queue.popleft()
        if depth >= max_depth or query in visited:
            continue

        suggestions = google_autosuggest(query)
        time.sleep(sleep_sec)    # polite delay
        visited.add(query)

        next_depth = depth + 1
        new_items = [s for s in suggestions
                     if s not in seen_at_depth[next_depth]]

        if new_items:
            with open(out_dir / f"depth{next_depth}.txt",
                      "a", encoding="utf-8") as f:
                for s in new_items:
                    f.write(s + "\n")
            seen_at_depth[next_depth].update(new_items)

        # queue children
        if next_depth < max_depth:
            for s in new_items:
                queue.append((s, next_depth))


# ---------------- 3. run ----------------
if __name__ == "__main__":
    start = time.perf_counter()          # ← pick up the start time for process record

    crawl_autosuggest_to_files(
        seed_query="nước mắm",
        max_depth=4,     # depth0, depth1, depth2, etc will be produced
        sleep_sec=0.8
    )

    elapsed = time.perf_counter() - start    # ← total wall-clock seconds

    print(f"Done in {elapsed:.2f} s. "
          "Check the 'suggest_output' directory for depth*.txt files.")
