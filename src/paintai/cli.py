import argparse
import json
import sys
import time
from pathlib import Path
from uuid import uuid4

import httpx
from pydantic import ValidationError

from .agent import CreationFailed, PainterAgent
from .config import load_settings
from .curriculum.spec import load_spec
from .jobs import Jobs, Worker
from .learning import Learning
from .memory import Memory
from .models import Painter
from .providers import make_provider
from .storage import Store


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(
        prog="paintai", description="Paintai M0: create → critique → gallery"
    )
    result.add_argument("--config", type=Path)
    result.add_argument(
        "--offline", action="store_true", help="Explicit simulated pipeline; no API calls"
    )
    result.add_argument("--data-dir", type=Path)
    commands = result.add_subparsers(dest="command", required=True)
    painter = commands.add_parser("painter").add_subparsers(dest="action", required=True)
    add = painter.add_parser("add")
    add.add_argument("painter_id")
    add.add_argument("--name", required=True)
    add.add_argument("--style", default="自由风格")
    painter.add_parser("list")
    create = commands.add_parser("create")
    create.add_argument("topic")
    create.add_argument("--painter", required=True)
    train = commands.add_parser('train', help='Run bounded three-exercise lesson rounds')
    train.add_argument('--painter', required=True)
    train.add_argument('--spec', help='default_curriculum or path to YAML')
    train.add_argument('--lessons', type=int, default=1)
    train.add_argument('--lesson', help='Repeat an unlocked lesson by stable ID')
    progress = commands.add_parser('progress')
    progress.add_argument('--painter', required=True)
    memory = commands.add_parser('memory')
    memory.add_argument('--painter', required=True)
    memory.add_argument('--sync', action='store_true')
    gallery = commands.add_parser("gallery")
    gallery.add_argument("--painter", required=True)
    gallery.add_argument("--limit", type=int, default=20)
    for name in ("show", "evaluate"):
        command = commands.add_parser(name)
        command.add_argument("entry_id")
        command.add_argument("--painter", required=True)
        if name == "evaluate":
            command.add_argument(
                "--force",
                action="store_true",
                help="Re-evaluate a completed image, preserving score history",
            )
    return result


def emit(value, *, error=False):
    print(json.dumps(value, ensure_ascii=False, indent=2), file=sys.stderr if error else sys.stdout)


def main(argv=None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    args = parser().parse_args(argv)
    try:
        settings = load_settings(args.config, offline=args.offline, data_dir=args.data_dir)
        store = Store(settings.data_dir)
        if args.command == "painter":
            value = (
                store.add_painter(
                    Painter(painter_id=args.painter_id, name=args.name, style=args.style)
                ).model_dump()
                if args.action == "add"
                else store.painters()
            )
        elif args.command == "gallery":
            if not 1 <= args.limit <= 1000:
                raise ValueError("limit must be between 1 and 1000")
            value = store.gallery(args.painter, args.limit)
        elif args.command == "show":
            value = store.get(args.painter, args.entry_id)
        elif args.command == 'progress':
            value = Learning(store).progress(args.painter)
        elif args.command == 'memory':
            memory = Memory(store)
            if args.sync:
                memory.sync(args.painter)
            value = memory.list(args.painter)
        elif args.command == 'train':
            if not 1 <= args.lessons <= 12:
                raise ValueError('lessons must be between 1 and 12')
            jobs = Jobs(store, settings)
            worker = Worker(jobs)
            worker.start()  # Same ownership lock as HTTP API; never run two trainers.
            try:
                if args.spec:
                    jobs.learning.assign(args.painter, load_spec(
                        None if args.spec == 'default_curriculum' else Path(args.spec)))
                for _ in range(args.lessons):
                    before = jobs.learning.progress(args.painter)
                    if not before['current'] and not args.lesson:
                        break
                    job = jobs.submit(args.painter, 'train', args.lesson or '', uuid4().hex)
                    while jobs.get(job['id'])['status'] in ('queued', 'running'):
                        time.sleep(0.2)
                    if jobs.get(job['id'])['status'] != 'succeeded':
                        raise ValueError(jobs.get(job['id'])['error'])
                    after = jobs.learning.progress(args.painter)
                    if after['completed'] == before['completed']:
                        break  # No unbounded retries of failed/simulated lessons.
                value = jobs.learning.progress(args.painter)
            finally:
                worker.stop()
        else:
            # HTTPX has no automatic request retries here; paid calls are explicit.
            with httpx.Client() as client:
                agent = PainterAgent(
                    store,
                    settings,
                    *(
                        make_provider(getattr(settings, role), client)
                        for role in ("planner", "generator", "critic")
                    ),
                )
                value = (
                    agent.create(args.topic, args.painter)
                    if args.command == "create"
                    else agent.evaluate(args.painter, args.entry_id, force=args.force)
                )
        if isinstance(value, dict) and value.get("image_path"):
            value = {**value, "image_absolute_path": str(store.image_path(value["image_path"]))}
        emit(value)
        return 0
    except CreationFailed as exc:
        emit(
            {
                "error": str(exc),
                "entry_id": exc.entry["id"],
                "status": exc.entry["status"],
                "image_path": exc.entry["image_path"],
            },
            error=True,
        )
        return 1
    except ValidationError as exc:
        emit(
            {
                "error": "Validation failed",
                "fields": [".".join(map(str, e["loc"])) for e in exc.errors()],
            },
            error=True,
        )
        return 2
    except ValueError as exc:
        emit({"error": str(exc)}, error=True)
        return 2
    except Exception as exc:
        emit({"error": f"Local operation failed: {type(exc).__name__}"}, error=True)
        return 1
