# app.py (Refactored Streamlit Client with Progressive Clustering UI)
import streamlit as st
import pandas as pd
import pathlib
import time
import queue # Required for queue.Empty and creating the queue
import threading # For threading.Lock (though not used in this version for queue)
from typing import List, Optional, Dict, Any

# ── Streamlit session state initialization MOVED TO VERY TOP ─────────────────
if "total_keywords_generated" not in st.session_state: st.session_state.total_keywords_generated = 0
if "total_topics_clustered" not in st.session_state: st.session_state.total_topics_clustered = 0
if "active_clustering_tasks" not in st.session_state: st.session_state.active_clustering_tasks = 0
if "all_kw" not in st.session_state: st.session_state.all_kw = []
if "stream_active" not in st.session_state: st.session_state.stream_active = False
if "last_seed" not in st.session_state: st.session_state.last_seed = ""
if "last_planner_option" not in st.session_state: st.session_state.last_planner_option = True
if "topic_clusters" not in st.session_state: 
    st.session_state.topic_clusters = []
    print("APP.PY (VERY VERY TOP): 'topic_clusters' initialized in st.session_state.")
if "existing_topic_names_session" not in st.session_state: st.session_state.existing_topic_names_session = []
if "last_cluster_error_from_queue" not in st.session_state: st.session_state.last_cluster_error_from_queue = None
if "last_stream_error" not in st.session_state: st.session_state.last_stream_error = None
# REMOVED: generate_button_clicked_flag initialization
# --- END OF MOVED INITIALIZATION ---

# ADDED: Print session state at the very top of the script execution
print(f"APP.PY (VERY TOP OF SCRIPT RUN): Initial st.session_state.topic_clusters length: {len(st.session_state.get('topic_clusters', [])) if 'topic_clusters' in st.session_state else 'Not in session yet'}") 
print(f"APP.PY (VERY TOP OF SCRIPT RUN): Initial st.session_state.active_clustering_tasks: {st.session_state.get('active_clustering_tasks', 'Not in session yet')}")


# ── Page & global config ───────────────────────────────────────────────────
st.set_page_config(page_title="Keyword Burst Buddy", layout="wide")

@st.cache_resource
def get_results_queue_instance():
    print("APP.PY (get_results_queue_instance): Creating new/cached results_queue instance.")
    return queue.Queue()

RESULTS_QUEUE_APP_INSTANCE = get_results_queue_instance()
print(f"APP.PY (Top Level): Using CACHED RESULTS_QUEUE_APP_INSTANCE with ID: {id(RESULTS_QUEUE_APP_INSTANCE)}")


# Import functions from api_client.py
try:
    from api_client import (
        get_api_status,
        stream_sse_keywords,
        LLMConfigClientInput,
        cluster_batch_queue, # For clearing
        MAX_CONCURRENT_CLUSTER_THREADS
    )
except ImportError:
    st.error("Failed to import from api_client.py. Make sure it's in the same directory or Python path.")
    def get_api_status(seed_kw, planner_active): return {"total_cached_keywords":0, "error":"API Client not loaded"}
    def stream_sse_keywords(*args, **kwargs): st.error("API client (stream) not available."); return False
    class LLMConfigClientInput: pass
    cluster_batch_queue = queue.Queue()
    MAX_CONCURRENT_CLUSTER_THREADS = 2 # Fallback


def load_css(file_name):
    try:
        css_path = pathlib.Path(__file__).parent / "templates" / file_name
        if not css_path.is_file():
            css_path_cwd = pathlib.Path.cwd() / "templates" / file_name
            if css_path_cwd.is_file():
                css_path = css_path_cwd
            else:
                st.warning(f"CSS file '{file_name}' not found at '{css_path}' or '{css_path_cwd}'. Please ensure 'templates/{file_name}' exists.")
                return
        with open(css_path) as f:
            st.markdown(f"<style>{f.read()}</style>", unsafe_allow_html=True)
    except Exception as e:
        st.error(f"Error loading CSS '{file_name}': {e}")
load_css("style.css")

