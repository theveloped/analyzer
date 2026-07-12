"""Pydantic request models for the API."""

from pydantic import BaseModel, Field


class JobRequest(BaseModel):
    part_id: str
    process: str
    analysis: str
    params: dict = Field(default_factory=dict)


class EjectorPin(BaseModel):
    point: list[float]  # xyz on the part surface
    diameter: float  # mm


class EjectorSimRequest(BaseModel):
    """Interactive ejector-pin simulation over a stored ejection_sticking
    result (identified by its cache hash)."""
    result_hash: str
    pins: list[EjectorPin]
    E: float = 2000.0  # MPa
    allowable_pressure: float = 80.0  # MPa
