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
from api.jobs import JobManager, PartBusyError
from api.schemas import JobRequest
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
        workdir = os.path.join(root, part["id"])
        try:
            data, _ = fields_api.mesh_bytes(workdir, which)
        except FileNotFoundError:
            raise HTTPException(status_code=404, detail=f"no mesh array {which}")
        return binary(request, data,
                      os.path.join(workdir, pipeline.FINE_FACES_FILE))

    @app.get("/api/parts/{part_id}/fields/{file_stem}/{key}")
    def get_field(request: Request, part_id: str, file_stem: str, key: str):
        part = part_or_404(part_id)
        workdir = os.path.join(root, part["id"])
        try:
            if file_stem == "accessibility":
                data, _ = fields_api.accessibility_bytes(workdir, int(key))
                tag_path = os.path.join(workdir, pipeline.ACCESSIBILITY_FILE)
            else:
                data, _ = fields_api.zcache_field_bytes(workdir, file_stem, key)
                tag_path = os.path.join(workdir, "zcache", f"{file_stem}.npz")
        except (FileNotFoundError, ValueError):
            raise HTTPException(status_code=404,
                                detail=f"no field {file_stem}/{key}")
        return binary(request, data, tag_path)

    @app.get("/api/parts/{part_id}/results/{process_id}/{analysis_id}/{result_hash}/{key}")
    def get_result_field(request: Request, part_id: str, process_id: str,
                         analysis_id: str, result_hash: str, key: str):
        part = part_or_404(part_id)
        workdir = os.path.join(root, part["id"])
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
        path = os.path.join(root, part["id"], pipeline.HIGHLIGHT_FILE)
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
