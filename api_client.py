# api_client.py
import requests
import streamlit as st # Still imported, but st.rerun is removed from worker
import json
import time
import pandas as pd
from typing import List, Optional, Dict, Any
import threading
import queue # For cluster_batch_queue and type hinting

# Import CLUSTER_KEYWORD_BATCH_SIZE from your project's config.py
try:
    import config as app_config
    CLUSTER_BATCH_SIZE = app_config.CLUSTER_KEYWORD_BATCH_SIZE
    MAX_CONCURRENT_CLUSTER_THREADS = getattr(app_config, 'MAX_CONCURRENT_CLUSTER_THREADS', 2)
except ImportError:
    print("API_CLIENT: Warning - Could not import app_config. Using defaults.")
    CLUSTER_BATCH_SIZE = 50
    MAX_CONCURRENT_CLUSTER_THREADS = 2
except AttributeError:
    print("API_CLIENT: Warning - Clustering config not found in app_config. Using defaults.")
    CLUSTER_BATCH_SIZE = 50
    MAX_CONCURRENT_CLUSTER_THREADS = 2

# This queue is for the main app to send keyword batches TO the workers
cluster_batch_queue = queue.Queue()
QUEUE_SENTINEL = object()


# Global configurations for the API client
API_BASE = "http://localhost:8000"
TIMEOUT_CONNECT = 10
TIMEOUT_READ_STREAM = 60
TIMEOUT_POST = 180

# --- Client-side Data Structures --- (No changes here)
class LLMConfigClientInput(object):
    llm_model: Optional[str]
    llm_provider: Optional[str]
    llm_temperature: Optional[float]
    def __init__(self, llm_model: Optional[str] = None, llm_provider: Optional[str] = None, llm_temperature: Optional[float] = None, **kwargs):
        self.llm_model = llm_model
        self.llm_provider = llm_provider
        self.llm_temperature = llm_temperature
        for key, value in kwargs.items(): setattr(self, key, value)

class TopicClusterClientOutput(object):
    topic_name: str
    keywords: List[str]
    def __init__(self, topic_name: str = "Unknown Topic", keywords: Optional[List[str]] = None, **kwargs):
        self.topic_name = topic_name
        self.keywords = keywords if keywords is not None else []
        for key, value in kwargs.items(): setattr(self, key, value)

class ClusteringMetadataClientOutput(object):
    total_keywords_processed_in_batch: int
    total_topics_found_in_batch: int
    llm_model_used: str
    def __init__(self, total_keywords_processed_in_batch: int = 0, total_topics_found_in_batch: int = 0, llm_model_used: str = "Unknown", **kwargs):
        self.total_keywords_processed_in_batch = total_keywords_processed_in_batch
        self.total_topics_found_in_batch = total_topics_found_in_batch
        self.llm_model_used = llm_model_used
        for key, value in kwargs.items(): setattr(self, key, value)

class TopicClusteringClientResponse(object):
    clusters: List[TopicClusterClientOutput]
    metadata: ClusteringMetadataClientOutput
    def __init__(self, clusters: Optional[List[TopicClusterClientOutput]] = None, metadata: Optional[ClusteringMetadataClientOutput] = None, **kwargs):
        self.clusters = clusters if clusters is not None else []
        self.metadata = metadata if metadata is not None else ClusteringMetadataClientOutput()
        for key, value in kwargs.items(): setattr(self, key, value)
# --- End Client-side Data Structures ---


def get_api_status(seed_kw: str, planner_active: bool) -> dict:
    try:
        r = requests.get(
            f"{API_BASE}/status",
            params={"seed": seed_kw, "planner": str(planner_active).lower()},
            timeout=TIMEOUT_CONNECT
        )
        r.raise_for_status()
        status_data = r.json()
        return status_data
    except requests.exceptions.RequestException as e:
        print(f"API_CLIENT: Error fetching API status: {e}")
        # Avoid calling st.error from non-main thread if this function were ever to be used by workers
        # However, get_api_status seems to be called by the main thread in stream_sse_keywords
        # For now, keeping st.error as it was, but be mindful if refactoring.
        st.error(f"Error fetching API status: {e}")
        return {
            "seed": seed_kw, "total_cached_keywords": 0,
            "suggest_task_complete": False, "planner_task_complete": not planner_active,
            "error": str(e)
        }