# ── Title & Global Summary Metrics ───────────────────────────────────────────
st.title("LSI Keyword Buddy")
st.caption("Generate and cluster SEO keywords")
header_cols = st.columns([3, 1, 1, 3])
with header_cols[0]:
    seed_input_val = st.text_input("Enter seed keyword", placeholder="e.g., nước mắm", key="seed_input_main", label_visibility="collapsed")
with header_cols[1]:
    start_button_val = st.button("Generate & Cluster", key="generate_button_main", use_container_width=True)
with header_cols[2]:
    total_keywords_metric_placeholder = st.empty()
with header_cols[3]:
    total_topics_metric_placeholder = st.empty()

cols_options = st.columns([1,1,5])
with cols_options[0]:
    use_planner_val = st.checkbox("Use Google Ads Planner", value=True, key="use_planner_checkbox_main")

# --- Function to process results from the RESULTS_QUEUE_APP_INSTANCE ---
def process_results_from_queue():
    if 'topic_clusters' not in st.session_state or not isinstance(st.session_state.topic_clusters, list):
        st.session_state.topic_clusters = [] 
        print(f"APP.PY (process_queue DEFENSIVE): 'topic_clusters' re-initialized to empty list.")

    print(f"APP.PY (process_queue ENTRY): Queue size: {RESULTS_QUEUE_APP_INSTANCE.qsize()}, Active tasks: {st.session_state.active_clustering_tasks}, Current topic_clusters length: {len(st.session_state.topic_clusters)}")

    processed_item_this_run = False
    new_clusters_processed_count = 0
    state_changed_by_processing = False
    initial_topic_cluster_length = len(st.session_state.topic_clusters)
    all_new_clusters_in_this_run = []
    all_new_topic_names_in_this_run = []

    try:
        while not RESULTS_QUEUE_APP_INSTANCE.empty():
            payload = RESULTS_QUEUE_APP_INSTANCE.get_nowait()
            print(f"APP.PY (process_queue): Received payload type: {type(payload)}, content: {str(payload)[:100]}")
            processed_item_this_run = True

            if payload.get("worker_finished"):
                st.session_state.active_clustering_tasks = max(0, st.session_state.active_clustering_tasks - 1)
                print(f"APP.PY (process_queue): Worker {payload.get('thread_name')} finished. Active tasks now: {st.session_state.active_clustering_tasks}")
                state_changed_by_processing = True
            elif "error" in payload:
                st.session_state.last_cluster_error_from_queue = payload["error"]
                print(f"APP.PY (process_queue): Error from worker {payload.get('thread_name')}: {payload['error']}")
                state_changed_by_processing = True
            elif "no_clusters_from_batch" in payload:
                print(f"APP.PY (process_queue): Worker {payload.get('thread_name')} reported no clusters for a batch.")
                state_changed_by_processing = True # Still a processing event
            else:
                new_clusters_batch = payload.get("new_clusters")
                if new_clusters_batch:
                    print(f"APP.PY (process_queue): Found {len(new_clusters_batch)} new clusters in payload.")
                    all_new_clusters_in_this_run.extend(new_clusters_batch)
                    for c_item in new_clusters_batch:
                        if 'existing_topic_names_session' not in st.session_state or not isinstance(st.session_state.existing_topic_names_session, list):
                            st.session_state.existing_topic_names_session = []
                        if c_item.topic_name not in st.session_state.existing_topic_names_session and \
                           c_item.topic_name not in all_new_topic_names_in_this_run:
                            all_new_topic_names_in_this_run.append(c_item.topic_name)
                    state_changed_by_processing = True
            RESULTS_QUEUE_APP_INSTANCE.task_done()

        if all_new_clusters_in_this_run:
            new_clusters_processed_count = len(all_new_clusters_in_this_run)
            print(f"APP.PY (process_queue): Extending st.session_state.topic_clusters (ID: {id(st.session_state.topic_clusters)}). Old length: {initial_topic_cluster_length}, New clusters: {new_clusters_processed_count}")
            st.session_state.topic_clusters.extend(all_new_clusters_in_this_run) 
            print(f"APP.PY (process_queue): Extended st.session_state.topic_clusters. Final length: {len(st.session_state.topic_clusters)}. ID after extend: {id(st.session_state.topic_clusters)}")

            if 'existing_topic_names_session' not in st.session_state or not isinstance(st.session_state.existing_topic_names_session, list):
                st.session_state.existing_topic_names_session = []
                print(f"APP.PY (process_queue DEFENSIVE): 'existing_topic_names_session' re-initialized.")
            current_names_set = set(st.session_state.existing_topic_names_session)
            added_count = 0
            for name in all_new_topic_names_in_this_run:
                if name not in current_names_set:
                    st.session_state.existing_topic_names_session.append(name)
                    current_names_set.add(name) 
                    added_count +=1
            if added_count > 0:
                 print(f"APP.PY (process_queue): Updated existing_topic_names_session. Added {added_count} names. Length now: {len(st.session_state.existing_topic_names_session)}")
        if new_clusters_processed_count > 0 : 
             print(f"APP.PY (process_queue): Processed {new_clusters_processed_count} new clusters from queue this run.")
    except queue.Empty:
        pass
    except Exception as e:
        import traceback
        print(f"APP.PY (process_queue): Exception while processing queue: {e}\n{traceback.format_exc()}")
        st.session_state.last_cluster_error_from_queue = f"App error processing results: {e}"
        state_changed_by_processing = True

    # Return True if any state relevant to UI refresh was changed, or if items were processed (to ensure loop continues if needed)
    if state_changed_by_processing or (processed_item_this_run and not RESULTS_QUEUE_APP_INSTANCE.empty()): # If items were processed and queue might still have more
        st.session_state.total_topics_clustered = len(st.session_state.topic_clusters)
        print(f"APP.PY (process_queue EXIT state_changed_or_more_items): Total topics: {st.session_state.total_topics_clustered}, Active tasks: {st.session_state.active_clustering_tasks}. Returning True.")
        return True
    else: 
        # If only processed_item_this_run is true but queue is now empty, and no state_changed_by_processing
        # it means the last item was processed. We might not need a rerun if this last item didn't change state.
        # However, to be safe and ensure the display updates after the last worker finishes, let's consider `processed_item_this_run`
        # as a signal for potential need to rerun, especially if active_clustering_tasks just went to 0.
        if processed_item_this_run: # An item was processed, even if it didn't change `topic_clusters` (e.g. last worker_finished)
             st.session_state.total_topics_clustered = len(st.session_state.topic_clusters) # Update metric
             print(f"APP.PY (process_queue EXIT last_item_processed): Total topics: {st.session_state.total_topics_clustered}, Active tasks: {st.session_state.active_clustering_tasks}. Returning True to ensure final UI update.")
             return True
        print(f"APP.PY (process_queue EXIT no_change_or_empty_queue): Current topic_clusters length: {len(st.session_state.topic_clusters)}. Returning False.")
        return False # No items processed or no state change that warrants a rerun from this call

