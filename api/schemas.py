"""Pydantic request models for the API."""

from pydantic import BaseModel, Field


class JobRequest(BaseModel):
    part_id: str
    process: str
    analysis: str
    params: dict = Field(default_factory=dict)
