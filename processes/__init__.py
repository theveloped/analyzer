"""Registry of manufacturing processes and their analyses."""

from processes import cnc, injection_molding, prep, sheet_metal, tube_laser
from processes.base import (AnalysisDef, AnalysisResult, Param, ProcessDef,
                            apply_defaults)

REGISTRY = {process.id: process for process in (
    prep.PROCESS,
    cnc.PROCESS,
    injection_molding.PROCESS,
    sheet_metal.PROCESS,
    tube_laser.PROCESS,
)}


def get_analysis(process_id, analysis_id):
    if process_id not in REGISTRY:
        raise KeyError(f"unknown process {process_id}")
    return REGISTRY[process_id].analysis(analysis_id)


def catalog():
    return [process.to_dict() for process in REGISTRY.values()]
