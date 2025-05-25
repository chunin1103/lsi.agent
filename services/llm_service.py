# services/llm_service.py
"""
Service for interacting with the Large Language Model (LLM)
for topic clustering of keywords.
"""
import json
import logging
import g4f # For g4f.Provider classes
from typing import List, Optional, Dict, Any, Sequence # Added Sequence

try:
    from g4ftemplate import g4f_chat_async
except ImportError:
    logging.error("LLM_SERVICE: Failed to import g4f_chat_async from g4ftemplate. Ensure it's accessible.")
    async def g4f_chat_async(*args, **kwargs):
        raise ImportError("g4f_chat_async could not be imported. Clustering will fail.")

import config as app_config 
from schemas import LLMConfigInput, TopicClusterOutput 

log = logging.getLogger(__name__)

def _get_g4f_provider_class(provider_name: str) -> Optional[Any]:
    """
    Attempts to get a g4f.Provider class by its string name.
    Names are case-sensitive as they map to class names in g4f.Provider.
    Example provider names from g4f docs: "Bing", "Liaobots", "Blackbox", "PollinationsAI", etc.
    """
    if not provider_name:
        return None
    try:
        # Ensure provider_name matches the exact class name in g4f.Provider
        # For example, "Liaobots" (not "liaobots") if the class is g4f.Provider.Liaobots
        provider_class = getattr(g4f.Provider, provider_name, None)
        if provider_class:
            log.debug("LLM_SERVICE: Mapped provider name '%s' to class %s", provider_name, provider_class)
            return provider_class
        else:
            log.warning("LLM_SERVICE: Provider name '%s' not found in g4f.Provider module.", provider_name)
            return None
    except Exception as e:
        log.error("LLM_SERVICE: Error getting provider class for '%s': %s", provider_name, e)
        return None


def _construct_llm_prompt(keywords: List[str], existing_topic_names: Optional[List[str]]) -> str:
    keyword_list_str = "\n".join([f"- {kw}" for kw in keywords])
    prompt_parts = [
        "You are an expert SEO and keyword researcher with 10 years of experience, specializing in Latent Semantic Indexing (LSI) and topic clustering.",
        "Your task is to group the following batch of keywords into distinct SEO-relevant topic clusters.",
        "For each topic cluster you identify from the current keyword batch:",
        "1. Provide a concise and descriptive 'topic_name' (ideally 3-5 words, maximum 7 words). This name should be suitable for SEO content strategy.",
        "2. List only the keywords from the *current input batch* that belong to this topic cluster.",
        "\nKeywords for the current batch to cluster:",
        keyword_list_str,
    ]
    if existing_topic_names:
        existing_topics_str = "\n".join([f"- {name}" for name in existing_topic_names])
        prompt_parts.extend([
            "\nIMPORTANT: A list of 'Previously Generated Topic Names' from the same research session is provided below.",
            "When generating 'topic_name' for the current batch, please ensure they are semantically distinct from these previously generated names, while still being highly relevant to the keywords they represent from the current batch.",
            "Do not simply rephrase; aim for genuinely different thematic angles if the keywords support it.",
            "\nPreviously Generated Topic Names:",
            existing_topics_str,
        ])
    prompt_parts.extend([
        "\nRespond ONLY with a valid JSON list of objects. Each object in the list must have exactly two keys: 'topic_name' (string) and 'keywords' (a list of strings from the input batch).",
        "Do not include any explanations, apologies, or introductory/concluding text outside of the JSON list itself.",
        "Example of the expected JSON format:",
        """
[
    {
        "topic_name": "Example Topic Name Alpha",
        "keywords": ["keywordA from batch", "keywordB from batch"]
    },
    {
        "topic_name": "Example Topic Name Beta",
        "keywords": ["keywordC from batch", "keywordD from batch"]
    }
]
        """.strip()
    ])
    return "\n\n".join(prompt_parts)