buffer_was_processed = process_results_from_queue()
print(f"APP.PY (After process_queue): buffer_was_processed = {buffer_was_processed}, topic_clusters length = {len(st.session_state.get('topic_clusters', []))}, total_topics_clustered = {st.session_state.get('total_topics_clustered', 0)}")

total_keywords_metric_placeholder.metric(label="Total Keywords", value=st.session_state.total_keywords_generated)
total_topics_metric_placeholder.metric(label="Total Topics", value=st.session_state.total_topics_clustered)
print(f"APP.PY (After metric update): topic_clusters length = {len(st.session_state.get('topic_clusters', []))}, total_topics_clustered = {st.session_state.get('total_topics_clustered', 0)}")

if buffer_was_processed:
    print(f"APP.PY: Top-level queue processing returned True. Forcing st.rerun(). Current topic_clusters length: {len(st.session_state.get('topic_clusters', []))}")
    st.rerun()

# This block is reached ONLY if buffer_was_processed is False (queue empty and no state change from top-level process_results_from_queue)
print(f"APP.PY (Before SIMPLIFIED DISPLAY): buffer_was_processed = {buffer_was_processed}, topic_clusters length = {len(st.session_state.get('topic_clusters', []))}")
st.subheader("DEBUG: Raw Topic Clusters State")

