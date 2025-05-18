# main.py
"""
Main application file for the LSI Keyword API.
Handles FastAPI app setup, endpoints, SSE streaming, and task orchestration.
"""
import sys
import os
import pathlib

# --- Add project root to sys.path ---
# This ensures that modules like 'config', 'schemas', 'services', 'tasks'
# can be imported directly when running 'uvicorn main:app' from the project root.
PROJECT_ROOT = os.path.abspath(os.path.dirname(__file__))
print(f"DEBUG: Initial PROJECT_ROOT = {PROJECT_ROOT}") # Debug print
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)
    print(f"DEBUG: PROJECT_ROOT '{PROJECT_ROOT}' was added to sys.path.") # Debug print
else:
    print(f"DEBUG: PROJECT_ROOT '{PROJECT_ROOT}' was already in sys.path.") # Debug print

print(f"DEBUG: Current sys.path = {sys.path}") # Debug print
# --- End sys.path modification ---

import asyncio
import json
import logging
import time 
from typing import AsyncGenerator, Dict, List, Any

from fastapi import FastAPI, Query, Request
from fastapi.responses import PlainTextResponse
from sse_starlette.sse import EventSourceResponse

# Import refactored modules - Should now be found due to sys.path modification
# The error occurs on the next line if 'schemas' is not found.
import config as app_config 
import schemas
from services import cache_service 
from tasks import crawl_suggest_task, planner_keywords_task

# --- Logging Setup ---
logging.basicConfig(
    level=getattr(logging, app_config.LOG_LEVEL.upper(), logging.INFO),
    format=app_config.LOG_FORMAT,
    datefmt=app_config.LOG_DATE_FORMAT,
)
log = logging.getLogger(__name__) 

# --- API Description ---
try:
    current_dir = pathlib.Path(__file__).parent
    # Assuming OPENAPI_DESC_PATH in config.py is just the filename e.g., "api_swagger.md"
    # and it's located in the same directory as main.py (PROJECT_ROOT)
    openapi_description_file = current_dir / app_config.OPENAPI_DESC_PATH.name 
    
    OPENAPI_DESCRIPTION = openapi_description_file.read_text(encoding="utf-8")
    log.info("Successfully loaded OpenAPI description from: %s", openapi_description_file)
except FileNotFoundError:
    log.warning("OpenAPI description file not found at expected location (%s). Using default description.", app_config.OPENAPI_DESC_PATH.name)
    OPENAPI_DESCRIPTION = "Vietnamese LSI keyword micro‑service (SSE enabled, Refactored)."
except Exception as e:
    log.error("Error loading OpenAPI description: %s. Using default.", e)
    OPENAPI_DESCRIPTION = "Vietnamese LSI keyword micro‑service (SSE enabled, Refactored)."


# --- Global State for Task Management ---
_TASK_STATUS: Dict[str, Dict[str, bool]] = {}
_TASK_STATUS_LOCK = asyncio.Lock() 

# --- FastAPI Application Instance ---
app = FastAPI(
    title=app_config.API_TITLE,
    version=app_config.API_VERSION,
    description=OPENAPI_DESCRIPTION,
)

