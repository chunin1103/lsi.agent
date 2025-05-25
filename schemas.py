# schemas.py
"""
Pydantic models (schemas) for data validation and serialization.
"""
from pydantic import BaseModel, Field
from typing import List, Optional, Dict, Any

# --- Existing Schemas ---
class CrawlStatus(BaseModel):
    """
    Represents the status of keyword generation tasks for a given seed.
    """
    seed: str
    total_cached_keywords: int
    suggest_task_complete: bool
    planner_task_complete: bool

# --- New Schemas for Topic Clustering ---

class LLMConfigInput(BaseModel):
    """
    Optional LLM configuration for a specific clustering request.
    These parameters will be passed to the g4f_chat_async function.
    """
    llm_model: Optional[str] = Field(None, description="Specific LLM model to use (e.g., 'gpt-4o', 'claude-3-opus-20240229'). If None, g4f default or application default is used.")
    llm_provider: Optional[str] = Field(None, description="String name of the LLM provider (e.g., 'Bing', 'Google', 'OpenAI'). If None, g4f auto-selects or application default is used.")
    llm_temperature: Optional[float] = Field(None, ge=0.0, le=2.0, description="Temperature for LLM generation (e.g., 0.7). Controls randomness.")
    # Consider adding timeout if you want to allow per-request timeout overrides
    # llm_timeout: Optional[int] = Field(None, gt=0, description="Timeout in seconds for the LLM call.")

    class Config:
        extra = 'ignore' # Ignore any extra fields sent in the config object

class TopicClusterRequest(BaseModel):
    """
    Request body for the /topics/cluster endpoint.
    """
    keywords: List[str] = Field(..., min_items=1, description="A list (batch) of keywords to be clustered. Must not be empty.")
    existing_topic_names: Optional[List[str]] = Field(None, description="Optional list of topic names already generated in the current session to guide distinct name generation.")
    config: Optional[LLMConfigInput] = Field(None, description="Optional LLM configuration for this specific request.")

class TopicClusterOutput(BaseModel):
    """
    Represents a single topic cluster in the response.
    """
    topic_name: str = Field(..., description="The name of the generated topic cluster.")
    keywords: List[str] = Field(..., description="List of keywords from the input batch belonging to this topic.")

class ClusteringMetadataOutput(BaseModel):
    """
    Metadata about the clustering process for the batch.
    """
    total_keywords_processed_in_batch: int = Field(..., description="Total number of keywords received in the input batch for clustering.")
    total_topics_found_in_batch: int = Field(..., description="Total number of distinct topics generated for this batch.")
    llm_model_used: str = Field(..., description="The actual LLM model that was used for this clustering request.")
    # You could add llm_provider_used if it's easily determined and useful

class TopicClusteringResponse(BaseModel):
    """
    Response body for the /topics/cluster endpoint.
    """
    clusters: List[TopicClusterOutput] = Field(..., description="List of topic clusters generated for the submitted batch of keywords.")
    metadata: ClusteringMetadataOutput = Field(..., description="Metadata about the clustering operation for this batch.")

