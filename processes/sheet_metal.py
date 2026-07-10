"""Sheet metal process placeholder.

Registered so the frontend shows the seam; analyses (bend radius, hole to
edge distance, ...) plug in here as AnalysisDefs following processes/cnc.py.
"""

from processes.base import ProcessDef

PROCESS = ProcessDef(
    id="sheet_metal",
    label="Sheet metal (coming soon)",
    description="No analyses implemented yet.",
    analyses=[],
)