if 'topic_clusters' in st.session_state and st.session_state.topic_clusters:
    st.success(f"DEBUG: st.session_state.topic_clusters has {len(st.session_state.topic_clusters)} items.")
    st.write("Raw st.session_state.topic_clusters (first 2 for brevity if long):")
    try:
        clusters_to_write = [c.__dict__ if hasattr(c, '__dict__') else str(c) for c in st.session_state.topic_clusters[:2]]
        st.json(clusters_to_write)
    except Exception as e:
        st.write(f"Error trying to display raw topic_clusters with st.json: {e}")
        st.write(st.session_state.topic_clusters[:2])
    for i, cluster_obj in enumerate(st.session_state.topic_clusters):
        st.write(f"--- DEBUG Cluster (from main app flow) {i+1} ---")
        st.write(f"Name: {getattr(cluster_obj, 'topic_name', 'N/A')}")
        st.write(f"Keywords: {getattr(cluster_obj, 'keywords', [])[:3]}")
        st.write("---")
else:
    st.info("DEBUG: st.session_state.topic_clusters is empty or not found.")

st.subheader("All Keywords")
keywords_summary_placeholder = st.empty()
keywords_table_placeholder = st.empty()
all_keywords_buttons_placeholder = st.empty()

sse_progress_bar_placeholder = st.empty()
cluster_status_text_placeholder = st.empty()

# MODIFIED: Using start_button_val directly
if start_button_val: 
    print("APP.PY: 'Generate & Cluster' button PRESSED.")
    current_seed = seed_input_val.strip()
    current_planner_option = use_planner_val
    if not current_seed:
        st.warning("Please enter a seed keyword first.")
    else:
        print(f"APP.PY (Button Logic): 'Generate & Cluster' process initiated. Seed: '{current_seed}', Planner: {current_planner_option}. Initial topic_clusters length before reset: {len(st.session_state.topic_clusters)}")
        st.session_state.all_kw = []
        st.session_state.topic_clusters = [] 
        print(f"APP.PY (Button Logic): topic_clusters RESET to []. Length now: {len(st.session_state.topic_clusters)}. ID: {id(st.session_state.topic_clusters)}")
        st.session_state.existing_topic_names_session = []
        st.session_state.total_keywords_generated = 0
        st.session_state.total_topics_clustered = 0
        st.session_state.active_clustering_tasks = MAX_CONCURRENT_CLUSTER_THREADS
        st.session_state.last_cluster_error_from_queue = None
        st.session_state.last_stream_error = None
        st.session_state.stream_active = False

        print(f"APP.PY (Button Logic): Clearing queues. Current topic_clusters length: {len(st.session_state.topic_clusters)}")
        while not RESULTS_QUEUE_APP_INSTANCE.empty():
            try: RESULTS_QUEUE_APP_INSTANCE.get_nowait(); RESULTS_QUEUE_APP_INSTANCE.task_done()
            except queue.Empty: break
        while not cluster_batch_queue.empty():
            try: cluster_batch_queue.get_nowait(); cluster_batch_queue.task_done()
            except queue.Empty: break

        total_keywords_metric_placeholder.metric(label="Total Keywords", value=0)
        total_topics_metric_placeholder.metric(label="Total Topics", value=0)
        keywords_table_placeholder.empty()
        keywords_summary_placeholder.empty()
        if hasattr(st.session_state, 'topic_clusters_display_container'):
            st.session_state.topic_clusters_display_container.empty()
        cluster_status_text_placeholder.info(f"Clustering in progress... ({st.session_state.active_clustering_tasks} worker(s) active)")

        st.session_state.last_seed = current_seed
        st.session_state.last_planner_option = current_planner_option
        current_order_top = True

        print(f"APP.PY (Button Logic): Calling stream_sse_keywords. Current topic_clusters length: {len(st.session_state.topic_clusters)}. ID: {id(st.session_state.topic_clusters)}")
        stream_successful = stream_sse_keywords(
            current_seed,
            current_planner_option,
            current_order_top,
            keywords_summary_placeholder,
            keywords_table_placeholder,
            sse_progress_bar_placeholder,
            st.session_state,
            RESULTS_QUEUE_APP_INSTANCE
        )
        print(f"APP.PY (Button Logic): stream_sse_keywords returned: {stream_successful}. Current topic_clusters length (before internal process_queue): {len(st.session_state.topic_clusters)}. ID: {id(st.session_state.topic_clusters)}")

        # Process items that might have been added during stream_sse_keywords
        # This ensures topic_clusters is updated with the first batch of results
        if process_results_from_queue():
             print(f"APP.PY (Button Logic): Queue processed after stream_sse_keywords. Current topic_clusters length: {len(st.session_state.topic_clusters)}. ID: {id(st.session_state.topic_clusters)}")
        
        if not stream_successful and not st.session_state.all_kw:
            st.error("Keyword streaming failed and no keywords were generated.")
            st.session_state.active_clustering_tasks = 0
        elif not st.session_state.all_kw and stream_successful :
             keywords_summary_placeholder.info(f"No keywords were generated from the stream for '{current_seed}'.")
        
        # Explicitly rerun after button logic is complete to trigger top-level queue processing
        print(f"APP.PY (Button Logic): End of button processing. Forcing st.rerun(). Final topic_clusters length in this run: {len(st.session_state.topic_clusters)}. ID: {id(st.session_state.topic_clusters)}")
        st.rerun()


