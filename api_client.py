# api_client.py
import requests
import streamlit as st 
import json
import time 
import pandas as pd
from typing import List, Optional, Dict, Any, Callable 

# Import CLUSTER_KEYWORD_BATCH_SIZE from your project's config.py
try:
    import config as app_config 
    CLUSTER_BATCH_SIZE = app_config.CLUSTER_KEYWORD_BATCH_SIZE
except ImportError:
    print("API_CLIENT: Warning - Could not import app_config. Using default CLUSTER_BATCH_SIZE.")
    CLUSTER_BATCH_SIZE = 30 # Fallback if config.py is not found by this script directly
except AttributeError: # If CLUSTER_KEYWORD_BATCH_SIZE is not in config
    print("API_CLIENT: Warning - CLUSTER_KEYWORD_BATCH_SIZE not found in app_config. Using default.")
    CLUSTER_BATCH_SIZE = 30


# Global configurations for the API client
API_BASE = "http://localhost:8000"  
TIMEOUT_CONNECT = 10  
TIMEOUT_READ_STREAM = 60 
TIMEOUT_POST = 180 

# --- Client-side Data Structures (mimicking Pydantic for type hints and structure) ---
class LLMConfigClientInput(object): 
    llm_model: Optional[str]
    llm_provider: Optional[str]
    llm_temperature: Optional[float]

    def __init__(self, llm_model: Optional[str] = None, llm_provider: Optional[str] = None, llm_temperature: Optional[float] = None, **kwargs):
        self.llm_model = llm_model
        self.llm_provider = llm_provider
        self.llm_temperature = llm_temperature
        # Allow for extra fields if Pydantic on server sends more, but client doesn't use them
        for key, value in kwargs.items():
            setattr(self, key, value)


class TopicClusterClientOutput(object):
    topic_name: str
    keywords: List[str]

    def __init__(self, topic_name: str = "Unknown Topic", keywords: Optional[List[str]] = None, **kwargs):
        self.topic_name = topic_name
        self.keywords = keywords if keywords is not None else []
        for key, value in kwargs.items():
            setattr(self, key, value)

class ClusteringMetadataClientOutput(object):
    total_keywords_processed_in_batch: int
    total_topics_found_in_batch: int
    llm_model_used: str

    def __init__(self, total_keywords_processed_in_batch: int = 0, total_topics_found_in_batch: int = 0, llm_model_used: str = "Unknown", **kwargs):
        self.total_keywords_processed_in_batch = total_keywords_processed_in_batch
        self.total_topics_found_in_batch = total_topics_found_in_batch
        self.llm_model_used = llm_model_used
        for key, value in kwargs.items():
            setattr(self, key, value)

class TopicClusteringClientResponse(object):
    clusters: List[TopicClusterClientOutput]
    metadata: ClusteringMetadataClientOutput

    def __init__(self, clusters: Optional[List[TopicClusterClientOutput]] = None, metadata: Optional[ClusteringMetadataClientOutput] = None, **kwargs):
        self.clusters = clusters if clusters is not None else []
        self.metadata = metadata if metadata is not None else ClusteringMetadataClientOutput()
        for key, value in kwargs.items():
            setattr(self, key, value)
# --- End Client-side Data Structures ---