def parse_sse_line(line: str) -> tuple[str | None, str | None]:
    if not line or line.startswith(':'):
        return None, None
    parts = line.split(":", 1)
    if len(parts) == 2:
        field = parts[0].strip()
        value = parts[1].strip()
        if field == "data" and value.startswith("data:"):
            value = value[len("data:"):].strip()
        if field in ["id", "event", "data"]:
            return field, value
    return None, None

def call_cluster_api(
    keywords: List[str],
    existing_topic_names: Optional[List[str]] = None,
    llm_config_override: Optional[LLMConfigClientInput] = None
) -> Optional[TopicClusteringClientResponse]:
    # ... (no changes in this function's logic) ...
    cluster_url = f"{API_BASE}/topics/cluster"
    request_payload: Dict[str, Any] = {"keywords": keywords}
    if existing_topic_names is not None:
        request_payload["existing_topic_names"] = existing_topic_names
    if llm_config_override:
        config_dict = {}
        if getattr(llm_config_override, 'llm_model', None) is not None:
            config_dict["llm_model"] = llm_config_override.llm_model
        if getattr(llm_config_override, 'llm_provider', None) is not None:
            config_dict["llm_provider"] = llm_config_override.llm_provider
        if getattr(llm_config_override, 'llm_temperature', None) is not None:
            config_dict["llm_temperature"] = llm_config_override.llm_temperature
        if config_dict:
             request_payload["config"] = config_dict
    print(f"API_CLIENT (call_cluster_api): Sending POST to {cluster_url} with {len(keywords)} keywords.")
    try:
        response = requests.post(cluster_url, json=request_payload, timeout=TIMEOUT_POST)
        response.raise_for_status()
        response_data = response.json()
        if "clusters" in response_data and "metadata" in response_data:
            clusters_list = [TopicClusterClientOutput(**c_data) for c_data in response_data["clusters"]]
            metadata_obj = ClusteringMetadataClientOutput(**response_data["metadata"])
            client_response = TopicClusteringClientResponse(clusters=clusters_list, metadata=metadata_obj)
            print(f"API_CLIENT (call_cluster_api): Parsed response: Clusters={len(client_response.clusters)}")
            return client_response
        else:
            print("API_CLIENT (call_cluster_api): ERROR - Invalid response structure from clustering API.")
            return None
    except requests.exceptions.HTTPError as e:
        error_detail = "Unknown error"
        try:
            error_content = e.response.json(); error_detail = error_content.get("detail", str(error_content))
        except json.JSONDecodeError: error_detail = e.response.text
        print(f"API_CLIENT (call_cluster_api): HTTPError - {e.response.status_code} - {error_detail}")
        raise
    except requests.exceptions.RequestException as e:
        print(f"API_CLIENT (call_cluster_api): RequestException - {e}")
        raise
    except Exception as e:
        import traceback
        print(f"API_CLIENT (call_cluster_api): Unexpected Exception - {e}\n{traceback.format_exc()}")
        raise

