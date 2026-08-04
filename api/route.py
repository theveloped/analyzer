"""Route endpoints: the per-part operations and checks.

Thin HTTP wrappers over the top-level ``route.py``
(docs/ROUTE-ARCHITECTURE.md). Route mutations are read-modify-write over
workdir sidecars, so they share one lock (FastAPI runs sync handlers in a
threadpool) — numpy/meshlib are never touched here, everything is JSON +
fingerprint reads.
"""

import threading

from fastapi import HTTPException

import route as route_mod
from api.schemas import RoutePutRequest

_route_lock = threading.Lock()


def register(app, part_or_404, workdir_for):
    @app.get("/api/parts/{part_id}/route")
    def get_route(part_id: str):
        part = part_or_404(part_id)
        return route_mod.route_section(workdir_for(part["id"]))

    @app.put("/api/parts/{part_id}/route")
    def put_route(part_id: str, body: RoutePutRequest):
        part = part_or_404(part_id)
        workdir = workdir_for(part["id"])
        with _route_lock:
            try:
                route_mod.save_route(workdir, body.route, body.revision)
            except route_mod.RevisionConflictError as error:
                raise HTTPException(status_code=409, detail=str(error))
            except ValueError as error:
                raise HTTPException(status_code=400, detail=str(error))
            return route_mod.route_section(workdir)

    @app.get("/api/parts/{part_id}/route/history")
    def get_route_history(part_id: str):
        part = part_or_404(part_id)
        return route_mod.route_history(workdir_for(part["id"]))

    @app.get("/api/catalogue/machines")
    def get_machines():
        return route_mod.list_machines()
