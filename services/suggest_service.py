# services/suggest_service.py
"""
Handles interaction with Google Autosuggest API.
"""
import httpx
import json
import logging
from typing import List, Dict

# Initialize logger for this module
log = logging.getLogger(__name__)

# User-Agent header for requests
_HEADERS: Dict[str, str] = {"User-Agent": "Mozilla/5.0 (compatible; LSIKeywordBot/1.0)"}

async def fetch_google_autosuggest(
    query: str,
    language: str = "vi",
    country_code: str = "VN",
    timeout_seconds: float = 8.0
) -> List[str]:
    """
    Fetches keyword suggestions from Google Autosuggest.

    Args:
        query: The search query (seed keyword).
        language: The language code (e.g., "vi" for Vietnamese).
        country_code: The country code (e.g., "VN" for Vietnam).
        timeout_seconds: Timeout for the HTTP request.

    Returns:
        A list of suggested keywords, or an empty list on failure.
    """
    suggest_url = "https://suggestqueries.google.com/complete/search"
    params = {
        "client": "chrome",  # Using "chrome" client type for common results
        "hl": language,
        "gl": country_code,
        "q": query,
        "num": "20"  # Request a decent number of suggestions
    }

    try:
        async with httpx.AsyncClient(timeout=timeout_seconds) as client:
            response = await client.get(suggest_url, params=params, headers=_HEADERS)
            response.raise_for_status()  # Raises HTTPStatusError for 4xx/5xx responses

            # Google suggest returns a list like: ["query", [suggestions_list], [descriptions_list], {metadata}]
            # We are interested in response.json()[1] which is the suggestions_list
            response_json = response.json()
            
            if isinstance(response_json, list) and \
               len(response_json) > 1 and \
               isinstance(response_json[1], list):
                suggestions = [str(item) for item in response_json[1] if isinstance(item, (str, int, float))]
                log.debug("SUGGEST_FETCH_SUCCESS: Query='%s', Found=%d suggestions.", query, len(suggestions))
                return suggestions
            else:
                log.warning(
                    "SUGGEST_FETCH_UNEXPECTED_FORMAT: Query='%s', Unexpected JSON structure: %s",
                    query, str(response_json)[:200] # Log a snippet of the response
                )
                return []
    except httpx.HTTPStatusError as e:
        log.warning(
            "SUGGEST_FETCH_HTTP_ERROR: Query='%s', Status=%d, Response='%s'",
            query, e.response.status_code, e.response.text[:200]
        )
        return []
    except httpx.RequestError as e: # Covers network errors, timeouts, etc.
        log.warning("SUGGEST_FETCH_REQUEST_ERROR: Query='%s', Error='%s'", query, str(e))
        return []
    except json.JSONDecodeError as e:
        log.warning("SUGGEST_FETCH_JSON_ERROR: Query='%s', Error='%s'", query, str(e))
        return []
    except Exception as e: # Catch any other unexpected errors
        log.error("SUGGEST_FETCH_UNEXPECTED_ERROR: Query='%s', Error='%s'", query, e, exc_info=True)
        return []