# MODIFIED: Accepts results_queue_arg and st.rerun() calls REMOVED
def _cluster_worker_thread_target(results_queue_arg: queue.Queue, session_state_snapshot: dict):
    thread_name = threading.current_thread().name
    print(f"CLUSTER_WORKER ({thread_name}): Started at {time.time()}. Using results_queue ID: {id(results_queue_arg)}")

    item_processed_in_this_iteration = False
    try:
        while True:
            item_processed_in_this_iteration = False
            print(f"CLUSTER_WORKER ({thread_name}): Waiting for item from cluster_batch_queue at {time.time()}.")
            keyword_batch_to_process = cluster_batch_queue.get(block=True)

            if keyword_batch_to_process is QUEUE_SENTINEL:
                print(f"CLUSTER_WORKER ({thread_name}): Sentinel received at {time.time()}. Exiting.")
                cluster_batch_queue.task_done()
                results_queue_arg.put({"worker_finished": True, "thread_name": thread_name})
                print(f"CLUSTER_WORKER ({thread_name}): Put 'worker_finished' on queue.")
                # REMOVED: st.rerun() # Signal main app that this worker is done
                break

            print(f"CLUSTER_WORKER ({thread_name}): Processing batch of {len(keyword_batch_to_process)} keywords at {time.time()}.")

            clustering_response = None
            api_call_start_time = time.time()
            try:
                existing_names_for_api_call = session_state_snapshot.get("existing_topic_names_session", [])
                print(f"CLUSTER_WORKER ({thread_name}): Calling call_cluster_api with {len(keyword_batch_to_process)} keywords and {len(existing_names_for_api_call)} existing names from snapshot.")

                clustering_response = call_cluster_api(
                    keywords=keyword_batch_to_process,
                    existing_topic_names=existing_names_for_api_call,
                    llm_config_override=None
                )
                api_call_duration = time.time() - api_call_start_time
                print(f"CLUSTER_WORKER ({thread_name}): call_cluster_api returned in {api_call_duration:.2f}s.")
            except Exception as e:
                api_call_duration = time.time() - api_call_start_time
                print(f"CLUSTER_WORKER ({thread_name}): Error calling cluster API (took {api_call_duration:.2f}s): {e}")
                results_queue_arg.put({"error": str(e), "thread_name": thread_name})
                print(f"CLUSTER_WORKER ({thread_name}): Put 'error' on queue.")
                # REMOVED: st.rerun() # Signal main app about the error

            if clustering_response and clustering_response.clusters:
                print(f"CLUSTER_WORKER ({thread_name}): SUCCESS - Received {len(clustering_response.clusters)} clusters from API.")
                update_payload = {
                    "new_clusters": clustering_response.clusters,
                    "thread_name": thread_name
                }
                results_queue_arg.put(update_payload)
                print(f"CLUSTER_WORKER ({thread_name}): Put {len(clustering_response.clusters)} new clusters into results_queue_arg (ID: {id(results_queue_arg)}).")
                # REMOVED: st.rerun() # Signal main app that new data is available
            else:
                # This case includes when clustering_response is None (due to an error handled above)
                # or when API returns a valid response but with no clusters.
                if clustering_response is not None: # Only print if it wasn't an exception from call_cluster_api
                    print(f"CLUSTER_WORKER ({thread_name}): Clustering call for batch returned no clusters or failed. Response: {clustering_response}")
                results_queue_arg.put({"no_clusters_from_batch": True, "thread_name": thread_name})
                print(f"CLUSTER_WORKER ({thread_name}): Put 'no_clusters_from_batch' on queue.")
                # REMOVED: st.rerun()


            cluster_batch_queue.task_done()
            item_processed_in_this_iteration = True

    except queue.Empty:
        print(f"CLUSTER_WORKER ({thread_name}): cluster_batch_queue empty (unexpected with block=True) at {time.time()}.")
    except Exception as e:
        print(f"CLUSTER_WORKER ({thread_name}): Unhandled exception in worker loop at {time.time()}: {e}")
        results_queue_arg.put({"error": f"Unhandled worker error: {e}", "thread_name": thread_name})
        print(f"CLUSTER_WORKER ({thread_name}): Put 'unhandled error' on queue.")
        # REMOVED: st.rerun()
        if 'keyword_batch_to_process' in locals() and keyword_batch_to_process is not QUEUE_SENTINEL and not item_processed_in_this_iteration:
            try:
                cluster_batch_queue.task_done()
            except ValueError:
                pass
    finally:
        print(f"CLUSTER_WORKER ({thread_name}): Finished processing loop at {time.time()}.")


