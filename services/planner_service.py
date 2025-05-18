# services/planner_service.py
"""
Handles interaction with Google Ads Keyword Planner.
"""
import logging
import pathlib
from typing import List, Tuple, Any
import yaml

from google.ads.googleads.client import GoogleAdsClient
from google.ads.googleads.errors import GoogleAdsException

import config # Import from parent directory's config.py

# Initialize logger for this module
log = logging.getLogger(__name__)

def _load_ads_client_and_id(
    config_path_str: str = config.GOOGLE_ADS_CONFIG_PATH
) -> Tuple[GoogleAdsClient | None, str | None]:
    """
    Loads the Google Ads client and Customer ID from a YAML configuration file.

    Args:
        config_path_str: Path to the google-ads.yaml file.

    Returns:
        A tuple (GoogleAdsClient instance or None, Customer ID string or None).
    """
    try:
        config_path = pathlib.Path(config_path_str)
        with open(config_path, "r", encoding="utf-8") as f:
            ads_config_dict = yaml.safe_load(f)
        
        client = GoogleAdsClient.load_from_dict(ads_config_dict)
        customer_id = ads_config_dict.get("my_customer_id") # Ensure this key exists in your YAML
        
        if customer_id is not None:
            customer_id = str(customer_id) # Ensure it's a string
        else:
            log.warning("PLANNER_CONFIG_ERROR: 'my_customer_id' not found in %s.", config_path_str)
            return None, None
            
        return client, customer_id
    except FileNotFoundError:
        log.error("PLANNER_CONFIG_ERROR: Google Ads config file not found at %s.", config_path_str)
        return None, None
    except Exception as e: # Catch other potential errors during client loading
        log.error("PLANNER_CONFIG_ERROR: Failed to load Google Ads client from %s. Error: %s", config_path_str, e, exc_info=True)
        return None, None

def fetch_keyword_planner_ideas(
    seed_keyword: str,
    max_results: int = config.DEFAULT_KEYWORD_LIMIT // 2 # Example: planner contributes up to half
) -> List[str]:
    """
    Fetches keyword ideas from Google Ads Keyword Planner.
    This is a blocking (synchronous) function and should be run in a separate thread
    using `asyncio.to_thread` in an async application.

    Args:
        seed_keyword: The seed keyword for generating ideas.
        max_results: The maximum number of keyword ideas to return.

    Returns:
        A list of keyword idea strings, or an empty list on failure.
    """
    client, customer_id = _load_ads_client_and_id()

    if not client or not customer_id:
        log.warning("PLANNER_FETCH_SKIP: Client or Customer ID not available for seed '%s'.", seed_keyword)
        return []

    try:
        keyword_plan_idea_service = client.get_service("KeywordPlanIdeaService")
        generate_keyword_ideas_request = client.get_type("GenerateKeywordIdeasRequest")

        generate_keyword_ideas_request.customer_id = customer_id
        # KeywordSeed
        if not generate_keyword_ideas_request.keyword_seed.keywords:
            generate_keyword_ideas_request.keyword_seed.keywords.append(seed_keyword)
        else:
            generate_keyword_ideas_request.keyword_seed.keywords = [seed_keyword]
        
        # Language and Geo-targeting (Vietnamese, Vietnam)
        generate_keyword_ideas_request.language = "languageConstants/1040" 
        if not generate_keyword_ideas_request.geo_target_constants:
            generate_keyword_ideas_request.geo_target_constants.append("geoTargetConstants/2704")
        else:
            generate_keyword_ideas_request.geo_target_constants = ["geoTargetConstants/2704"]

        generate_keyword_ideas_request.keyword_plan_network = (
            client.enums.KeywordPlanNetworkEnum.GOOGLE_SEARCH
        )
        # Other options like include_adult_keywords, historical_metrics can be set here if needed.

        response_iterator = keyword_plan_idea_service.generate_keyword_ideas(
            request=generate_keyword_ideas_request
        )

        keyword_ideas: List[str] = []
        for idea_result in response_iterator.results: # Accessing .results attribute
            if idea_result.text:
                keyword_ideas.append(idea_result.text)
            if len(keyword_ideas) >= max_results:
                break
        
        log.info("PLANNER_FETCH_SUCCESS: Seed='%s', Found=%d keyword ideas.", seed_keyword, len(keyword_ideas))
        return keyword_ideas

    except GoogleAdsException as e:
        log.warning("PLANNER_FETCH_GADS_ERROR: Seed='%s'. GoogleAdsException: %s", seed_keyword, e.failure, exc_info=False)
        # Log detailed errors from the GoogleAdsException
        for error in e.failure.errors:
            log.warning("  GA_Error: Code=%s, Message='%s'", error.error_code, error.message)
            if error.trigger:
                log.warning("    GA_Trigger: Value='%s'", error.trigger.string_value)
            # You can log other details like error.location if needed
        return []
    except Exception as e: # Catch any other unexpected errors
        log.error("PLANNER_FETCH_UNEXPECTED_ERROR: Seed='%s'. Error: %s", seed_keyword, e, exc_info=True)
        return []
