"""FastAPI application: parts, analysis catalog, jobs and binary fields.

create_app(root) serves the parts root (each subdirectory with mesh arrays
or a part.json is a part) and, when built, the frontend/dist single-page
app at /.
"""

import os

from fastapi import FastAPI, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles

import processes
from api import fields as fields_api
from api import manifest as manifest_api
from api import parts as parts_api
from api import ejector as ejector_api
from api.jobs import JobManager, PartBusyError
from api.schemas import EjectorSimRequest, JobRequest
import pipeline

FRONTEND_DIST = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "frontend", "dist")


def create_app(root=".", preload=None):
    root = os.path.abspath(root)
    app = FastAPI(title="DFM Analyzer")
    jobs = JobManager(root)

    def part_or_404(part_id):
        if os.path.basename(part_id) != part_id or part_id in ("", ".", ".."):
            raise HTTPException(status_code=404, detail="unknown part")
        part = parts_api.part_info(root, part_id)
        if part is None:
            raise HTTPException(status_code=404, detail="unknown part")
        return part

    def binary(request: Request, data: bytes, tag_path: str):
        """Raw typed-array response with a cheap mtime ETag revalidation."""
        try:
            etag = f'"{os.path.getmtime(tag_path):.6f}-{len(data)}"'
        except OSError:
            etag = f'"{len(data)}"'
        headers = {"ETag": etag, "Cache-Control": "no-cache"}
        if request.headers.get("if-none-match") == etag:
            return Response(status_code=304, headers=headers)
        return Response(content=data, media_type="application/octet-stream",
                        headers=headers)

    @app.get("/api/config")
    def get_config():
        return {"preload": preload}

    @app.get("/api/processes")
    def get_processes():
        return processes.catalog()

    @app.get("/api/parts")
    def get_parts():
        return parts_api.list_parts(root)

    @app.post("/api/parts", status_code=201)
    async def upload_part(file: UploadFile):
        data = await file.read()
        try:
            return parts_api.create_part(root, file.filename, data)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))

    @app.get("/api/parts/{part_id}")
    def get_part(part_id: str):
        return part_or_404(part_id)

    @app.get("/api/parts/{part_id}/manifest")
    def get_manifest(part_id: str):
        return manifest_api.build_manifest(root, part_or_404(part_id))

    @app.get("/api/parts/{part_id}/mesh/{which}")
    def get_mesh(request: Request, part_id: str, which: str):
        part = part_or_404(part_id)
        workdir = parts_api.workdir_for(root, part["id"])
        try:
            data, _ = fields_api.mesh_bytes(workdir, which)
        except FileNotFoundError:
            raise HTTPException(status_code=404, detail=f"no mesh array {which}")
        return binary(request, data,
                      os.path.join(workdir, pipeline.FINE_FACES_FILE))

    @app.get("/api/parts/{part_id}/fields/{file_stem}/{key}")
    def get_field(request: Request, part_id: str, file_stem: str, key: str):
        part = part_or_404(part_id)
        workdir = parts_api.workdir_for(root, part["id"])
        try:
            if file_stem == "accessibility":
                data, _ = fields_api.accessibility_bytes(workdir, int(key))
                tag_path = os.path.join(workdir, pipeline.ACCESSIBILITY_FILE)
            elif file_stem == "brep_faces":
                data, _ = fields_api.brep_faces_bytes(workdir)
                tag_path = os.path.join(workdir, pipeline.BREP_FACES_FILE)
            elif file_stem == "brep_edges":
                data, _ = fields_api.brep_edges_bytes(workdir)
                tag_path = os.path.join(workdir, pipeline.BREP_EDGES_FILE)
            elif file_stem == "brep_edge_pairs":
                data, _ = fields_api.brep_edge_pairs_bytes(workdir)
                tag_path = os.path.join(workdir, pipeline.BREP_EDGE_PAIRS_FILE)
            else:
                data, _ = fields_api.zcache_field_bytes(workdir, file_stem, key)
                tag_path = os.path.join(workdir, "zcache", f"{file_stem}.npz")
        except (FileNotFoundError, ValueError):
            raise HTTPException(status_code=404,
                                detail=f"no field {file_stem}/{key}")
        return binary(request, data, tag_path)

    def _overrides_path(part_id, process_id, analysis_id, result_hash):
        """Validated path of a result's assignment-overrides JSON."""
        import re
        part = part_or_404(part_id)
        if not (re.fullmatch(r"[0-9a-f]{12}", result_hash)
                and re.fullmatch(r"[a-z0-9_]+", process_id)
                and re.fullmatch(r"[a-z0-9_]+", analysis_id)):
            raise HTTPException(status_code=404, detail="unknown result")
        base = os.path.join(parts_api.workdir_for(root, part["id"]),
                            "results", process_id, analysis_id)
        if not os.path.exists(os.path.join(base, f"{result_hash}.json")):
            raise HTTPException(status_code=404, detail="unknown result")
        return os.path.join(base, f"{result_hash}_overrides.json")

    @app.get("/api/parts/{part_id}/results/{process_id}/{analysis_id}/{result_hash}/overrides")
    def get_overrides(part_id: str, process_id: str, analysis_id: str,
                      result_hash: str):
        path = _overrides_path(part_id, process_id, analysis_id, result_hash)
        if not os.path.exists(path):
            return {}
        import json as json_module
        with open(path) as f:
            return json_module.load(f)

    @app.put("/api/parts/{part_id}/results/{process_id}/{analysis_id}/{result_hash}/overrides")
    def put_overrides(part_id: str, process_id: str, analysis_id: str,
                      result_hash: str, body: dict):
        path = _overrides_path(part_id, process_id, analysis_id, result_hash)
        for option, faces in body.items():
            if not (str(option).isdigit() and isinstance(faces, dict)):
                raise HTTPException(status_code=400, detail="invalid overrides")
            for face_id, feature in faces.items():
                if not (str(face_id).isdigit() and isinstance(feature, int)
                        and 0 <= feature <= 253):
                    raise HTTPException(status_code=400, detail="invalid overrides")
        import json as json_module
        with open(path, "w") as f:
            json_module.dump(body, f)
        return {"ok": True}

    @app.post("/api/parts/{part_id}/ejector/simulate")
    def ejector_simulate(part_id: str, body: EjectorSimRequest):
        """Synchronous ejector-pin solve over cached arrays.

        Runs inline (no job queue): the compute is scipy over stored npz
        arrays only — the job worker serializes meshlib, which this never
        touches.
        """
        import re

        part = part_or_404(part_id)
        if not re.fullmatch(r"[0-9a-f]{12}", body.result_hash):
            raise HTTPException(status_code=404, detail="unknown result")
        if not 1 <= len(body.pins) <= 64:
            raise HTTPException(
                status_code=400,
                detail="between 1 and 64 pins (the viewer paints the "
                       "sticking field itself when there are none)")
        try:
            return ejector_api.simulate(
                parts_api.workdir_for(root, part["id"]), body.result_hash,
                [{"point": pin.point, "diameter": pin.diameter}
                 for pin in body.pins],
                E=body.E, allowable_pressure=body.allowable_pressure)
        except FileNotFoundError as error:
            raise HTTPException(status_code=404, detail=str(error))
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error))

    @app.get("/api/parts/{part_id}/results/{process_id}/{analysis_id}/{result_hash}/{key}")
    def get_result_field(request: Request, part_id: str, process_id: str,
                         analysis_id: str, result_hash: str, key: str):
        part = part_or_404(part_id)
        workdir = parts_api.workdir_for(root, part["id"])
        try:
            data, _ = fields_api.result_field_bytes(
                workdir, process_id, analysis_id, result_hash, key)
        except FileNotFoundError:
            raise HTTPException(status_code=404, detail="no such result field")
        tag_path = os.path.join(workdir, "results", process_id, analysis_id,
                                f"{result_hash}.npz")
        return binary(request, data, tag_path)

    @app.get("/api/parts/{part_id}/highlights")
    def get_highlights(part_id: str):
        part = part_or_404(part_id)
        path = os.path.join(parts_api.workdir_for(root, part["id"]),
                            pipeline.HIGHLIGHT_FILE)
        if not os.path.exists(path):
            raise HTTPException(status_code=404, detail="no highlights")
        return FileResponse(path, media_type="application/json")

    @app.post("/api/jobs", status_code=201)
    def submit_job(request: JobRequest):
        part_or_404(request.part_id)
        try:
            job = jobs.submit(request.part_id, request.process,
                              request.analysis, request.params)
        except PartBusyError as exc:
            raise HTTPException(status_code=409, detail=str(exc))
        except (KeyError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        return job.to_dict()

    @app.get("/api/jobs")
    def list_jobs(part_id: str = None):
        return [job.to_dict() for job in jobs.list(part_id)]

    @app.get("/api/jobs/{job_id}")
    def get_job(job_id: int):
        job = jobs.get(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="unknown job")
        return job.to_dict()

    if os.path.isdir(FRONTEND_DIST):
        app.mount("/", StaticFiles(directory=FRONTEND_DIST, html=True),
                  name="frontend")

    return app


def serve_app(root=".", preload=None, port=8080, open_browser=True, timeout=None):
    """Run the app with uvicorn; optionally open a browser and auto-stop."""
    import threading
    import webbrowser

    import uvicorn
    from loguru import logger

    if not os.path.isdir(FRONTEND_DIST):
        logger.warning(
            "frontend/dist not found — build the UI first: cd frontend && npm install && npm run build")

    application = create_app(root, preload=preload)
    config = uvicorn.Config(application, host="127.0.0.1", port=port, log_level="info")
    server = uvicorn.Server(config)

    url = f"http://localhost:{port}/"
    logger.info(f"Serving at {url}" + (f" for {timeout:.0f}s" if timeout else ""))
    if open_browser:
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    if timeout:
        threading.Timer(timeout, lambda: setattr(server, "should_exit", True)).start()
    server.run()


app = create_app(os.environ.get("ANALYZER_ROOT", "."))