# MODIFIED: Accepts results_queue_arg
def stream_sse_keywords(
        seed_kw: str,
        use_planner: bool,
        order_top_input: bool,
        keywords_summary_placeholder,
        keywords_table_placeholder,
        sse_progress_bar_placeholder_arg,
        session_state,
        results_queue_arg: queue.Queue # Changed from results_lock
    ):
    session_state.stream_active = True

    while not cluster_batch_queue.empty():
        try:
            cluster_batch_queue.get_nowait()
            cluster_batch_queue.task_done()
        except queue.Empty:
            break

    worker_threads = []

    session_state_snapshot_for_workers = {
        "existing_topic_names_session": list(session_state.get("existing_topic_names_session", []))
    }
    print(f"API_CLIENT (stream_sse): Starting {MAX_CONCURRENT_CLUSTER_THREADS} worker threads. Passing results_queue ID: {id(results_queue_arg)}")
    for i in range(MAX_CONCURRENT_CLUSTER_THREADS):
        thread = threading.Thread(
            target=_cluster_worker_thread_target,
            args=(results_queue_arg, session_state_snapshot_for_workers.copy()),
            daemon=True
        )
        thread.start()
        worker_threads.append(thread)
        print(f"API_CLIENT: Started cluster worker thread {i+1}/{MAX_CONCURRENT_CLUSTER_THREADS}")

    sse_url = f"{API_BASE}/keywords/stream?seed={seed_kw}&planner={str(use_planner).lower()}"
    # ... (rest of stream_sse_keywords logic remains the same) ...
    keywords_received_count = 0
    new_keywords_for_batch: List[str] = []
    initial_status_checked = False
    estimated_total_keywords = 0
    stream_completed_normally = False
    sse_progress_bar = None

    print(f"API_CLIENT: Connecting to SSE URL: {sse_url}")
    try:
        sse_progress_bar = sse_progress_bar_placeholder_arg.progress(0.0, text="Initializing keyword stream...")

        with requests.get(sse_url, stream=True, timeout=(TIMEOUT_CONNECT, TIMEOUT_READ_STREAM)) as response:
            response.raise_for_status()
            print("API_CLIENT: SSE Connection successful.")
            current_event_type = None
            for line_bytes in response.iter_lines():
                if not session_state.stream_active:
                    print("API_CLIENT: Stream stopped by external state (session_state.stream_active is False).")
                    stream_completed_normally = False
                    break
                line = line_bytes.decode('utf-8').strip()
                if not line:
                    current_event_type = None
                    continue
                field, value = parse_sse_line(line)
                if field == "event":
                    current_event_type = value
                elif field == "id":
                    pass
                elif field == "data":
                    if current_event_type is None: continue
                    if value is None: continue
                    try:
                        data_payload = {}
                        if value: data_payload = json.loads(value)

                        if current_event_type == "keyword":
                            keyword = data_payload.get("keyword")
                            if keyword and keyword not in session_state.all_kw:
                                session_state.all_kw.append(keyword)
                                new_keywords_for_batch.append(keyword)
                                keywords_received_count += 1
                                session_state.total_keywords_generated = keywords_received_count

                                display_order_df = list(reversed(session_state.all_kw)) if order_top_input else session_state.all_kw
                                df_keywords_display = pd.DataFrame({"Keyword": display_order_df})
                                keywords_table_placeholder.dataframe(df_keywords_display, use_container_width=True, hide_index=True, height=400)
                                summary_text_val = f"**Fetched {keywords_received_count:,} keywords...**"
                                if estimated_total_keywords > 0:
                                    summary_text_val += f" (approx. {keywords_received_count/estimated_total_keywords*100:.0f}% of estimate)"
                                keywords_summary_placeholder.markdown(summary_text_val)

                                if sse_progress_bar:
                                    prog_val = min(keywords_received_count / estimated_total_keywords, 1.0) if estimated_total_keywords > 0 else (keywords_received_count / (keywords_received_count + 10.0))
                                    prog_text_ui = f"Streaming Keywords... {keywords_received_count}/{estimated_total_keywords if estimated_total_keywords > 0 else '?'}"
                                    sse_progress_bar.progress(prog_val, text=prog_text_ui)

                                if len(new_keywords_for_batch) >= CLUSTER_BATCH_SIZE:
                                    print(f"API_CLIENT: Batch size {CLUSTER_BATCH_SIZE} reached. Adding {len(new_keywords_for_batch)} new keywords to cluster_batch_queue.")
                                    cluster_batch_queue.put(list(new_keywords_for_batch))
                                    new_keywords_for_batch.clear()
                        elif current_event_type == "done":
                            if sse_progress_bar: sse_progress_bar.progress(1.0, text="Keyword stream complete!")
                            stream_completed_normally = True
                            print(f"API_CLIENT (stream_sse): 'done' event. Total keywords: {keywords_received_count}")
                            if new_keywords_for_batch:
                                print(f"API_CLIENT: Adding final batch of {len(new_keywords_for_batch)} keywords to cluster_batch_queue.")
                                cluster_batch_queue.put(list(new_keywords_for_batch))
                                new_keywords_for_batch.clear()
                            keywords_summary_placeholder.markdown(f"**Total {keywords_received_count:,} unique keywords fetched.**")
                            if sse_progress_bar_placeholder_arg: sse_progress_bar_placeholder_arg.empty()
                            break
                        elif current_event_type == "error":
                            error_msg = data_payload.get('message', 'Unknown stream error')
                            error_detail_msg = data_payload.get('detail', 'N/A')
                            print(f"API_CLIENT (stream_sse): 'error' event. Msg: {error_msg}, Detail: {error_detail_msg}")
                            session_state.last_stream_error = f"Stream error: {error_msg} (Detail: {error_detail_msg})" # For app.py to display
                    except json.JSONDecodeError as json_err:
                        print(f"API_CLIENT (stream_sse): ERROR - JSON Decode Error for data value '{value}': {json_err}")
                        session_state.last_stream_error = f"JSON Decode Error: {json_err}"
                        continue
                if not initial_status_checked and keywords_received_count == 0:
                    status_data = get_api_status(seed_kw, use_planner)
                    estimated_total_keywords = status_data.get("total_cached_keywords", 0)
                    initial_status_checked = True
                    if sse_progress_bar:
                        prog_text_status_ui = f"Connected. Initial estimate: {estimated_total_keywords} keywords." if estimated_total_keywords > 0 else "Connected. Fetching keywords..."
                        sse_progress_bar.progress(0.0, text=prog_text_status_ui)
    except requests.exceptions.ReadTimeout as e:
        print(f"API_CLIENT (stream_sse): ReadTimeout - {e}")
        session_state.last_stream_error = f"Read Timeout: {e}"
        return False
    except requests.exceptions.HTTPError as e:
        print(f"API_CLIENT (stream_sse): HTTPError - {e}")
        session_state.last_stream_error = f"HTTP Error: {e.response.status_code if e.response else 'N/A'}"
        return False
    except requests.exceptions.ConnectionError as e:
        print(f"API_CLIENT (stream_sse): ConnectionError - {e}")
        session_state.last_stream_error = f"Connection Error: {e}"
        return False
    except Exception as e:
        import traceback
        print(f"API_CLIENT (stream_sse): Unexpected Exception - {e}\n{traceback.format_exc()}")
        session_state.last_stream_error = f"Unexpected streaming error: {e}"
        return False
    finally:
        session_state.stream_active = False
        print(f"API_CLIENT (stream_sse): Stream ended. Keywords: {keywords_received_count}. Completed normally: {stream_completed_normally}")

        print(f"API_CLIENT: Signaling {MAX_CONCURRENT_CLUSTER_THREADS} cluster worker threads to stop by adding sentinels to cluster_batch_queue.")
        for _ in range(MAX_CONCURRENT_CLUSTER_THREADS):
            cluster_batch_queue.put(QUEUE_SENTINEL)

        print(f"API_CLIENT: Worker threads signaled to stop. Main SSE processing function returning. Main app should monitor results_queue for worker_finished signals.")

        if not stream_completed_normally:
            if keywords_received_count > 0:
                 keywords_summary_placeholder.markdown(f"**Stream interrupted. Fetched {keywords_received_count:,} unique keywords.**")
            else:
                keywords_summary_placeholder.markdown(f"**No keywords found or stream interrupted for '{seed_kw}'. Check API server logs.**")

        if sse_progress_bar_placeholder_arg:
             sse_progress_bar_placeholder_arg.empty()
    return stream_completed_normally