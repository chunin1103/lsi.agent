# services/cache_service.py
"""
Handles in-memory caching of keywords.
"""
import asyncio
import time
import logging
from typing import List, Dict, Any

import config # Direct import

# Initialize logger for this module
log = logging.getLogger(__name__) # This will create a logger named 'services.cache_service'

# Global cache store and lock
_CACHE: Dict[str, Dict[str, Any]] = {}
_CACHE_LOCK = asyncio.Lock()

def _merge_unique(base_list: List[str], extra_list: List[str], limit: int) -> List[str]:
    seen = set(base_list)
    merged = list(base_list) 
    for keyword in extra_list:
        if len(merged) >= limit:
            break 
        if keyword not in seen:
            seen.add(keyword)
            merged.append(keyword)
    return merged

async def update_cache(seed: str, new_keywords: List[str], source_name: str) -> int:
    if not new_keywords:
        return 0
    async with _CACHE_LOCK:
        cache_entry = _CACHE.get(seed)
        if cache_entry is None:
            cache_entry = {
                "kw": [], "ts": 0.0,
                "suggest_count": 0, "planner_count": 0
            }
        
        keywords_before_merge = len(cache_entry["kw"])
        cache_entry["kw"] = _merge_unique(
            cache_entry["kw"], new_keywords, config.DEFAULT_KEYWORD_LIMIT
        )
        keywords_after_merge = len(cache_entry["kw"])
        newly_added_count = keywords_after_merge - keywords_before_merge

        source_count_key = f"{source_name}_count"
        if source_count_key not in cache_entry: 
             cache_entry[source_count_key] = 0
        cache_entry[source_count_key] += newly_added_count 
        cache_entry["ts"] = time.time() 
        _CACHE[seed] = cache_entry

        if newly_added_count > 0:
            # Changed to DEBUG level for less console noise
            log.debug(
                "CACHE_UPDATE: Seed='%s', Source='%s', Added=%d, Total_Cached=%d, Source_Total=%d",
                seed, source_name, newly_added_count, keywords_after_merge, cache_entry[source_count_key]
            )
        return newly_added_count

async def get_cached_keywords(seed: str) -> List[str] | None:
    async with _CACHE_LOCK:
        cache_entry = _CACHE.get(seed)
        if cache_entry:
            return list(cache_entry["kw"]) 
    return None

async def get_cache_entry(seed: str) -> Dict[str, Any] | None:
    async with _CACHE_LOCK:
        entry = _CACHE.get(seed)
        if entry:
            return dict(entry) 
    return None

async def is_cache_fresh(seed: str) -> bool:
    async with _CACHE_LOCK:
        cache_entry = _CACHE.get(seed)
        if cache_entry:
            return (time.time() - cache_entry["ts"]) < config.CACHE_TTL_SECONDS
    return False

async def clear_all_cache_entries():
    async with _CACHE_LOCK:
        _CACHE.clear()
    log.info("CACHE_FLUSH: All cache entries cleared.") # Keep this as INFO

def get_cache_snapshot() -> Dict[str, Dict[str, Any]]:
    return dict(_CACHE)
