# test_llm_service.py
"""
Standalone script to test the LLM topic clustering service.
Place this file in your project root (e.g., C:\LSI Agent).
Ensure g4ftemplate.py, config.py, schemas.py, and the services/ package are accessible.

Run from your project root with your virtual environment activated:
python test_llm_service.py
"""
import asyncio
import logging
import sys
import os

# --- Add project root to sys.path for consistent imports ---
PROJECT_ROOT = os.path.abspath(os.path.dirname(__file__))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)
# --- End sys.path modification ---

# Import necessary components from your project
try:
    from services import llm_service # From your services package
    from schemas import LLMConfigInput  # From your schemas.py
    import config as app_config # From your config.py
except ImportError as e:
    print(f"Error importing project modules: {e}")
    print("Please ensure this script is in the project root and all required files (config.py, schemas.py, services/llm_service.py, g4ftemplate.py) are present.")
    sys.exit(1)

# --- Basic Logging Setup for this test script ---
# This will show logs from llm_service and this script itself.
test_log_level = "DEBUG" # Set to "INFO" for less verbosity, "DEBUG" for more
logging.basicConfig(
    level=test_log_level,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
    stream=sys.stdout # Log to console
)
# You can also set specific levels for other loggers if they become noisy during test
logging.getLogger("g4f").setLevel(logging.INFO) # Example: Keep g4f a bit quieter
logging.getLogger("httpx").setLevel(logging.WARNING)
# --- End Logging Setup ---

log = logging.getLogger("test_script")

async def main_test():
    log.info("--- Starting LLM Clustering Service Test ---")

    # --- Sample Data for Testing ---
    sample_keywords_batch = [
        "vietnam travel destinations",
        "best beaches in vietnam",
        "hanoi street food",
        "saigon nightlife",
        "ha long bay cruise",
        "sapa trekking",
        "vietnamese coffee recipe",
        "how to make pho",
        "traditional vietnamese music"
    ]

    sample_existing_topics = [
        "Vietnam Tourist Attractions",
        "Vietnamese Cuisine Essentials"
    ]
    
    # Optional: Define specific LLM config for this test, or let it use defaults
    # If None, defaults from config.py and llm_service.py will be used.
    sample_llm_config_override = LLMConfigInput(
        llm_model="gpt-4o", # Example: try a different model for testing
        # llm_provider="Bing", # Example: try a specific provider
        llm_temperature=0.5
    )
    # To use defaults, set: sample_llm_config_override = None

    log.info("Test Keywords Batch: %s", sample_keywords_batch)
    log.info("Test Existing Topic Names: %s", sample_existing_topics or "None")
    if sample_llm_config_override:
        log.info("Test LLM Config Override: %s", sample_llm_config_override.model_dump(exclude_none=True))
    else:
        log.info("Test LLM Config Override: Using defaults from config.py")

    try:
        # Call your LLM service function
        clustered_topics = await llm_service.get_keyword_clusters_from_llm(
            keywords_batch=sample_keywords_batch,
            existing_topic_names=sample_existing_topics,
            request_llm_config=sample_llm_config_override # Pass the override or None
        )

        log.info("--- LLM Clustering Test Results ---")
        if clustered_topics:
            log.info("Successfully received %d clusters:", len(clustered_topics))
            for i, cluster in enumerate(clustered_topics):
                log.info(
                    "  Cluster %d: Name='%s', Keywords=%d %s",
                    i + 1,
                    cluster.topic_name,
                    len(cluster.keywords),
                    # Display first 3 keywords or all if less than 3
                    cluster.keywords[:3] if len(cluster.keywords) > 3 else cluster.keywords
                )
                # For more detail, you can print all keywords:
                # log.info("    Full keywords: %s", cluster.keywords)
        else:
            log.info("No clusters were returned by the LLM service.")

    except ImportError as e: # Catch if g4f_chat_async itself failed to import in llm_service
        log.critical("Critical Import Error from LLM Service: %s", e)
    except RuntimeError as e: # Catch critical errors from llm_service
        log.critical("Runtime Error from LLM Service: %s", e)
    except Exception as e:
        log.error("An error occurred during the test: %s", e, exc_info=True)

    log.info("--- LLM Clustering Service Test Finished ---")

if __name__ == "__main__":
    # Ensure g4f is configured (e.g., if it needs specific setup for providers)
    # This script assumes g4f is ready to use as per your g4ftemplate.py.
    asyncio.run(main_test())
