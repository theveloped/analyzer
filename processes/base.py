"""Process/analysis registry model.

A ProcessDef groups AnalysisDefs for one manufacturing process (CNC,
injection molding, sheet metal, ...). Each AnalysisDef declares typed
parameters (so the frontend can auto-generate forms) and a ``run`` callable
that executes against a part working directory, writing its outputs into
the on-disk cache shared with the CLI.

This module is framework-free: it must stay importable without fastapi so
the CLI can use the registry too.
"""

import hashlib
import json
import os
from dataclasses import dataclass, field

import numpy as np

RESULTS_DIR = "results"


@dataclass
class Param:
    """One declared analysis parameter, renderable as a form control."""
    name: str
    type: str  # bool | int | number | string | select | int_list | number_list | tip_list | tool_list
    default: object = None
    label: str = None
    unit: str = None
    min: object = None
    max: object = None
    options: list = None  # for select

    def to_dict(self):
        data = {"name": self.name, "type": self.type, "default": self.default}
        for key in ("label", "unit", "min", "max", "options"):
            value = getattr(self, key)
            if value is not None:
                data[key] = value
        return data


@dataclass
class AnalysisDef:
    """A runnable analysis: declared params + a run(workdir, params, progress)."""
    id: str
    label: str
    params: list
    run: callable
    requires: list = field(default_factory=list)
    description: str = ""

    def to_dict(self):
        return {
            "id": self.id,
            "label": self.label,
            "description": self.description,
            "requires": list(self.requires),
            "params": [p.to_dict() for p in self.params],
        }


@dataclass
class ProcessDef:
    id: str
    label: str
    analyses: list
    description: str = ""

    def to_dict(self):
        return {
            "id": self.id,
            "label": self.label,
            "description": self.description,
            "analyses": [a.to_dict() for a in self.analyses],
        }

    def analysis(self, analysis_id):
        for entry in self.analyses:
            if entry.id == analysis_id:
                return entry
        raise KeyError(f"unknown analysis {self.id}/{analysis_id}")


@dataclass
class AnalysisResult:
    """What a run produced: JSON-safe stats plus ids of new cache fields."""
    stats: dict = field(default_factory=dict)
    fields: list = field(default_factory=list)

    def to_dict(self):
        return {"stats": self.stats, "fields": list(self.fields)}


def apply_defaults(analysis, params):
    """Fill missing params with declared defaults; reject unknown keys."""
    known = {p.name for p in analysis.params}
    unknown = set(params) - known
    if unknown:
        raise ValueError(f"unknown parameters for {analysis.id}: {sorted(unknown)}")
    merged = {p.name: p.default for p in analysis.params}
    merged.update(params)
    return merged


def params_hash(params):
    """Stable short hash of a parameter dict, used as the cache key."""
    canonical = json.dumps(params, sort_keys=True, separators=(",", ":"))
    return hashlib.sha1(canonical.encode()).hexdigest()[:12]


def result_paths(workdir, process_id, analysis_id, params):
    base = os.path.join(workdir, RESULTS_DIR, process_id, analysis_id,
                        params_hash(params))
    return base + ".json", base + ".npz"


def load_cached_result(workdir, process_id, analysis_id, params):
    """Return a previously stored AnalysisResult dict, or None."""
    json_path, _ = result_paths(workdir, process_id, analysis_id, params)
    if not os.path.exists(json_path):
        return None
    with open(json_path) as f:
        return json.load(f)


def load_result_arrays(workdir, process_id, analysis_id, params):
    """Open the npz of a stored result (None if absent)."""
    _, npz_path = result_paths(workdir, process_id, analysis_id, params)
    if not os.path.exists(npz_path):
        return None
    return np.load(npz_path, allow_pickle=False)


def store_result(workdir, process_id, analysis_id, params, stats, arrays=None,
                 field_meta=None):
    """Persist a generic analysis result (JSON stats + optional npz arrays).

    ``arrays`` maps npz member name -> numpy array; ``field_meta`` maps the
    same names -> descriptor params surfaced in the manifest.
    """
    json_path, npz_path = result_paths(workdir, process_id, analysis_id, params)
    os.makedirs(os.path.dirname(json_path), exist_ok=True)

    if arrays:
        np.savez_compressed(npz_path, **arrays)

    payload = {
        "process": process_id,
        "analysis": analysis_id,
        "params": params,
        "stats": stats,
        "arrays": {name: dict(field_meta.get(name, {})) if field_meta else {}
                   for name in (arrays or {})},
    }
    with open(json_path, "w") as f:
        json.dump(payload, f, indent=1)
    return payload
