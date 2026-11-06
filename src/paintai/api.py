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
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response
from pydantic import Field
from starlette.concurrency import run_in_threadpool

from .config import Settings, load_settings
from .curriculum.spec import CurriculumSpec, direction_curriculum, load_spec
from .evolution import Evolution
from .experiments import ExperimentRequest
from .jobs import Jobs, QueueConflict, Worker
from .learning import LearningError
from .lora_reports import LoraReports
from .memory import Memory
from .model_presets import ark_all, bailian_seedream
from .model_settings import ModelEdit, ModelRevision, ModelSettings, RoleEdit
from .models import DirectionText, Model, Painter, PainterId, PracticeOptions, StyleTraits, Text
from .storage import Store
from .transfer import MAX_ARCHIVE, Transfer, TransferConflict, TransferError


class NewPainter(Model):
    growth_goal: DirectionText = ""
    name: Text = Field(max_length=60)
    style: Text = Field(default="watercolor painting", max_length=200)


class CreateRequest(Model):
    painter_id: PainterId
    topic: Text = Field(max_length=1500)


class GoalRequest(Model):
    growth_goal: DirectionText = ""


class TrainRequest(PracticeOptions):
    lesson_id: PainterId | None = None


class StyleRequest(Model):
    name: Text = Field(max_length=100)
    reference_ids: list[str] = Field(min_length=1, max_length=3)


class StyleActivation(Model):
    profile_id: str | None = None
    traits: StyleTraits | None = None
    name: Text | None = Field(default=None, max_length=100)


def public_creation(entry):
    fields = (
        "id",
        "painter_id",
        "topic",
        "created_at",
        "updated_at",
        "status",
        "plan",
        "critique",
        "simulated",
        "simulation",
        "learning",
        "memory_used",
        "art_brief",
        "experiment",
        "generation_constraints",
        "reference_conditioning",
        "lora_conditioning",
    )
    result = {key: entry.get(key) for key in fields}
    result["image_url"] = (
        f"/api/v1/painters/{entry['painter_id']}/creations/{entry['id']}/image"
        if entry.get("image_path")
        else None
    )
    result["can_evaluate"] = bool(entry.get("image_path") and entry.get("plan"))
    result["evaluation_history"] = [
        {key: attempt.get(key) for key in ("started_at", "finished_at", "status", "critique")}
        for attempt in entry.get("evaluation_attempts", [])
    ]
    return result


