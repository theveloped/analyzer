"""Plan routes: the per-part production plan, dispositions and impact.

Thin HTTP wrappers over ``plans.py`` (docs/PLAN-ARCHITECTURE.md). Plan
mutations are read-modify-write over workdir sidecars, so they share one
lock (FastAPI runs sync handlers in a threadpool) — numpy/meshlib are never
touched here, everything is JSON + fingerprint reads.
"""

import threading

from fastapi import HTTPException
from fastapi.responses import FileResponse

import plans
from api.schemas import (DispositionRequest, PlanImpactRequest,
                         PlanPutRequest, ReportPublishRequest)

_plan_lock = threading.Lock()


def register(app, part_or_404, workdir_for):
    @app.get("/api/parts/{part_id}/plan")
    def get_plan(part_id: str):
        part = part_or_404(part_id)
        return plans.plan_section(workdir_for(part["id"]))

    @app.put("/api/parts/{part_id}/plan")
    def put_plan(part_id: str, body: PlanPutRequest):
        part = part_or_404(part_id)
        workdir = workdir_for(part["id"])
        with _plan_lock:
            try:
                plans.save_plan(workdir, body.plan, body.revision)
            except plans.RevisionConflictError as error:
                raise HTTPException(status_code=409, detail=str(error))
            except ValueError as error:
                raise HTTPException(status_code=400, detail=str(error))
            return plans.plan_section(workdir)

    @app.get("/api/parts/{part_id}/plan/history")
    def get_plan_history(part_id: str):
        part = part_or_404(part_id)
        return plans.plan_history(workdir_for(part["id"]))

    @app.post("/api/parts/{part_id}/plan/impact")
    def post_plan_impact(part_id: str, body: PlanImpactRequest):
        part = part_or_404(part_id)
        try:
            return plans.impact_preview(workdir_for(part["id"]), body.patch)
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error))

    @app.get("/api/parts/{part_id}/reports")
    def get_reports(part_id: str):
        part = part_or_404(part_id)
        return plans.list_reports(workdir_for(part["id"]))

    @app.post("/api/parts/{part_id}/reports", status_code=201)
    def post_report(part_id: str, body: ReportPublishRequest):
        part = part_or_404(part_id)
        try:
            return plans.publish_report(
                workdir_for(part["id"]),
                {"title": body.title, "part": body.part or part["name"],
                 "checks": body.checks})
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error))

    @app.get("/api/parts/{part_id}/reports/{rid}")
    def get_report(part_id: str, rid: str):
        part = part_or_404(part_id)
        report = plans.load_report(workdir_for(part["id"]), rid)
        if report is None:
            raise HTTPException(status_code=404, detail="unknown report")
        return report

    @app.get("/api/parts/{part_id}/reports/{rid}/shots/{name}")
    def get_report_shot(part_id: str, rid: str, name: str):
        part = part_or_404(part_id)
        path = plans.report_shot_path(workdir_for(part["id"]), rid, name)
        if path is None:
            raise HTTPException(status_code=404, detail="unknown shot")
        return FileResponse(path, media_type="image/png")

    @app.get("/api/parts/{part_id}/dispositions")
    def get_dispositions(part_id: str):
        part = part_or_404(part_id)
        return plans.load_dispositions(workdir_for(part["id"]))

    @app.post("/api/parts/{part_id}/dispositions", status_code=201)
    def post_disposition(part_id: str, body: DispositionRequest):
        part = part_or_404(part_id)
        workdir = workdir_for(part["id"])
        with _plan_lock:
            try:
                stored = plans.append_disposition(workdir, body.model_dump())
            except ValueError as error:
                raise HTTPException(status_code=400, detail=str(error))
        return {"stored": stored,
                "dispositions": plans.latest_dispositions(workdir)}
