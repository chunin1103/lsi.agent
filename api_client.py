# api_client.py
import requests
import streamlit as st
import json
import time # For potential delays if needed, though not currently used in stream
import pandas as pd # Added import for pd.DataFrame

# Global configurations for the API client
API_BASE = "http://localhost:8000"  # API base URL
TIMEOUT_CONNECT = 10  # Seconds to wait for initial connection
TIMEOUT_READ_STREAM = 60 # Seconds to wait for data chunks on the stream (increased)

def get_api_status(seed_kw: str, planner_active: bool) -> dict:
    """
    Fetches the current keyword generation status from the API's /status endpoint.

    Args:
        seed_kw: The seed keyword to check.
        planner_active: Boolean indicating if planner results are expected.

    Returns:
        A dictionary containing the status information or a default error structure.
    """
    print(f"API_CLIENT: Fetching status for seed='{seed_kw}', planner={planner_active}")
    try:
        r = requests.get(
            f"{API_BASE}/status",
            params={"seed": seed_kw, "planner": planner_active},
            timeout=TIMEOUT_CONNECT # Use connect timeout for this non-streaming request
        )
        r.raise_for_status()  # Raises an exception for 4XX/5XX errors
        status_data = r.json()
        print(f"API_CLIENT: Status received: {status_data}")
        return status_data
    except requests.exceptions.RequestException as e:
        # Log the error to Streamlit's UI and console
        st.error(f"Error fetching API status: {e}")
        print(f"API_CLIENT: Error fetching API status: {e}")
        # Return a default structure so the app can continue gracefully
        return {
            "seed": seed_kw,
            "total_cached_keywords": 0,
            "suggest_task_complete": False,
            "planner_task_complete": not planner_active, 
        }

def parse_sse_line(line: str) -> tuple[str | None, str | None]:
    """
    Parses a single line from an SSE stream.
    Returns a tuple (field, value) or (None, None) if the line is a comment or invalid.
    Handles potential "data: data: {...}" malformation by stripping the inner "data:".
    """
    if not line or line.startswith(':'):  # Empty line or comment
        return None, None
    
    parts = line.split(":", 1)
    if len(parts) == 2:
        field = parts[0].strip()
        value = parts[1].strip()

        if field == "data" and value.startswith("data:"):
            print(f"API_CLIENT (parse_sse_line): Inner 'data:' prefix found. Original value: '{value}'")
            value = value[len("data:"):].strip()
        
        if field in ["id", "event", "data"]: 
            return field, value
        else:
            print(f"API_CLIENT (parse_sse_line): WARNING - Non-standard SSE field: '{field}' in line: '{line}'")
            return None, None 
    else:
        field_only = line.strip()
        if field_only in ["id", "event", "data"]:
             print(f"API_CLIENT (parse_sse_line): WARNING - SSE field '{field_only}' received without value. Treating as empty.")
             return field_only, "" 
        
        print(f"API_CLIENT (parse_sse_line): WARNING - Malformed SSE line (no colon): '{line}'")
        return None, None


