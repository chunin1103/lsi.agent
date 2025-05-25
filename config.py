# config.py
"""
Configuration constants for the LSI Keyword API.
"""
import pathlib
import logging 

# API Metadata
API_TITLE = "LSI Keyword API (SSE - Refactored)"
API_VERSION = "2.3.0" 
OPENAPI_DESC_PATH = pathlib.Path("api_swagger.md") 

# Keyword Generation Limits & Behavior
DEFAULT_KEYWORD_LIMIT = 100000  
SUGGEST_MAX_DEPTH = 1           
SUGGEST_POLITE_SLEEP_SECONDS = 1 

# Cache Configuration
CACHE_TTL_SECONDS = 3600        

# Google Ads Configuration
GOOGLE_ADS_CONFIG_PATH = "./google-ads.yaml" 

# Logging Configuration
LOG_LEVEL = "INFO" 
LOG_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s" 
LOG_DATE_FORMAT = "%H:%M:%S"
LOG_LEVEL_HTTPX = "WARNING" 
LOG_LEVEL_G4F = "INFO" 
ENABLE_FILE_LOGGING = True 
LOG_FILE_PATH = "lsi_api.log" 
LOG_FILE_LEVEL = "DEBUG" 
LOG_FILE_MAX_BYTES = 10 * 1024 * 1024  
LOG_FILE_BACKUP_COUNT = 5 

# SSE Configuration
SSE_QUEUE_TIMEOUT_SECONDS = 1.0 

# LLM Configuration for Topic Clustering
DEFAULT_LLM_MODEL = "gpt-4o"  
PREFERRED_LLM_PROVIDERS: list[str | None] = [
    "PollinationsAI",
    "Blackbox",
    "Liaobots",
    None 
]
DEFAULT_LLM_TEMPERATURE = 0.7
DEFAULT_LLM_TIMEOUT_SECONDS = 120 

# New: Batch size for progressive clustering
CLUSTER_KEYWORD_BATCH_SIZE = 50 # Number of new keywords to accumulate before triggering a cluster call
# --- End LLM Configuration ---

