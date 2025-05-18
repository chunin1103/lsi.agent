# schemas.py
"""
Pydantic models (schemas) for data validation and serialization.
"""
from pydantic import BaseModel

class CrawlStatus(BaseModel):
    """
    Represents the status of keyword generation tasks for a given seed.
    """
    seed: str
    total_cached_keywords: int
    suggest_task_complete: bool
    planner_task_complete: bool

# You can add other schemas here as your API grows, for example:
# class KeywordItem(BaseModel):
#     keyword: str
#
# class ErrorDetail(BaseModel):
#     message: str
#     detail: str | None = None