def create_app(settings: Settings | None = None, *, start_worker=True):
    settings = settings or load_settings(
        Path(os.environ.get("PAINTAI_CONFIG", "configs/local-full.toml"))
    )
    store = Store(settings.data_dir)
    model_settings = ModelSettings(store, settings)
    jobs = Jobs(store, settings, model_settings=model_settings)
    worker = Worker(jobs)
    learning, memory = jobs.learning, Memory(store)
    transfer = Transfer(store)

    @asynccontextmanager
    async def lifespan(app):
        if start_worker:
            worker.start()
        yield
        if start_worker:
            worker.stop()

    app = FastAPI(title="Paintai API", version="0.1.0", lifespan=lifespan)
    app.state.store, app.state.jobs, app.state.worker = store, jobs, worker
    origins = os.environ.get(
        "PAINTAI_WEB_ORIGINS", "http://127.0.0.1:5173,http://localhost:5173"
    ).split(",")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_methods=["GET", "POST"],
        allow_headers=["Content-Type", "Idempotency-Key"],
    )

    @app.middleware("http")
    async def check_origin(request: Request, call_next):
        if request.method == "POST" and request.headers.get("origin") not in [None, *origins]:
            return JSONResponse({"detail": "不允许此网页提交操作。"}, status_code=403)
        return await call_next(request)

    @app.exception_handler(QueueConflict)
    async def conflict(request, exc):
        return JSONResponse({"detail": str(exc)}, status_code=409)

    @app.exception_handler(RequestValidationError)
    async def invalid_input(request, exc):
        # Never echo submitted API keys (including malformed requests) back to the client.
        return JSONResponse(
            {
                "detail": [
                    {"loc": e["loc"], "msg": e["msg"], "type": e["type"]} for e in exc.errors()
                ]
            },
            status_code=422,
        )

    @app.get("/api/v1/model-settings")
    def get_models():
        return model_settings.public()

    @app.get("/api/v1/model-settings/presets/bailian-seedream")
    def recommended_models():
        return bailian_seedream()

    @app.get("/api/v1/model-settings/presets/ark-all")
    def ark_models():
        return ark_all()

    @app.post("/api/v1/model-settings")
    def save_models(body: ModelEdit):
        return model_settings.save(body)

    @app.post("/api/v1/model-settings/reset")
    def reset_models(body: ModelRevision):
        return model_settings.reset(body.revision)

    @app.post("/api/v1/model-settings/{role}/check")
    def check_model(role: Literal["planner", "generator", "critic"], body: RoleEdit):
        return model_settings.check(role, body)

    @app.exception_handler(LearningError)
    async def invalid_learning(request, exc):
        return JSONResponse({"detail": str(exc)}, status_code=409)

    @app.exception_handler(ValueError)
    async def missing(request, exc):
        return JSONResponse(
            {"detail": "请求的画师、作品或任务不存在，或尚不可评价。"}, status_code=404
        )

    def job_response(job):
        result = {
            key: job[key]
            for key in (
                "id",
                "painter_id",
                "kind",
                "topic",
                "entry_id",
                "status",
                "error",
                "created_at",
                "updated_at",
            )
        }
        result["stage"] = job["status"]
        result["training"] = None
        if job["kind"] == "reference_review":
            payload = json.loads(job["payload"])
            record = next(
                (
                    r
                    for r in jobs.experiments.list(job["painter_id"])
                    if r["id"] == payload["experiment_id"]
                ),
                {},
            )
            review = next(
                (r for r in record.get("reference_reviews", []) if r["id"] == job["id"]), {}
            )
            result["training"] = {
                "name": "参考图对照评价",
                "total": len(payload["works"]) * 5,
                "completed": sum(len(r["dimensions"]) for r in review.get("results", [])),
            }
        if job["kind"] == "experiment":
            payload = json.loads(job["payload"])
            record = next(
                (r for r in jobs.experiments.list(job["painter_id"]) if r["id"] == job["id"]), None
            )
            result["training"] = {
                "name": "成长对照实验",
                "total": payload["pairs"] * 2,
                "completed": len(record["results"]) if record else 0,
            }
        if job["kind"] == "train":
            payload = json.loads(job["payload"])
            record = next(
                (r for r in learning.rounds(job["painter_id"]) if r["id"] == job["id"]), None
            )
            result["training"] = {
                "name": payload["name"],
                "total": len(payload["tasks"]),
                "completed": len(record["results"]) if record else 0,
            }
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
        active_settings = model_settings.current()
        roles = {}
        with httpx.Client(timeout=2) as client:
            for role in ("planner", "generator", "critic"):
                config = getattr(active_settings, role)
                ready = True
                if config.provider == "sd_cpp":
                    ready = (
                        config.local_image.executable.is_file()
                        and config.local_image.model_path.is_file()
                    )
                elif config.provider == "openai_compatible":
                    try:
                        key = config.credential()
                        response = client.get(
                            config.base_url.rstrip("/") + "/models",
                            headers={"Authorization": f"Bearer {key}"} if key else {},
                        )
                        # Some image-only compatible APIs do not implement model discovery.
                        ready = response.is_success or response.status_code in (404, 405)
                    except (httpx.HTTPError, ValueError):
                        ready = False
                roles[role] = {"ready": ready, "simulated": config.provider == "mock"}
        return {
            "roles": roles,
            "ready": all(r["ready"] for r in roles.values()),
            "simulated": any(r["simulated"] for r in roles.values()),
        }

    @app.get("/api/v1/painters")
    def list_painters():
        return store.painters()

    @app.exception_handler(TransferError)
    async def transfer_error(request, exc):
        return JSONResponse(
            {"detail": str(exc)}, status_code=409 if isinstance(exc, TransferConflict) else 400
        )

    @app.get("/api/v1/painters/{painter_id}/export")
    def export_painter(painter_id: str):
        raw = transfer.export(painter_id)
        return Response(
            raw,
            media_type="application/zip",
            headers={
                "Content-Disposition": f'attachment; filename="paintai-{painter_id}.zip"',
                "Cache-Control": "no-store",
            },
        )

    @app.post("/api/v1/painters/import", status_code=201)
    async def import_painter(request: Request):
        raw = bytearray()
        async for chunk in request.stream():
            if len(raw) + len(chunk) > MAX_ARCHIVE:
                raise HTTPException(413, "迁移包不能超过 256 MB。")
            raw.extend(chunk)
        return await run_in_threadpool(transfer.restore, bytes(raw))

    @app.get("/api/v1/curricula/default")
    def default_curriculum():
        return load_spec().model_dump()

    @app.get("/api/v1/painters/{painter_id}/learning")
    def progress(painter_id: str):
        return learning.progress(painter_id)

    @app.get("/api/v1/painters/{painter_id}/styles")
    def styles(painter_id: str):
        return jobs.styles.list(painter_id)

    @app.post("/api/v1/painters/{painter_id}/references", status_code=201)
    async def upload_reference(painter_id: str, request: Request):
        raw = bytearray()
        async for chunk in request.stream():
            raw.extend(chunk)
            if len(raw) > 10 * 1024 * 1024:
                raise HTTPException(413, "每张参考图不能超过 10 MB。")
        return jobs.styles.upload(painter_id, bytes(raw))

    @app.get("/api/v1/painters/{painter_id}/references/{reference_id}/image")
    def reference_image(painter_id: str, reference_id: str):
        return FileResponse(jobs.styles.path(painter_id, reference_id), media_type="image/png")

    @app.post("/api/v1/painters/{painter_id}/styles/analyze", status_code=202)
    def analyze_style(
        painter_id: str,
        body: StyleRequest,
        idempotency_key: str = Header(min_length=8, max_length=100),
    ):
        return job_response(
            jobs.submit(
                painter_id, "style", body.name, idempotency_key, style_request=body.model_dump()
            )
        )

    @app.post("/api/v1/painters/{painter_id}/styles/activate")
    def activate_style(painter_id: str, body: StyleActivation):
        return jobs.styles.activate(painter_id, body.profile_id, body.traits, body.name)

    @app.post("/api/v1/painters/{painter_id}/curriculum")
    def assign_curriculum(painter_id: str, body: CurriculumSpec):
        return learning.assign(painter_id, body)

    @app.post("/api/v1/painters/{painter_id}/train", status_code=202)
    def train(
        painter_id: str,
        body: TrainRequest,
        idempotency_key: str = Header(min_length=8, max_length=100),
    ):
        return job_response(
            jobs.submit(
                painter_id,
                "train",
                body.lesson_id or "",
                idempotency_key,
                options=PracticeOptions.model_validate(body.model_dump(exclude={"lesson_id"})),
            )
        )

    @app.post("/api/v1/painters/{painter_id}/goal")
    def update_goal(painter_id: str, body: GoalRequest):
        return store.update_goal(painter_id, body.growth_goal)

    @app.post("/api/v1/painters/{painter_id}/curriculum/preview")
    def preview_curriculum(painter_id: str):
        return direction_curriculum(store.painter(painter_id).growth_goal)

    @app.post("/api/v1/painters/{painter_id}/train/preview")
    def preview_training(painter_id: str, body: TrainRequest):
        return learning.snapshot(
            painter_id,
            body.lesson_id,
            PracticeOptions.model_validate(body.model_dump(exclude={"lesson_id"})),
        )

    @app.get("/api/v1/painters/{painter_id}/memory")
    def list_memory(painter_id: str):
        return {"items": memory.list(painter_id), "retrieval": "local_text_skills_v1"}

    @app.get("/api/v1/painters/{painter_id}/distilled")
    def distilled(painter_id: str):
        return {"items": Evolution(store).list(painter_id)}

    @app.post("/api/v1/painters/{painter_id}/distilled")
    def distill(painter_id: str):
        if any(j["status"] in ("queued", "running") for j in jobs.list(painter_id)):
            raise QueueConflict("请等当前任务完成后再蒸馏经验。")
        return Evolution(store).distill(painter_id)

    @app.get("/api/v1/painters/{painter_id}/experiments")
    def experiments(painter_id: str):
        return {"items": jobs.experiments.list(painter_id)}

    @app.get("/api/v1/painters/{painter_id}/lora-reports")
    def lora_reports(painter_id: str):
        return {"items": LoraReports(store).list(painter_id)}

    @app.get("/api/v1/painters/{painter_id}/lora-reports/{report_id}/images/{image_id}")
    def lora_report_image(painter_id: str, report_id: str, image_id: str):
        return FileResponse(
            LoraReports(store).image(painter_id, report_id, image_id), media_type="image/png"
        )

    @app.post("/api/v1/painters/{painter_id}/experiments/preview")
    def preview_experiment(painter_id: str, body: ExperimentRequest):
        return jobs.experiments.snapshot(painter_id, body, model_settings.current())

    @app.post("/api/v1/painters/{painter_id}/experiments", status_code=202)
    def experiment(
        painter_id: str,
        body: ExperimentRequest,
        idempotency_key: str = Header(min_length=8, max_length=100),
    ):
        return job_response(
            jobs.submit(painter_id, "experiment", body.topic, idempotency_key, experiment=body)
        )

    @app.post("/api/v1/painters/{painter_id}/memory/sync")
    def sync_memory(painter_id: str):
        if any(j["status"] in ("queued", "running") for j in jobs.list(painter_id)):
            raise QueueConflict("请等当前任务完成后再整理记忆。")
        return {"processed": memory.sync(painter_id)}

    @app.post(
        "/api/v1/painters/{painter_id}/experiments/{experiment_id}/reference-review",
        status_code=202,
    )
    def reference_review(
        painter_id: str,
        experiment_id: str,
        idempotency_key: str = Header(min_length=8, max_length=100),
    ):
        return job_response(
            jobs.submit(painter_id, "reference_review", experiment_id, idempotency_key)
        )

    @app.post("/api/v1/painters", status_code=201)
    def add_painter(body: NewPainter):
        return store.add_painter(
            Painter(
                painter_id="painter_" + uuid4().hex[:12],
                name=body.name,
                style=body.style,
                growth_goal=body.growth_goal,
            )
        )

    @app.get("/api/v1/painters/{painter_id}/creations")
    def gallery(
        painter_id: str,
        limit: int = Query(60, ge=1, le=200),
        offset: int = Query(0, ge=0),
        status: Literal["all", "complete", "failed"] = "all",
    ):
        store.painter(painter_id)
        condition = "painter_id=?"
        if status == "complete":
            condition += " AND status='complete'"
        elif status == "failed":
            condition += " AND status IN ('creation_failed','evaluation_failed')"
        with store.connect() as db:
            total = db.execute(
                f"SELECT COUNT(*) FROM creations WHERE {condition}", (painter_id,)
            ).fetchone()[0]
            rows = db.execute(
                f"SELECT payload FROM creations WHERE {condition} ORDER BY created_at DESC LIMIT ? OFFSET ?",
                (painter_id, limit, offset),
            ).fetchall()
        return {
            "items": [public_creation(json.loads(row["payload"])) for row in rows],
            "total": total,
        }

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
        return FileResponse(
            path,
            media_type="image/png",
            filename=f"paintai-{entry_id}.png",
            content_disposition_type="inline",
        )

    @app.post("/api/v1/creations", status_code=202)
    def create(body: CreateRequest, idempotency_key: str = Header(min_length=8, max_length=100)):
        return job_response(jobs.submit(body.painter_id, "create", body.topic, idempotency_key))

    @app.post("/api/v1/painters/{painter_id}/creations/{entry_id}/evaluate", status_code=202)
    def evaluate(
        painter_id: str, entry_id: str, idempotency_key: str = Header(min_length=8, max_length=100)
    ):
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
