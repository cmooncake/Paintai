import argparse
import json
import sys
from pathlib import Path

import httpx
from pydantic import ValidationError

from .agent import CreationFailed, PainterAgent
from .config import load_settings
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