active_tasks_display = st.session_state.get("active_clustering_tasks", 0)
if active_tasks_display > 0:
    cluster_status_text_placeholder.info(f"Clustering in progress... ({active_tasks_display} worker(s) active)")
elif not st.session_state.get("stream_active", False) and st.session_state.get("last_seed") :
    cluster_status_text_placeholder.empty()
    if len(st.session_state.all_kw) > 0 and \
       len(st.session_state.get("topic_clusters", [])) == 0 and \
       not st.session_state.get("last_cluster_error_from_queue"):
        cluster_status_text_placeholder.info("Clustering complete. No topics found.")

if st.session_state.all_kw:
    if not st.session_state.stream_active:
        keywords_summary_placeholder.markdown(f"**Total {len(st.session_state.all_kw):,} unique keywords fetched for '{st.session_state.last_seed}'.**")
    df_all_kws_display_final = pd.DataFrame({"Keyword": list(reversed(st.session_state.all_kw))})
    keywords_table_placeholder.dataframe(df_all_kws_display_final, use_container_width=True, hide_index=True, height=400)

    with all_keywords_buttons_placeholder.container():
        kw_button_cols = st.columns(2)
        with kw_button_cols[0]:
            if st.button("Prepare for Copy (All Kws)", key="copy_all_kws_button_main_v3", use_container_width=True):
                copy_all_kws_list_final = list(reversed(st.session_state.all_kw))
                joined_all_keywords_final = "\n".join(copy_all_kws_list_final)
                st.text_area("Copy all keywords:", joined_all_keywords_final, height=150, key="copy_all_kws_text_area_main_v3")
                st.info("All keywords ready in the text area above for you to copy.")
        with kw_button_cols[1]:
            csv_all_kws_list_final = list(reversed(st.session_state.all_kw))
            csv_all_bytes_final = ("\ufeff" + "\n".join(csv_all_kws_list_final)).encode("utf-8")
            st.download_button(
                "Export CSV (All Kws)", csv_all_bytes_final,
                file_name=f"{st.session_state.get('last_seed', 'keywords')}_all_lsi_keywords.csv",
                mime="text/csv;charset=utf-8", key="download_all_kws_csv_button_main_v3", use_container_width=True
            )
st.markdown(
    "<div class='footer-text'>⚡ LSI‑Agent API | Streamlit UI</div>",
    unsafe_allow_html=True,
)
