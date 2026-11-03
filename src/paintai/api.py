"""Standalone HTTP API. No frontend assets, model secrets or filesystem browsing."""
import argparse
import json
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal
from uuid import uuid4

import httpx
from fastapi import FastAPI, Header, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from pydantic import Field

from .config import Settings, load_settings
from .jobs import Jobs, QueueConflict, Worker
from .models import Model, Painter, PainterId, Text
from .storage import Store


class NewPainter(Model):
    name: Text = Field(max_length=60)
    style: Text = Field(default="watercolor painting", max_length=200)


class CreateRequest(Model):
    painter_id: PainterId
    topic: Text = Field(max_length=1500)


def public_creation(entry):
    fields = ("id", "painter_id", "topic", "created_at", "updated_at", "status", "plan",
              "critique", "simulated", "simulation")
    result = {key: entry.get(key) for key in fields}
    result["image_url"] = (f"/api/v1/painters/{entry['painter_id']}/creations/{entry['id']}/image"
                           if entry.get("image_path") else None)
    result["can_evaluate"] = bool(entry.get("image_path") and entry.get("plan"))
    result["evaluation_history"] = [
        {key: attempt.get(key) for key in ("started_at", "finished_at", "status", "critique")}
        for attempt in entry.get("evaluation_attempts", [])
    ]
    return result


def create_app(settings: Settings | None = None, *, start_worker=True):
    settings = settings or load_settings(Path(os.environ.get("PAINTAI_CONFIG", "configs/local-full.toml")))
    store = Store(settings.data_dir)
    jobs = Jobs(store, settings)
    worker = Worker(jobs)

    @asynccontextmanager
    async def lifespan(app):
        if start_worker:
            worker.start()
        yield
        if start_worker:
            worker.stop()

    app = FastAPI(title="Paintai API", version="0.1.0", lifespan=lifespan)
    app.state.store, app.state.jobs, app.state.worker = store, jobs, worker
    origins = os.environ.get("PAINTAI_WEB_ORIGINS", "http://127.0.0.1:5173,http://localhost:5173").split(",")
    app.add_middleware(CORSMiddleware, allow_origins=origins,
                       allow_methods=["GET", "POST"], allow_headers=["Content-Type", "Idempotency-Key"])

    @app.middleware("http")
    async def check_origin(request: Request, call_next):
        if request.method == "POST" and request.headers.get("origin") not in [None, *origins]:
            return JSONResponse({"detail": "不允许此网页提交操作。"}, status_code=403)
        return await call_next(request)

    @app.exception_handler(QueueConflict)
    async def conflict(request, exc):
        return JSONResponse({"detail": str(exc)}, status_code=409)

    @app.exception_handler(ValueError)
    async def missing(request, exc):
        return JSONResponse({"detail": "请求的画师、作品或任务不存在，或尚不可评价。"}, status_code=404)

    def job_response(job):
        result = {key: job[key] for key in ("id", "painter_id", "kind", "topic", "entry_id", "status",
                                          "error", "created_at", "updated_at")}
        result["stage"] = job["status"]
        if job["entry_id"] and job["status"] == "running":
            try:
                result["stage"] = store.get(job["painter_id"], job["entry_id"])["status"]
            except ValueError:
                pass
        return result

    @app.get("/api/v1/health")
    def health():
        return {"status": "ok"}

    @app.get("/api/v1/system")
    def system():
        roles = {}
        with httpx.Client(timeout=2) as client:
            for role in ("planner", "generator", "critic"):
                config = getattr(settings, role)
                ready = True
                if config.provider == "sd_cpp":
                    ready = config.local_image.executable.is_file() and config.local_image.model_path.is_file()
                elif config.provider == "openai_compatible":
                    try:
                        key = config.credential()
                        response = client.get(config.base_url.rstrip("/") + "/models",
                                              headers={"Authorization": f"Bearer {key}"} if key else {})
                        ready = response.is_success
                    except (httpx.HTTPError, ValueError):
                        ready = False
                roles[role] = {"ready": ready, "simulated": config.provider == "mock"}
        return {"roles": roles, "ready": all(r["ready"] for r in roles.values()),
                "simulated": any(r["simulated"] for r in roles.values())}

    @app.get("/api/v1/painters")
    def list_painters():
        return store.painters()

    @app.post("/api/v1/painters", status_code=201)
    def add_painter(body: NewPainter):
        return store.add_painter(Painter(painter_id="painter_" + uuid4().hex[:12],
                                         name=body.name, style=body.style))

    @app.get("/api/v1/painters/{painter_id}/creations")
    def gallery(painter_id: str, limit: int = Query(60, ge=1, le=200),
                offset: int = Query(0, ge=0), status: Literal['all', 'complete', 'failed'] = 'all'):
        store.painter(painter_id)
        condition = "painter_id=?"
        if status == 'complete':
            condition += " AND status='complete'"
        elif status == 'failed':
            condition += " AND status IN ('creation_failed','evaluation_failed')"
        with store.connect() as db:
            total = db.execute(f"SELECT COUNT(*) FROM creations WHERE {condition}", (painter_id,)).fetchone()[0]
            rows = db.execute(f"SELECT payload FROM creations WHERE {condition} ORDER BY created_at DESC LIMIT ? OFFSET ?",
                              (painter_id, limit, offset)).fetchall()
        return {"items": [public_creation(json.loads(row["payload"])) for row in rows], "total": total}

    @app.get("/api/v1/painters/{painter_id}/creations/{entry_id}")
    def creation(painter_id: str, entry_id: str):
        return public_creation(store.get(painter_id, entry_id))

    @app.get("/api/v1/painters/{painter_id}/creations/{entry_id}/image")
    def image(painter_id: str, entry_id: str):
        entry = store.get(painter_id, entry_id)
        if not entry.get("image_path"):
            raise HTTPException(404, "图片尚未生成。")
        path = store.image_path(entry["image_path"])
        if not path.is_file():
            raise HTTPException(404, "图片文件暂不可用。")
        return FileResponse(path, media_type="image/png", filename=f"paintai-{entry_id}.png",
                            content_disposition_type="inline")

    @app.post("/api/v1/creations", status_code=202)
    def create(body: CreateRequest, idempotency_key: str = Header(min_length=8, max_length=100)):
        return job_response(jobs.submit(body.painter_id, "create", body.topic, idempotency_key))

    @app.post("/api/v1/painters/{painter_id}/creations/{entry_id}/evaluate", status_code=202)
    def evaluate(painter_id: str, entry_id: str,
                 idempotency_key: str = Header(min_length=8, max_length=100)):
        return job_response(jobs.submit(painter_id, "evaluate", "", idempotency_key, entry_id))

    @app.get("/api/v1/jobs")
    def list_jobs(painter_id: str | None = None):
        return [job_response(job) for job in jobs.list(painter_id)]

    @app.get("/api/v1/jobs/{job_id}")
    def get_job(job_id: str):
        return job_response(jobs.get(job_id))

    return app


def main():
    import uvicorn
    parser = argparse.ArgumentParser(description="Paintai standalone API")
    parser.add_argument("--config", type=Path, default=Path("configs/local-full.toml"))
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    uvicorn.run(create_app(load_settings(args.config)), host="127.0.0.1", port=args.port)


if __name__ == "__main__":
    main()
