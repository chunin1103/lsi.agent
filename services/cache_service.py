# services/cache_service.py
"""
Handles in-memory caching of keywords.
"""
import asyncio
import time
import logging
from typing import List, Dict, Any

import config # Import from parent directory's config.py

# Initialize logger for this module
log = logging.getLogger(__name__)

# Global cache store and lock
# seed → {kw: list[str], ts: float, suggest_count: int, planner_count: int}
_CACHE: Dict[str, Dict[str, Any]] = {}
_CACHE_LOCK = asyncio.Lock()

def _merge_unique(base_list: List[str], extra_list: List[str], limit: int) -> List[str]:
    """
    Merges two lists of strings, ensuring uniqueness and respecting a total limit.
    The base_list is preserved, and items from extra_list are added if new.
    """
    seen = set(base_list)
    merged = list(base_list) # Start with a copy of the base list

    for keyword in extra_list:
        if len(merged) >= limit:
            break # Stop if the overall limit is reached
        if keyword not in seen:
            seen.add(keyword)
            merged.append(keyword)
    return merged

async def update_cache(seed: str, new_keywords: List[str], source_name: str) -> int:
    """
    Updates the cache with new keywords for a given seed and source.

    Args:
        seed: The seed keyword.
        new_keywords: A list of newly discovered keywords.
        source_name: The source of the keywords (e.g., "suggest", "planner").

    Returns:
        The number of keywords actually added to the cache for this seed from this update.
    """
    if not new_keywords:
        return 0

    async with _CACHE_LOCK:
        cache_entry = _CACHE.get(seed)
        if cache_entry is None:
            # Initialize new cache entry
            cache_entry = {
                "kw": [],
                "ts": 0.0,
                "suggest_count": 0, # Number of keywords from suggest
                "planner_count": 0  # Number of keywords from planner
            }
        
        keywords_before_merge = len(cache_entry["kw"])
        
        # Merge new keywords, ensuring uniqueness and respecting the global limit
        cache_entry["kw"] = _merge_unique(
            cache_entry["kw"],
            new_keywords,
            config.DEFAULT_KEYWORD_LIMIT
        )
        
        keywords_after_merge = len(cache_entry["kw"])
        newly_added_count = keywords_after_merge - keywords_before_merge

        # Update count for the specific source
        source_count_key = f"{source_name}_count"
        if source_count_key not in cache_entry: # Should always be there if initialized properly
             cache_entry[source_count_key] = 0
        cache_entry[source_count_key] += newly_added_count # Increment by newly added unique keywords

        cache_entry["ts"] = time.time() # Update timestamp
        _CACHE[seed] = cache_entry

        if newly_added_count > 0:
            log.info(
                "CACHE_UPDATE: Seed='%s', Source='%s', Added=%d, Total_Cached=%d, Source_Total=%d",
                seed, source_name, newly_added_count, keywords_after_merge, cache_entry[source_count_key]
            )
        return newly_added_count

async def get_cached_keywords(seed: str) -> List[str] | None:
    """Retrieves all cached keywords for a given seed."""
    async with _CACHE_LOCK:
        cache_entry = _CACHE.get(seed)
        if cache_entry:
            return list(cache_entry["kw"]) # Return a copy
    return None

async def get_cache_entry(seed: str) -> Dict[str, Any] | None:
    """Retrieves the full cache entry (including metadata) for a seed."""
    async with _CACHE_LOCK:
        entry = _CACHE.get(seed)
        if entry:
            return dict(entry) # Return a copy
    return None

async def is_cache_fresh(seed: str) -> bool:
    """Checks if the cache for a given seed is still within its TTL."""
    async with _CACHE_LOCK:
        cache_entry = _CACHE.get(seed)
        if cache_entry:
            return (time.time() - cache_entry["ts"]) < config.CACHE_TTL_SECONDS
    return False

async def clear_all_cache_entries():
    """Clears all entries from the cache."""
    async with _CACHE_LOCK:
        _CACHE.clear()
    log.info("CACHE_FLUSH: All cache entries cleared.")

def get_cache_snapshot() -> Dict[str, Dict[str, Any]]:
    """Returns a snapshot of the current cache (for diagnostics, read-only)."""
    # This is a synchronous read, be mindful if used in async context without lock
    # For a truly safe snapshot in an async app, it should also use the lock or be called from sync context.
    # For simplicity in a diagnostic endpoint, direct access might be acceptable if races are not critical.
    return dict(_CACHE) # Returns a shallow copy