def stream_sse_keywords(
        seed_kw: str, 
        use_planner: bool, 
        order_top_input: bool, 
        summary_placeholder, 
        table_placeholder,   
        progress_bar,        
        session_state        
    ):
    """
    Connects to the SSE keyword stream, processes events, and updates Streamlit UI placeholders.
    """
    session_state.all_kw = [] 
    session_state.stream_active = True 

    sse_url = f"{API_BASE}/keywords/stream?seed={seed_kw}&planner={use_planner}"
    
    keywords_received_count = 0
    initial_status_checked = False
    estimated_total_keywords = 0
    stream_completed_normally = False

    print(f"API_CLIENT: Connecting to SSE URL: {sse_url}")

    try:
        # Using a tuple for timeout: (connect_timeout, read_timeout)
        # For streaming requests, read_timeout applies to the time between receiving bytes.
        with requests.get(sse_url, stream=True, timeout=(TIMEOUT_CONNECT, TIMEOUT_READ_STREAM)) as response:
            response.raise_for_status() 
            print("API_CLIENT: SSE Connection successful.")
            
            current_event_type = None 

            for line_bytes in response.iter_lines(): 
                if not session_state.stream_active: 
                    st.warning("Stream stopped by external state.")
                    print("API_CLIENT: Stream stopped by external state.")
                    break

                line = line_bytes.decode('utf-8').strip()
                print(f"API_CLIENT (stream_sse): RAW_LINE << '{line}'")

                if not line:  
                    print("API_CLIENT (stream_sse): SSE Event separator (empty line). Resetting current_event_type.")
                    current_event_type = None 
                    continue

                field, value = parse_sse_line(line)
                print(f"API_CLIENT (stream_sse): PARSED_FIELD='{field}', PARSED_VALUE='{value}'")

                if field == "event":
                    current_event_type = value
                    print(f"API_CLIENT (stream_sse): Event type set to '{current_event_type}'")
                elif field == "id":
                    pass
                elif field == "data":
                    print(f"API_CLIENT (stream_sse): Data field received. Current event type: '{current_event_type}'")
                    
                    if current_event_type is None:
                        print(f"API_CLIENT (stream_sse): WARNING - Data received ('{value}') but current_event_type is None. Skipping.")
                        continue 

                    if value is None: 
                        print(f"API_CLIENT (stream_sse): WARNING - Data field parsed but value is None.")
                        continue
                        
                    try:
                        data_payload = {} 
                        if value: 
                            data_payload = json.loads(value)
                        print(f"API_CLIENT (stream_sse): JSON_PAYLOAD = {data_payload}")

                        if current_event_type == "keyword":
                            keyword = data_payload.get("keyword")
                            print(f"API_CLIENT (stream_sse): 'keyword' event. Keyword: '{keyword}'")
                            if keyword and keyword not in session_state.all_kw:
                                session_state.all_kw.append(keyword)
                                keywords_received_count += 1
                                print(f"API_CLIENT (stream_sse): Added keyword '{keyword}'. Total unique: {keywords_received_count}")

                                display_order = list(reversed(session_state.all_kw)) if order_top_input else session_state.all_kw
                                df = pd.DataFrame({"Keyword": display_order}) # Ensure pd is imported
                                table_placeholder.dataframe(df, use_container_width=True, hide_index=True)
                                
                                summary_text = f"**Fetched {keywords_received_count:,} keywords...**"
                                if estimated_total_keywords > 0:
                                    summary_text += f" (approx. {keywords_received_count/estimated_total_keywords*100:.0f}% of initial estimate)"
                                summary_placeholder.markdown(summary_text)

                                prog_val = min(keywords_received_count / estimated_total_keywords, 1.0) if estimated_total_keywords > 0 else (keywords_received_count / (keywords_received_count + 10.0)) 
                                prog_text_ui = f"Processing... {keywords_received_count}/{estimated_total_keywords if estimated_total_keywords > 0 else '?'}"
                                progress_bar.progress(prog_val, text=prog_text_ui)
                            elif keyword:
                                print(f"API_CLIENT (stream_sse): Keyword '{keyword}' already in list or is None/empty.")

                        elif current_event_type == "done":
                            st.success(f"Stream complete: {data_payload.get('message', 'All tasks finished.')} (Seed: {data_payload.get('seed')})")
                            summary_placeholder.markdown(f"**Total {keywords_received_count:,} unique keywords fetched.**")
                            progress_bar.progress(1.0, text="Completed!")
                            print(f"API_CLIENT (stream_sse): 'done' event. Msg: {data_payload.get('message')}")
                            stream_completed_normally = True
                            break 

                        elif current_event_type == "error":
                            st.error(f"Stream error: {data_payload.get('message', 'Unknown error')} (Detail: {data_payload.get('detail', 'N/A')})")
                            print(f"API_CLIENT (stream_sse): 'error' event. Msg: {data_payload.get('message')}")
                        
                    except json.JSONDecodeError as json_err:
                        st.error(f"JSON Decode Error for data value '{value}': {json_err}")
                        print(f"API_CLIENT (stream_sse): ERROR - JSON Decode Error for data value '{value}': {json_err}")
                        continue 
                
                if not initial_status_checked and keywords_received_count == 0:
                    status_data = get_api_status(seed_kw, use_planner)
                    estimated_total_keywords = status_data.get("total_cached_keywords", 0)
                    initial_status_checked = True
                    prog_text_status_ui = f"Connected. Initial estimate: {estimated_total_keywords} keywords." if estimated_total_keywords > 0 else "Connected. Fetching keywords..."
                    progress_bar.progress(0.0, text=prog_text_status_ui)
                    print(f"API_CLIENT (stream_sse): Initial status check. Estimated keywords: {estimated_total_keywords}")

    except requests.exceptions.ReadTimeout as e: # Specifically catch ReadTimeout
        st.error(f"Read Timeout: The server did not send data in time. It might be busy or stuck. Details: {e}")
        print(f"API_CLIENT (stream_sse): ReadTimeout - {e}")
    except requests.exceptions.HTTPError as e:
        st.error(f"HTTP Error connecting to stream: {e.response.status_code} - {e.response.text if e.response else 'No response text'}")
        print(f"API_CLIENT (stream_sse): HTTPError - {e}")
    except requests.exceptions.ConnectionError as e: # This will catch the "Read timed out" from the pool
        st.error(f"Connection Error: Could not connect or stream from API at {API_BASE}. Is the server running and responsive? Details: {e}")
        print(f"API_CLIENT (stream_sse): ConnectionError - {e}")
    except Exception as e: 
        st.error(f"An unexpected error occurred during streaming: {e}")
        import traceback
        print(f"API_CLIENT (stream_sse): Unexpected Exception - {e}\n{traceback.format_exc()}")
    finally:
        session_state.stream_active = False 
        print(f"API_CLIENT (stream_sse): Stream ended. Keywords: {keywords_received_count}. Completed normally: {stream_completed_normally}")
        if not stream_completed_normally:
            if keywords_received_count > 0:
                 summary_placeholder.markdown(f"**Stream interrupted. Fetched {keywords_received_count:,} unique keywords.**")
            else: 
                summary_placeholder.markdown(f"**No keywords found or stream interrupted for '{seed_kw}'. Check API server logs.**")
            if progress_bar: progress_bar.empty() 
