# main.py
"""
Main application file for the LSI Keyword API.
Handles FastAPI app setup, endpoints, SSE streaming, and task orchestration.
"""
import sys
import os
import pathlib
import asyncio
import json
import logging
import logging.handlers # Added for RotatingFileHandler
import time 
from typing import AsyncGenerator, Dict, List, Any

from fastapi import FastAPI, Query, Request, HTTPException, Body 
from fastapi.responses import PlainTextResponse
from sse_starlette.sse import EventSourceResponse

# --- Add project root to sys.path ---
PROJECT_ROOT = os.path.abspath(os.path.dirname(__file__))
print(f"DEBUG: Initial PROJECT_ROOT = {PROJECT_ROOT}") 
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)
    print(f"DEBUG: PROJECT_ROOT '{PROJECT_ROOT}' was added to sys.path.") 
else:
    print(f"DEBUG: PROJECT_ROOT '{PROJECT_ROOT}' was already in sys.path.") 

print(f"DEBUG: Current sys.path = {sys.path}") 
# --- End sys.path modification ---

import config as app_config 
import schemas
from services import cache_service 
from services import llm_service 
from tasks import crawl_suggest_task, planner_keywords_task

# --- Logging Setup ---
# Get the root logger
root_logger = logging.getLogger()
# Set root logger level FIRST, this acts as a ceiling. 
# Handlers can have their own higher (less verbose) or equal level.
root_logger.setLevel(getattr(logging, app_config.LOG_FILE_LEVEL.upper(), logging.DEBUG) if app_config.ENABLE_FILE_LOGGING else getattr(logging, app_config.LOG_LEVEL.upper(), logging.INFO) )


# Console Handler
console_formatter = logging.Formatter(app_config.LOG_FORMAT, datefmt=app_config.LOG_DATE_FORMAT)
console_handler = logging.StreamHandler(sys.stdout)
console_handler.setFormatter(console_formatter)
# Set console handler level based on general app config for console
console_handler.setLevel(getattr(logging, app_config.LOG_LEVEL.upper(), logging.INFO))
# Remove existing handlers if any (especially important for Uvicorn reloader)
for handler in root_logger.handlers[:]:
    root_logger.removeHandler(handler)
root_logger.addHandler(console_handler)

# File Handler (Optional)
if app_config.ENABLE_FILE_LOGGING:
    file_formatter = logging.Formatter(app_config.LOG_FORMAT, datefmt=app_config.LOG_DATE_FORMAT)
    # Use RotatingFileHandler to manage log file size
    file_handler = logging.handlers.RotatingFileHandler(
        app_config.LOG_FILE_PATH,
        maxBytes=app_config.LOG_FILE_MAX_BYTES,
        backupCount=app_config.LOG_FILE_BACKUP_COUNT,
        encoding='utf-8'
    )
    file_handler.setFormatter(file_formatter)
    file_handler.setLevel(getattr(logging, app_config.LOG_FILE_LEVEL.upper(), logging.DEBUG))
    root_logger.addHandler(file_handler)
    logging.info("File logging enabled: %s at level %s", app_config.LOG_FILE_PATH, app_config.LOG_FILE_LEVEL)
else:
    logging.info("File logging is disabled via config.")


# Set specific log levels for noisy libraries after root and console handlers are set
logging.getLogger("httpx").setLevel(getattr(logging, app_config.LOG_LEVEL_HTTPX.upper(), logging.WARNING))
logging.getLogger("g4f").setLevel(getattr(logging, app_config.LOG_LEVEL_G4F.upper(), logging.INFO))
# Example: logging.getLogger("services.cache_service").setLevel(logging.INFO) # To override root for specific modules

# Logger for this main module
log = logging.getLogger(__name__) 
# --- End Logging Setup ---


# --- API Description ---
try:
    current_dir = pathlib.Path(__file__).parent
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
                log.debug( # Changed to DEBUG for less noise on successful producer finish
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
                log.debug("SSE_STREAM_KEYWORD: Event sent for seed '%s', Keyword='%s'", seed_keyword, item) # Changed to DEBUG
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


# --- New Topic Clustering Endpoint ---
@app.post(
    "/topics/cluster",
    response_model=schemas.TopicClusteringResponse,
    summary="Cluster a batch of keywords into SEO topics using an LLM",
    tags=["Topic Clustering"] 
)
async def cluster_keywords_endpoint(
    request_data: schemas.TopicClusterRequest = Body(...) 
):
    log.info(
        "ENDPOINT_CLUSTER_REQUEST: Received %d keywords for clustering. Existing topics provided: %s.",
        len(request_data.keywords), "Yes" if request_data.existing_topic_names else "No"
    )
    if request_data.config:
        log.info("ENDPOINT_CLUSTER_REQUEST: LLM config override provided: %s", request_data.config.model_dump(exclude_none=True))

    try:
        generated_clusters: List[schemas.TopicClusterOutput] = await llm_service.get_keyword_clusters_from_llm(
            keywords_batch=request_data.keywords,
            existing_topic_names=request_data.existing_topic_names,
            request_llm_config=request_data.config
        )

        actual_llm_model_used = app_config.DEFAULT_LLM_MODEL
        if request_data.config and request_data.config.llm_model:
            actual_llm_model_used = request_data.config.llm_model
        
        response_metadata = schemas.ClusteringMetadataOutput(
            total_keywords_processed_in_batch=len(request_data.keywords),
            total_topics_found_in_batch=len(generated_clusters),
            llm_model_used=actual_llm_model_used
        )

        log.info( 
            "ENDPOINT_CLUSTER_SUCCESS: Clustered %d keywords into %d topics. Model used: %s.",
            response_metadata.total_keywords_processed_in_batch,
            response_metadata.total_topics_found_in_batch,
            response_metadata.llm_model_used
        )
        
        return schemas.TopicClusteringResponse(
            clusters=generated_clusters,
            metadata=response_metadata
        )

    except ValueError as ve: 
        log.error("ENDPOINT_CLUSTER_VALIDATION_ERROR: Error processing LLM response for clustering. Error: %s", ve, exc_info=False) 
        raise HTTPException(
            status_code=422, 
            detail=f"LLM response processing error: {str(ve)}"
        )
    except RuntimeError as rte: 
        log.critical("ENDPOINT_CLUSTER_RUNTIME_ERROR: Critical runtime error during clustering. Error: %s", rte, exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=f"Clustering service critical error: {str(rte)}"
        )
    except Exception as e: 
        log.error("ENDPOINT_CLUSTER_FAIL: Failed to cluster keywords. Error: %s", e, exc_info=True)
        raise HTTPException(
            status_code=503, 
            detail=f"Keyword clustering failed due to an external service or internal error: {str(e)}"
        )
