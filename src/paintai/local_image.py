"""Local CPU inference through stable-diffusion.cpp; no shell or network calls."""

import os
import subprocess
from uuid import uuid4

from .config import ProviderConfig
from .models import Plan
from .providers import MAX_IMAGE_BYTES, ProviderError


class LocalImageGenerator:
    def __init__(self, config: ProviderConfig):
        self.config = config

    def generate(self, plan: Plan) -> bytes:
        local = self.config.local_image
        if local is None:
            raise ProviderError("Missing local image configuration")
        if not local.executable.is_file() or not local.model_path.is_file():
            raise ProviderError(
                "Local image runtime/model missing; run scripts/setup-local-image.ps1"
            )
        run_dir = local.work_dir.resolve() / uuid4().hex
        run_dir.mkdir(parents=True, exist_ok=False)
        prompt = run_dir / "prompt.txt"
        output = run_dir / "image.png"
        log = run_dir / "generation.log"
        prompt.write_text(plan.prompt, encoding="utf-8")
        command = [
            str(local.executable.resolve()),
            "-m",
            str(local.model_path.resolve()),
            "--prompt-file",
            str(prompt),
            "-o",
            str(output),
            "--width",
            str(local.width),
            "--height",
            str(local.height),
            "--steps",
            str(local.steps),
            "--cfg-scale",
            str(local.cfg_scale),
            "--sampling-method",
            local.sampling_method,
            "--scheduler",
            local.scheduler,
            "--seed",
            str(local.seed),
            "--threads",
            str(local.threads),
            "--backend",
            "cpu",
            "--batch-count",
            "1",
        ]
        try:
            with log.open("wb") as handle:
                result = subprocess.run(
                    command,
                    cwd=run_dir,
                    stdout=handle,
                    stderr=subprocess.STDOUT,
                    stdin=subprocess.DEVNULL,
                    shell=False,
                    timeout=self.config.timeout_seconds,
                    creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
                )
        except subprocess.TimeoutExpired:
            raise ProviderError(f"Local generation timed out; log: {log}") from None
        except OSError:
            raise ProviderError(f"Unable to launch local image runtime; log: {log}") from None
        if result.returncode != 0 or not output.is_file():
            raise ProviderError(f"Local generation failed (exit {result.returncode}); log: {log}")
        if output.stat().st_size > MAX_IMAGE_BYTES:
            raise ProviderError("Generated image exceeds 20 MiB")
        return output.read_bytes()