# --- Helper for Task Orchestration (_ensure_tasks) ---
async def _ensure_background_tasks(seed_keyword: str, run_planner: bool):
    async with _TASK_STATUS_LOCK:
        current_status = _TASK_STATUS.get(seed_keyword)
        if not current_status or \
           (current_status.get("suggest_done", False) and \
            (not run_planner or current_status.get("planner_done", False))):
            log.info("ENSURE_TASKS: Resetting task status for seed '%s'.", seed_keyword)
            _TASK_STATUS[seed_keyword] = {
                "suggest_done": False,
                "planner_done": not run_planner 
            }
            current_status = _TASK_STATUS[seed_keyword]

    is_fresh = await cache_service.is_cache_fresh(seed_keyword)
    cached_entry = await cache_service.get_cache_entry(seed_keyword)

    if current_status and not current_status.get("suggest_done") and not is_fresh:
        log.info("ENSURE_TASKS: Suggest cache stale/missing for '%s'. Initiating background Suggest task.", seed_keyword)
        async with _TASK_STATUS_LOCK: _TASK_STATUS[seed_keyword]["suggest_done"] = False
        asyncio.create_task(crawl_suggest_task(seed_keyword, None)) 
    elif current_status and is_fresh and cached_entry and cached_entry.get("suggest_count", 0) > 0 and not current_status.get("suggest_done"):
        log.info("ENSURE_TASKS: Suggest cache fresh for '%s'. Marking as done.", seed_keyword)
        async with _TASK_STATUS_LOCK: _TASK_STATUS[seed_keyword]["suggest_done"] = True

    if current_status and run_planner and not current_status.get("planner_done"):
        needs_planner_run_for_cache = not is_fresh or (cached_entry and cached_entry.get("planner_count", 0) == 0)
        if needs_planner_run_for_cache:
            log.info("ENSURE_TASKS: Planner task needed for '%s'. Initiating background Planner task.", seed_keyword)
            async with _TASK_STATUS_LOCK: _TASK_STATUS[seed_keyword]["planner_done"] = False
            asyncio.create_task(planner_keywords_task(seed_keyword, None)) 
        elif is_fresh and cached_entry and cached_entry.get("planner_count", 0) > 0:
            log.info("ENSURE_TASKS: Planner cache fresh for '%s'. Marking as done.", seed_keyword)
            async with _TASK_STATUS_LOCK: _TASK_STATUS[seed_keyword]["planner_done"] = True


# --- SSE Event Stream Generator ---
async def sse_keyword_stream_generator(
    request: Request,
    seed_keyword: str,
    sse_output_queue: asyncio.Queue,
    expected_producers: int,
) -> AsyncGenerator[Dict[str, Any], None]:
    producers_finished_count = 0
    event_id_counter = 0
    log.info("SSE_STREAM_START: Seed='%s', ExpectedProducers=%d", seed_keyword, expected_producers)
    try:
        while True:
            if await request.is_disconnected():
                log.info("SSE_STREAM_DISCONNECT: Client disconnected for seed '%s'.", seed_keyword)
                break
            try:
                item = await asyncio.wait_for(sse_output_queue.get(), timeout=app_config.SSE_QUEUE_TIMEOUT_SECONDS)
                sse_output_queue.task_done()
            except asyncio.TimeoutError:
                continue

            event_id_counter += 1
            current_event_id = str(event_id_counter)

            if item is None:  
                producers_finished_count += 1
                log.debug(
                    "SSE_STREAM_PRODUCER_DONE: Seed='%s', Finished=%d/%d",
                    seed_keyword, producers_finished_count, expected_producers
                )
                if producers_finished_count >= expected_producers:
                    yield {
                        "id": current_event_id, "event": "done",
                        "data": json.dumps({
                            "seed": seed_keyword,
                            "message": "All keyword generation tasks complete."
                        })
                    }
                    log.info("SSE_STREAM_ALL_DONE: Event sent for seed '%s'.", seed_keyword)
                    break 
                continue
            elif isinstance(item, dict) and "error" in item: 
                yield {
                    "id": current_event_id, "event": "error",
                    "data": json.dumps(item)
                }
                log.warning(
                    "SSE_STREAM_PRODUCER_ERROR: Event sent for seed '%s', Detail: %s",
                    seed_keyword, item.get("detail")
                )
                producers_finished_count += 1 
                if producers_finished_count >= expected_producers:
                    log.info("SSE_STREAM_ALL_DONE_WITH_ERRORS: Seed='%s'.", seed_keyword)
                    break
                continue
            else:
                yield {
                    "id": current_event_id, "event": "keyword",
                    "data": json.dumps({"keyword": str(item)})
                }
                log.debug("SSE_STREAM_KEYWORD: Event sent for seed '%s', Keyword='%s'", seed_keyword, item)
    except asyncio.CancelledError:
        log.info("SSE_STREAM_CANCELLED: Seed='%s'. Client likely disconnected.", seed_keyword)
    except Exception as e:
        log.error("SSE_STREAM_UNEXPECTED_ERROR: Seed='%s', Error='%s'", seed_keyword, e, exc_info=True)
        if not await request.is_disconnected():
            try:
                yield {
                    "id": str(event_id_counter + 1), "event": "error",
                    "data": json.dumps({
                        "message": "An unexpected error occurred in the SSE stream.",
                        "detail": str(e)
                    })
                }
            except Exception as send_err:
                log.error("SSE_STREAM_FINAL_ERROR_SEND_FAIL: Seed='%s', Error='%s'", seed_keyword, send_err)
    finally:
        log.info("SSE_STREAM_END: Generator for seed '%s' concluding.", seed_keyword)

