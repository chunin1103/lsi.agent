# test_sse_stream.py
"""
Standalone script to test the SSE keyword streaming endpoint (GET /keywords/stream).
Place this file in your project root (e.g., C:\LSI Agent).

Run from your project root with your virtual environment activated:
python test_sse_stream.py --seed "your keyword" --planner True
"""
import requests
import json
import logging
import sys
import os
import argparse # For command-line arguments

# --- Add project root to sys.path for consistent imports ---
PROJECT_ROOT = os.path.abspath(os.path.dirname(__file__))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)
# --- End sys.path modification ---

try:
    import config as app_config # To get API_BASE if needed, though we'll use a default here
except ImportError:
    print("Warning: Could not import config.py. Using default API_BASE.")
    class MockAppConfig: # Define a mock if config.py is not found for some reason
        API_BASE = "http://127.0.0.1:8000" # Default if config.py is missing
    app_config = MockAppConfig()


# --- Basic Logging Setup for this test script ---
test_log_level = "INFO" # Set to "DEBUG" for more verbosity from this script
logging.basicConfig(
    level=test_log_level,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
    stream=sys.stdout # Log to console
)
# Quieten requests library's own INFO logs if desired
logging.getLogger("requests").setLevel(logging.WARNING)
logging.getLogger("urllib3").setLevel(logging.WARNING)
# --- End Logging Setup ---

log = logging.getLogger("test_sse_stream")

API_BASE_URL = getattr(app_config, 'API_BASE', "http://127.0.0.1:8000") # Get from config or default
TIMEOUT_CONNECT = 10  # Seconds to wait for initial connection
TIMEOUT_READ_STREAM = 60 * 5 # Seconds to wait for data chunks on the stream (5 minutes total for long streams)

def parse_sse_line_for_test(line: str) -> tuple[str | None, str | None]:
    """
    Simplified SSE line parser for this test script.
    Returns (field, value).
    """
    if not line or line.startswith(':'):
        return None, None
    parts = line.split(":", 1)
    if len(parts) == 2:
        field = parts[0].strip()
        value = parts[1].strip()
        if field in ["id", "event", "data", "retry"]: # Standard SSE fields
            return field, value
    return None, None # Ignore malformed lines for this test

def consume_sse_stream(seed_keyword: str, use_planner: bool):
    """
    Connects to the SSE endpoint and prints received events.
    """
    sse_url = f"{API_BASE_URL}/keywords/stream"
    params = {"seed": seed_keyword, "planner": str(use_planner).lower()} # Ensure planner is string "true" or "false"

    log.info("--- Starting SSE Stream Test ---")
    log.info(f"Connecting to: {sse_url} with params: {params}")

    current_event_data_lines = []
    current_event_type = None
    current_event_id = None

    try:
        with requests.get(sse_url, params=params, stream=True, timeout=(TIMEOUT_CONNECT, TIMEOUT_READ_STREAM)) as response:
            response.raise_for_status() # Check for initial HTTP errors (4xx, 5xx)
            log.info("Successfully connected to SSE stream. Waiting for events...")

            for line_bytes in response.iter_lines(): # iter_lines handles chunk decoding
                if not line_bytes: # Keep-alive newlines or end of chunk
                    continue

                line = line_bytes.decode('utf-8').strip()

                if not line: # An empty line signifies the end of an event block
                    if current_event_type and current_event_data_lines:
                        full_data = "".join(current_event_data_lines)
                        log.info("--- SSE Event Received ---")
                        if current_event_id:
                            log.info(f"  ID: {current_event_id}")
                        log.info(f"  Event: {current_event_type}")
                        log.info(f"  Data: {full_data}")
                        try:
                            # Attempt to parse data if it's supposed to be JSON
                            if current_event_type in ["keyword", "done", "error"]:
                                parsed_data = json.loads(full_data)
                                log.info(f"  Parsed Data: {parsed_data}")
                        except json.JSONDecodeError:
                            log.warning(f"  Could not parse data as JSON: {full_data}")
                        log.info("--------------------------")

                    # Reset for the next event
                    current_event_data_lines = []
                    current_event_type = None
                    # ID can persist if not sent with every event, but we'll reset for clarity here
                    # current_event_id = None 
                    continue

                field, value = parse_sse_line_for_test(line)

                if field == "id":
                    current_event_id = value
                elif field == "event":
                    current_event_type = value
                elif field == "data":
                    current_event_data_lines.append(value)
                elif field == "retry":
                    log.info(f"SSE server suggested retry timeout: {value} ms")
                elif field is None and line.startswith(':'): # Comment
                    log.debug(f"SSE Comment: {line}")
                else:
                    log.debug(f"SSE Raw Line (unparsed or non-data): {line}")
            
            log.info("Stream finished or iter_lines ended.")

    except requests.exceptions.ReadTimeout:
        log.error("Read Timeout: The server did not send data in the expected time.")
    except requests.exceptions.HTTPError as e:
        log.error(f"HTTP Error: {e.response.status_code} - {e.response.text}")
    except requests.exceptions.ConnectionError as e:
        log.error(f"Connection Error: Could not connect to API at {API_BASE_URL}. Details: {e}")
    except Exception as e:
        log.error(f"An unexpected error occurred: {e}", exc_info=True)
    finally:
        log.info("--- SSE Stream Test Finished ---")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Test the SSE keyword streaming endpoint.")
    parser.add_argument("--seed", type=str, required=True, help="The seed keyword for generation.")
    parser.add_argument(
        "--planner",
        type=lambda x: (str(x).lower() == 'true'), # Convert "true"/"false" string to bool
        default=True,
        help="Whether to include Google Ads Planner results (True/False). Default: True."
    )
    args = parser.parse_args()

    consume_sse_stream(seed_keyword=args.seed, use_planner=args.planner)
