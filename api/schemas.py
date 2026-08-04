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


class SplitRequest(BaseModel):
    """One face cut: two snapped boundary mesh-vertex ids on an effective
    face (see splits.py)."""
    face: int
    start: int
    end: int


class RoutePutRequest(BaseModel):
    """Store a new route revision. ``revision`` is the revision the client
    edited (optimistic concurrency — a mismatch is a 409)."""
    route: dict
    revision: int


class PmiPutRequest(BaseModel):
    """Author/replace a part's semantic PMI. ``pmi`` is a full pmi.json payload
    (dimensions/tolerances/datums); it is validated and its round-trip warnings
    re-derived server-side before the file is written."""
    pmi: dict


class EjectorSimRequest(BaseModel):
    """Interactive ejector-pin simulation over a stored ejection_sticking
    result (identified by its cache hash)."""
    result_hash: str
    pins: list[EjectorPin]
    E: float = 2000.0  # MPa
    allowable_pressure: float = 80.0  # MPa