# --- API Endpoints ---
@app.get("/", response_class=PlainTextResponse, include_in_schema=False)
async def root_redirect():
    title = OPENAPI_DESCRIPTION.split("\n", 1)[0] if OPENAPI_DESCRIPTION else app_config.API_TITLE
    return f"{title}\nAPI Documentation available at: /docs or /redoc"

@app.get("/keywords/stream", summary="Real-time keyword stream using Server-Sent Events (SSE)")
async def stream_keywords_endpoint(
    request: Request, 
    seed: str = Query(..., min_length=1, description="Seed keyword for generation."),
    planner: bool = Query(True, description="Include Google Ads Planner results?"),
):
    log.info("ENDPOINT_STREAM_REQUEST: Seed='%s', Planner=%s", seed, planner)
    await _ensure_background_tasks(seed, planner)
    sse_specific_queue = asyncio.Queue()
    active_producers_for_this_stream = 0
    
    log.debug("ENDPOINT_STREAM_TASK_LAUNCH: Creating Suggest task for SSE stream '%s'", seed)
    asyncio.create_task(crawl_suggest_task(seed, sse_specific_queue))
    active_producers_for_this_stream += 1

    if planner:
        log.debug("ENDPOINT_STREAM_TASK_LAUNCH: Creating Planner task for SSE stream '%s'", seed)
        asyncio.create_task(planner_keywords_task(seed, sse_specific_queue))
        active_producers_for_this_stream += 1
    
    log.info(
        "ENDPOINT_STREAM_SETUP: SSE stream for '%s' expecting %d producers.",
        seed, active_producers_for_this_stream
    )
    return EventSourceResponse(
        sse_keyword_stream_generator(request, seed, sse_specific_queue, active_producers_for_this_stream),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache"} 
    )

@app.get("/status", response_model=schemas.CrawlStatus, summary="Check keyword generation status for a seed")
async def get_crawl_status_endpoint(
    seed: str = Query(..., min_length=1, description="Seed keyword to check status for."),
    planner: bool = Query(True, description="Is planner expected to run for this seed?"),
):
    log.debug("ENDPOINT_STATUS_REQUEST: Seed='%s', PlannerExpected=%s", seed, planner)
    await _ensure_background_tasks(seed, planner) 
    total_kws_in_cache = 0
    cached_data = await cache_service.get_cache_entry(seed)
    if cached_data:
        total_kws_in_cache = len(cached_data.get("kw", []))

    suggest_is_done = False
    planner_is_done = not planner 

    async with _TASK_STATUS_LOCK:
        status_entry = _TASK_STATUS.get(seed)
        if status_entry:
            suggest_is_done = status_entry.get("suggest_done", False)
            if planner: 
                planner_is_done = status_entry.get("planner_done", False)
    
    return schemas.CrawlStatus(
        seed=seed,
        total_cached_keywords=total_kws_in_cache,
        suggest_task_complete=suggest_is_done,
        planner_task_complete=planner_is_done,
    )

@app.get("/clear_cache", response_class=PlainTextResponse, summary="Flush all in-memory keyword caches and task statuses")
async def clear_cache_endpoint():
    await cache_service.clear_all_cache_entries()
    async with _TASK_STATUS_LOCK:
        _TASK_STATUS.clear()
    log.info("ENDPOINT_CLEAR_CACHE: Cache and task statuses have been cleared.")
    return "Cache and task statuses cleared successfully."

