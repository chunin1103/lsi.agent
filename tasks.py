# tasks.py
"""
Background asynchronous tasks for keyword generation.
Imports specific functions from service modules directly.
"""
import asyncio
import logging
from collections import deque
from typing import List, Set, Deque, Tuple, Any 

import config # Direct import, should be found due to sys.path in main.py
# Import specific functions from service modules
from services.suggest_service import fetch_google_autosuggest
from services.planner_service import fetch_keyword_planner_ideas
from services.cache_service import update_cache 
# If other cache_service functions are needed directly in this file, import them too.
# e.g., from services.cache_service import update_cache, is_cache_fresh 

# Initialize logger for this module
log = logging.getLogger(__name__)


async def crawl_suggest_task(
    seed_keyword: str,
    output_queue: asyncio.Queue | None,
):
    """
    Asynchronously crawls Google Autosuggest in a breadth-first manner.
    Puts discovered keywords onto the output_queue and updates the cache.
    """
    log.info("SUGGEST_TASK_START: Seed='%s'%s", seed_keyword, " (for stream)" if output_queue else " (for cache only)")
    
    visited_queries: Set[str] = set()
    bfs_queue: Deque[Tuple[str, int]] = deque([(seed_keyword, 0)])
    keywords_collected_this_run: List[str] = [] 

    try:
        while bfs_queue:
            current_query, current_depth = bfs_queue.popleft()

            if current_query in visited_queries:
                continue
            visited_queries.add(current_query)

            suggestions: List[str] = []
            try:
                # Call the directly imported function
                suggestions = await fetch_google_autosuggest(current_query)
            except Exception as e: 
                log.warning("SUGGEST_TASK_FETCH_FAIL: Query='%s', Error='%s'", current_query, e)
            
            newly_discovered_keywords: List[str] = []
            for s_keyword in suggestions:
                if s_keyword not in keywords_collected_this_run:
                    newly_discovered_keywords.append(s_keyword)
            
            if newly_discovered_keywords:
                keywords_collected_this_run.extend(newly_discovered_keywords)
                # Call the directly imported function
                await update_cache(seed_keyword, newly_discovered_keywords, "suggest")
                
                if output_queue:
                    for kw_to_send in newly_discovered_keywords:
                        await output_queue.put(kw_to_send)
            
            if current_depth < config.SUGGEST_MAX_DEPTH: # Uses imported config
                for child_suggestion in suggestions:
                    if child_suggestion not in visited_queries:
                        bfs_queue.append((child_suggestion, current_depth + 1))
            
            await asyncio.sleep(config.SUGGEST_POLITE_SLEEP_SECONDS) # Uses imported config

    except Exception as e:
        log.error("SUGGEST_TASK_UNHANDLED_ERROR: Seed='%s', Error='%s'", seed_keyword, e, exc_info=True)
        if output_queue: 
            await output_queue.put({"error": "Suggest crawler task failed", "detail": str(e)})
    finally:
        log.info(
            "SUGGEST_TASK_DONE: Seed='%s', Collected_this_run=%d",
            seed_keyword, len(keywords_collected_this_run)
        )
        if output_queue:
            await output_queue.put(None) 

async def planner_keywords_task(
    seed_keyword: str,
    output_queue: asyncio.Queue | None,
):
    """
    Asynchronously fetches keywords from Google Ads Planner and updates cache/queue.
    """
    log.info("PLANNER_TASK_START: Seed='%s'%s", seed_keyword, " (for stream)" if output_queue else " (for cache only)")
    
    try:
        # Call the directly imported function
        planner_keywords_list: List[str] = await asyncio.to_thread(
            fetch_keyword_planner_ideas, # Directly imported function
            seed_keyword,
            config.DEFAULT_KEYWORD_LIMIT // 2 # Uses imported config
        )
        
        if planner_keywords_list:
            # Call the directly imported function
            await update_cache(seed_keyword, planner_keywords_list, "planner")
            
            if output_queue:
                for kw_to_send in planner_keywords_list:
                    await output_queue.put(kw_to_send)
    except Exception as e:
        log.error("PLANNER_TASK_UNHANDLED_ERROR: Seed='%s', Error='%s'", seed_keyword, e, exc_info=True)
        if output_queue: 
            await output_queue.put({"error": "Planner task failed", "detail": str(e)})
    finally:
        log.info("PLANNER_TASK_DONE: Seed='%s'", seed_keyword)
        if output_queue:
            await output_queue.put(None)
