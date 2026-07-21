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


class PlanPutRequest(BaseModel):
    """Store a new plan revision. ``revision`` is the revision the client
    edited (optimistic concurrency — a mismatch is a 409)."""
    plan: dict
    revision: int


class PlanImpactRequest(BaseModel):
    """Dry-run a plan edit: decisions deep-merge, operations/checks replace
    when present. Never enqueues work."""
    patch: dict = Field(default_factory=dict)


class ReportPublishRequest(BaseModel):
    """Publish an immutable report bundle: per-check verdict/findings/
    evidence plus optional PNG data-URL shots."""
    title: str = ""
    part: str = ""
    checks: list[dict]


class DispositionRequest(BaseModel):
    """One human judgment on a finding (appended, never overwritten)."""
    finding_id: str
    state: str  # open | accepted | customer_approval | resolved
    by: str
    why: str = ""
    evidence: dict = Field(default_factory=dict)


class EjectorSimRequest(BaseModel):
    """Interactive ejector-pin simulation over a stored ejection_sticking
    result (identified by its cache hash)."""
    result_hash: str
    pins: list[EjectorPin]
    E: float = 2000.0  # MPa
    allowable_pressure: float = 80.0  # MPa