async def get_keyword_clusters_from_llm(
    keywords_batch: List[str],
    existing_topic_names: Optional[List[str]],
    request_llm_config: Optional[LLMConfigInput]
) -> List[TopicClusterOutput]:
    prompt = _construct_llm_prompt(keywords_batch, existing_topic_names)
    messages = [{"role": "user", "content": prompt}]

    llm_model_to_use = app_config.DEFAULT_LLM_MODEL
    llm_temperature_to_use = app_config.DEFAULT_LLM_TEMPERATURE
    llm_timeout_to_use = app_config.DEFAULT_LLM_TIMEOUT_SECONDS
    
    provider_classes_to_try: List[Any] = [] # List of g4f.Provider classes

    # Determine LLM parameters, using request config or app defaults
    if request_llm_config:
        if request_llm_config.llm_model:
            llm_model_to_use = request_llm_config.llm_model
        if request_llm_config.llm_temperature is not None:
            llm_temperature_to_use = request_llm_config.llm_temperature
        
        if request_llm_config.llm_provider:
            log.info("LLM_SERVICE: Client specified provider: '%s'", request_llm_config.llm_provider)
            provider_class = _get_g4f_provider_class(request_llm_config.llm_provider)
            if provider_class:
                provider_classes_to_try = [provider_class]
            else:
                log.warning("LLM_SERVICE: Client specified provider '%s' is not valid or found. Falling back to configured preferred providers.", request_llm_config.llm_provider)
                # Fall through to use PREFERRED_LLM_PROVIDERS if client-specified is invalid

    # If no valid provider class was set from client request, use the configured preferred list
    if not provider_classes_to_try:
        log.info("LLM_SERVICE: Using configured preferred providers: %s", app_config.PREFERRED_LLM_PROVIDERS)
        for provider_name in app_config.PREFERRED_LLM_PROVIDERS:
            if provider_name is None: # Special case for auto-selection by g4f
                # If None is in the list, g4f_chat_async handles it by trying auto-selection
                # We can pass None directly in the list to g4f_chat_async
                provider_classes_to_try.append(None) 
                log.info("LLM_SERVICE: Adding 'None' (auto-select) to provider list based on config.")
                break # If None is encountered, it's usually the last resort / general auto-pick
            elif isinstance(provider_name, str):
                provider_class = _get_g4f_provider_class(provider_name)
                if provider_class:
                    provider_classes_to_try.append(provider_class)
        
        # If after processing config, provider_classes_to_try is still empty,
        # it means all configured names were invalid. g4f_chat_async needs None for auto.
        if not provider_classes_to_try:
            provider_classes_to_try = [None] # Ensure at least auto-selection is attempted by g4f_chat_async
            log.info("LLM_SERVICE: No valid preferred providers found in config. Defaulting to full auto-select by g4f.")


    # Ensure g4f_chat_async gets a list, or None if only auto-select is desired from the start.
    # The g4ftemplate.py handles providers=None as prov_list = [None]
    # If provider_classes_to_try contains [None], it will also effectively be auto-select.
    # If provider_classes_to_try is an empty list [], g4f_chat_async's loop won't run.
    # So, if provider_classes_to_try is truly empty (all configured string names were invalid),
    # we should pass None to trigger g4f's default behavior.
    final_providers_for_g4f = provider_classes_to_try if provider_classes_to_try else None
    if final_providers_for_g4f == [None] and len(provider_classes_to_try) == 1: # if only a single None entry
        final_providers_for_g4f = None # Pass actual None for full auto-select, not a list containing None.

    log.info(
        "LLM_SERVICE: Calling LLM for clustering. Model='%s', Providers to try (classes/None): %s, Temp=%.1f, Timeout=%d s",
        llm_model_to_use,
        [p.__name__ if p else "None (auto)" for p in final_providers_for_g4f] if isinstance(final_providers_for_g4f, list) else "None (auto-select by g4f)",
        llm_temperature_to_use,
        llm_timeout_to_use
    )
    log.debug("LLM_SERVICE: Prompt being sent to LLM:\n%s", prompt)

    try:
        llm_response_str = await g4f_chat_async(
            messages=messages,
            model=llm_model_to_use,
            providers=final_providers_for_g4f, # Pass the list of provider classes or None
            temperature=llm_temperature_to_use,
            timeout=llm_timeout_to_use
        )
        log.info("LLM_SERVICE: Raw response received from LLM. Length: %d chars.", len(llm_response_str or ""))
        log.debug("LLM_SERVICE: Raw LLM response string:\n%s", llm_response_str) 

        if not llm_response_str:
            log.warning("LLM_SERVICE: LLM returned an empty response.")
            return []

        try:
            cleaned_response_str = llm_response_str.strip()
            if cleaned_response_str.startswith("```json"):
                cleaned_response_str = cleaned_response_str[len("```json"):].strip()
            if cleaned_response_str.startswith("```"): 
                cleaned_response_str = cleaned_response_str[len("```"):].strip()
            if cleaned_response_str.endswith("```"):
                cleaned_response_str = cleaned_response_str[:-len("```")].strip()
            
            if not cleaned_response_str.startswith("[") or not cleaned_response_str.endswith("]"):
                log.error("LLM_SERVICE: LLM response does not appear to be a JSON list. Response: %s", cleaned_response_str[:500])
                raise ValueError("LLM response is not in the expected JSON list format.")

            parsed_clusters_data = json.loads(cleaned_response_str)
            
            if not isinstance(parsed_clusters_data, list):
                log.error("LLM_SERVICE: Parsed LLM response is not a list. Type: %s. Response: %s", type(parsed_clusters_data), str(parsed_clusters_data)[:500])
                raise ValueError("LLM response, after parsing, was not a list as expected.")

            validated_clusters: List[TopicClusterOutput] = []
            for cluster_data in parsed_clusters_data:
                if not isinstance(cluster_data, dict):
                    log.warning("LLM_SERVICE: Item in LLM cluster list is not a dictionary: %s", str(cluster_data)[:100])
                    continue
                try:
                    original_keywords_set = set(k.lower() for k in keywords_batch) 
                    llm_cluster_keywords = cluster_data.get("keywords", [])
                    if not isinstance(llm_cluster_keywords, list):
                        log.warning("LLM_SERVICE: 'keywords' field in cluster is not a list: %s", str(llm_cluster_keywords)[:100])
                        llm_cluster_keywords = [] 

                    filtered_cluster_keywords = [
                        kw for kw in llm_cluster_keywords if isinstance(kw, str) and kw.lower() in original_keywords_set
                    ]
                    
                    if not filtered_cluster_keywords and llm_cluster_keywords:
                        log.warning("LLM_SERVICE: Cluster '%s' had keywords, but none matched input batch. Original from LLM: %s", cluster_data.get("topic_name"), llm_cluster_keywords)
                        continue 
                    
                    if not filtered_cluster_keywords: 
                        log.info("LLM_SERVICE: Skipping cluster '%s' as it has no valid keywords from the input batch after filtering.", cluster_data.get("topic_name"))
                        continue

                    validated_cluster = TopicClusterOutput(
                        topic_name=str(cluster_data.get("topic_name", "Unnamed Topic")), 
                        keywords=filtered_cluster_keywords
                    )
                    validated_clusters.append(validated_cluster)
                except Exception as pydantic_err: 
                    log.warning("LLM_SERVICE: Failed to validate a cluster object from LLM. Data: %s. Error: %s", str(cluster_data)[:200], pydantic_err)
            
            if validated_clusters:
                log.info("LLM_SERVICE: Successfully parsed and validated %d clusters from LLM response:", len(validated_clusters))
                for i, cluster in enumerate(validated_clusters):
                    log.info("  Cluster %d: Name='%s', Keywords=%d %s", i+1, cluster.topic_name, len(cluster.keywords), cluster.keywords[:3] if len(cluster.keywords) > 3 else cluster.keywords)
            else:
                log.info("LLM_SERVICE: No valid clusters were generated from the LLM response after validation.")
            return validated_clusters

        except json.JSONDecodeError as e:
            log.error("LLM_SERVICE: Failed to decode JSON from LLM response. Error: %s. Response snippet: %s", e, llm_response_str[:500])
            raise ValueError(f"LLM response was not valid JSON: {e}")
        except ValueError as e: 
            log.error("LLM_SERVICE: Error processing LLM response structure: %s", e)
            raise 
            
    except ImportError as e: 
        log.critical("LLM_SERVICE: g4ftemplate.py or g4f_chat_async not found. Clustering is non-functional. Error: %s", e)
        raise RuntimeError(f"LLM service dependency missing: {e}") 
    except Exception as e: 
        log.error("LLM_SERVICE: Error during LLM call (g4f_chat_async). Error: %s", e, exc_info=True)
        raise 