def get_api_status(seed_kw: str, planner_active: bool) -> dict:
    print(f"API_CLIENT: Fetching status for seed='{seed_kw}', planner={planner_active}")
    try:
        r = requests.get(
            f"{API_BASE}/status",
            params={"seed": seed_kw, "planner": str(planner_active).lower()}, 
            timeout=TIMEOUT_CONNECT 
        )
        r.raise_for_status()  
        status_data = r.json()
        print(f"API_CLIENT: Status received: {status_data}")
        return status_data
    except requests.exceptions.RequestException as e:
        st.error(f"Error fetching API status: {e}") 
        print(f"API_CLIENT: Error fetching API status: {e}")
        return {
            "seed": seed_kw, "total_cached_keywords": 0,
            "suggest_task_complete": False, "planner_task_complete": not planner_active, 
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
    cluster_url = f"{API_BASE}/topics/cluster"
    request_payload: Dict[str, Any] = {"keywords": keywords}
    if existing_topic_names is not None: 
        request_payload["existing_topic_names"] = existing_topic_names
    if llm_config_override:
        config_dict = {}
        # Use vars() to get attributes if they are set, or default to None
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
        print(f"API_CLIENT (call_cluster_api): Response received.")
        
        if "clusters" in response_data and "metadata" in response_data:
            # Instantiate client-side classes using **kwargs
            clusters_list = [TopicClusterClientOutput(**c_data) for c_data in response_data["clusters"]]
            metadata_obj = ClusteringMetadataClientOutput(**response_data["metadata"])
            
            client_response = TopicClusteringClientResponse(clusters=clusters_list, metadata=metadata_obj)
            
            print(f"API_CLIENT (call_cluster_api): Parsed response: Clusters={len(client_response.clusters)}")
            return client_response
        else:
            print("API_CLIENT (call_cluster_api): ERROR - Invalid response structure from clustering API.")
            st.error("Clustering API response is missing expected 'clusters' or 'metadata' fields.")
            return None
    except requests.exceptions.HTTPError as e:
        error_detail = "Unknown error"
        try:
            error_content = e.response.json(); error_detail = error_content.get("detail", str(error_content))
        except json.JSONDecodeError: error_detail = e.response.text
        print(f"API_CLIENT (call_cluster_api): HTTPError - {e.response.status_code} - {error_detail}")
        st.error(f"Clustering API Error ({e.response.status_code}): {error_detail}") 
        return None
    except requests.exceptions.RequestException as e:
        print(f"API_CLIENT (call_cluster_api): RequestException - {e}")
        st.error(f"Error calling clustering API: {e}")
        return None
    except Exception as e:
        import traceback
        print(f"API_CLIENT (call_cluster_api): Unexpected Exception - {e}\n{traceback.format_exc()}")
        st.error(f"An unexpected error occurred while calling clustering API: {e}")
        return None

def stream_sse_keywords(
        seed_kw: str, 
        use_planner: bool, 
        order_top_input: bool, 
        keywords_summary_placeholder, 
        keywords_table_placeholder,   
        sse_progress_bar_placeholder_arg, 
        cluster_progress_bar_placeholder, 
        session_state      
    ):
    session_state.all_kw = [] 
    session_state.stream_active = True 
    if "topic_clusters" not in session_state: session_state.topic_clusters = [] 
    
    sse_url = f"{API_BASE}/keywords/stream?seed={seed_kw}&planner={str(use_planner).lower()}"
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
                    print("API_CLIENT: Stream stopped by external state.")
                    return False 
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
                                
                                display_order = list(reversed(session_state.all_kw)) if order_top_input else session_state.all_kw
                                df = pd.DataFrame({"Keyword": display_order}) 
                                keywords_table_placeholder.dataframe(df, use_container_width=True, hide_index=True, height=400)
                                summary_text = f"**Fetched {keywords_received_count:,} keywords...**"
                                if estimated_total_keywords > 0:
                                    summary_text += f" (approx. {keywords_received_count/estimated_total_keywords*100:.0f}% of estimate)"
                                keywords_summary_placeholder.markdown(summary_text)
                                session_state.total_keywords_generated = keywords_received_count

                                if sse_progress_bar:
                                    prog_val = min(keywords_received_count / estimated_total_keywords, 1.0) if estimated_total_keywords > 0 else (keywords_received_count / (keywords_received_count + 10.0)) 
                                    prog_text_ui = f"Streaming Keywords... {keywords_received_count}/{estimated_total_keywords if estimated_total_keywords > 0 else '?'}"
                                    sse_progress_bar.progress(prog_val, text=prog_text_ui)

                                if len(new_keywords_for_batch) >= CLUSTER_BATCH_SIZE:
                                    print(f"API_CLIENT: Batch size {CLUSTER_BATCH_SIZE} reached. Triggering clustering for {len(new_keywords_for_batch)} new keywords.")
                                    cluster_progress = None 
                                    with cluster_progress_bar_placeholder: 
                                        cluster_progress = st.progress(0.1, text=f"Clustering batch ({len(new_keywords_for_batch)})...")
                                    
                                    clustering_response = call_cluster_api(
                                        keywords=list(new_keywords_for_batch), 
                                        existing_topic_names=session_state.existing_topic_names_session,
                                        llm_config_override=None 
                                    )
                                    if cluster_progress: cluster_progress.progress(0.8, text="Processing cluster results...")
                                    if clustering_response and clustering_response.clusters:
                                        session_state.topic_clusters.extend(clustering_response.clusters)
                                        for c_item in clustering_response.clusters: 
                                            if c_item.topic_name not in session_state.existing_topic_names_session:
                                                session_state.existing_topic_names_session.append(c_item.topic_name)
                                        session_state.total_topics_clustered = len(session_state.topic_clusters)
                                    new_keywords_for_batch.clear() 
                                    if cluster_progress: cluster_progress_bar_placeholder.empty() 

                        elif current_event_type == "done":
                            if sse_progress_bar: sse_progress_bar.progress(1.0, text="Keyword stream complete!")
                            stream_completed_normally = True
                            print(f"API_CLIENT (stream_sse): 'done' event. Total keywords: {keywords_received_count}")
                            if new_keywords_for_batch: 
                                print(f"API_CLIENT: Processing final batch of {len(new_keywords_for_batch)} keywords for clustering.")
                                cluster_progress = None 
                                with cluster_progress_bar_placeholder:
                                     cluster_progress = st.progress(0.1, text=f"Clustering final batch ({len(new_keywords_for_batch)})...")
                                clustering_response = call_cluster_api(
                                    keywords=list(new_keywords_for_batch),
                                    existing_topic_names=session_state.existing_topic_names_session,
                                    llm_config_override=None
                                )
                                if cluster_progress: cluster_progress.progress(0.8, text="Processing final cluster results...")
                                if clustering_response and clustering_response.clusters:
                                    session_state.topic_clusters.extend(clustering_response.clusters)
                                    for c_item_final in clustering_response.clusters: 
                                        if c_item_final.topic_name not in session_state.existing_topic_names_session:
                                             session_state.existing_topic_names_session.append(c_item_final.topic_name)
                                    session_state.total_topics_clustered = len(session_state.topic_clusters)
                                new_keywords_for_batch.clear()
                                if cluster_progress: cluster_progress_bar_placeholder.empty()
                            
                            keywords_summary_placeholder.markdown(f"**Total {keywords_received_count:,} unique keywords fetched.**")
                            time.sleep(0.5) 
                            if sse_progress_bar_placeholder_arg: sse_progress_bar_placeholder_arg.empty() 
                            break 
                        
                        elif current_event_type == "error":
                            print(f"API_CLIENT (stream_sse): 'error' event. Msg: {data_payload.get('message')}")
                            st.error(f"Stream error: {data_payload.get('message', 'Unknown error')} (Detail: {data_payload.get('detail', 'N/A')})")
                    except json.JSONDecodeError as json_err:
                        print(f"API_CLIENT (stream_sse): ERROR - JSON Decode Error for data value '{value}': {json_err}")
                        st.error(f"JSON Decode Error for data value '{value}': {json_err}")
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
        st.error(f"Read Timeout: The server did not send data in time. Details: {e}")
        return False
    except requests.exceptions.HTTPError as e:
        print(f"API_CLIENT (stream_sse): HTTPError - {e}")
        st.error(f"HTTP Error connecting to stream: {e.response.status_code} - {e.response.text if e.response else 'No response text'}")
        return False
    except requests.exceptions.ConnectionError as e: 
        print(f"API_CLIENT (stream_sse): ConnectionError - {e}")
        st.error(f"Connection Error: Could not connect or stream from API. Details: {e}")
        return False
    except Exception as e: 
        import traceback
        print(f"API_CLIENT (stream_sse): Unexpected Exception - {e}\n{traceback.format_exc()}")
        st.error(f"An unexpected error occurred during streaming: {e}")
        return False
    finally:
        session_state.stream_active = False 
        print(f"API_CLIENT (stream_sse): Stream ended. Keywords: {keywords_received_count}. Completed normally: {stream_completed_normally}")
        if not stream_completed_normally:
            if keywords_received_count > 0:
                 keywords_summary_placeholder.markdown(f"**Stream interrupted. Fetched {keywords_received_count:,} unique keywords.**")
            else: 
                keywords_summary_placeholder.markdown(f"**No keywords found or stream interrupted for '{seed_kw}'. Check API server logs.**")
        
        if sse_progress_bar_placeholder_arg: # Check if the placeholder itself was passed
             sse_progress_bar_placeholder_arg.empty()
        if cluster_progress_bar_placeholder:
            cluster_progress_bar_placeholder.empty() 
    return stream_completed_normally
