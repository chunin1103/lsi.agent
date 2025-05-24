"""
Configuration constants for the LSI Keyword API.
"""
import pathlib

# API Metadata
API_TITLE = "LSI Keyword API (SSE - Refactored)"
API_VERSION = "2.3.0" # Updated version for refactoring
OPENAPI_DESC_PATH = pathlib.Path(__file__).parent / "api_swagger.md" 

# Keyword Generation Limits & Behavior
DEFAULT_KEYWORD_LIMIT = 100000  # Overall hard limit for keywords from all sources
SUGGEST_MAX_DEPTH = 1           # BFS depth for Google Autosuggest crawl
SUGGEST_POLITE_SLEEP_SECONDS = 1 # Delay between Google Autosuggest calls

# Cache Configuration
CACHE_TTL_SECONDS = 3600        # Cache freshness in seconds (1 hour)

# Google Ads Configuration
GOOGLE_ADS_CONFIG_PATH = "./google-ads.yaml" # Path to Google Ads API credentials

# Logging Configuration (Basic, can be expanded)
LOG_LEVEL = "INFO"
LOG_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s" # Added logger name
LOG_DATE_FORMAT = "%H:%M:%S"

# SSE Configuration
SSE_QUEUE_TIMEOUT_SECONDS = 1.0 # Timeout for queue.get() in SSE stream

# Development/Debug settings (optional)
# DEBUG_MODE = False
