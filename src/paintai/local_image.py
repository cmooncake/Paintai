"""Local CPU inference through stable-diffusion.cpp; no shell or network calls."""

import hashlib
import json
import math
import os
import re
import subprocess
from uuid import uuid4

from .config import ProviderConfig
from .models import Plan
from .prompt_guard import anime_tags, negative_constraints, portrait_locks
from .providers import MAX_IMAGE_BYTES, ProviderError


class LocalImageGenerator:
    def __init__(self, config: ProviderConfig):
        self.config = config
        self.reference = None
        self.reference_metadata = None
        self.lora = None
        self.lora_metadata = None
        self.inpaint = None

    def set_lora(self, path, digest, strength=1.0):
        """Explicit opt-in for a verified local adapter; never changes the base model."""
        if not math.isfinite(strength) or not 0 < strength <= 1:
            raise ProviderError("LoRA strength must be in (0, 1]")
        if path.suffix != ".safetensors" or not re.fullmatch(r"[A-Za-z0-9_-]+", path.stem):
            raise ProviderError("LoRA requires a simple safetensors filename")
        if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            raise ProviderError("LoRA file missing or hash mismatch")
        self.lora = path.resolve()
        self.lora_metadata = {
            "method": "lora",
            "name": path.stem,
            "sha256": digest,
            "strength": strength,
        }

    def set_reference(self, path, digest, strength, reference_id):
        local = self.config.local_image
        if not local or not local.ip_adapter_path or not local.clip_vision_path:
            raise ProviderError("Configure IP-Adapter and CLIP-Vision weights first")
        if not 0 < strength <= 1:
            raise ProviderError("Reference strength must be in (0, 1]")
        if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            raise ProviderError("Reference image changed since experiment preview")
        self.reference = path
        self.reference_metadata = {
            "method": "ip-adapter-plus-sd15",
            "reference_id": reference_id,
            "sha256": digest,
            "strength": strength,
        }

    def prepare(self, plan: Plan):
        locks = portrait_locks(plan.prompt.splitlines()[0])
        result = plan.model_copy(deep=True)
        if locks:
            weights = {
                "hair": 1.25,
                "eyes": 1.5,
                "clothing": 1.4,
                "background": 1.25,
                "framing": 1.3,
            }
            anchors = [f"({text}:{weights[key]})" for key, text in locks.items()]
            if locks.get("eyes", "").lower() == "amber eyes":
                anchors.append("(golden orange irises:1.3)")
            if "ivory blouse" in locks.get("clothing", "").lower():
                anchors.append("(white blouse:1.3)")
            result.prompt = ", ".join(anchors) + "\n" + plan.prompt
        if "animagine" in self.config.model.lower():
            subject, *techniques = plan.prompt.splitlines()
            subject = anime_tags(subject)
            for key in ("framing", "hair", "clothing"):
                if key in locks:
                    original = anime_tags(locks[key])
                    subject = subject.replace(original, f"({original}:1.3)")
            result.prompt = subject + (", " + ", ".join(techniques) if techniques else "")
        suffix = self.config.local_image.prompt_suffix.strip()
        if suffix and not result.prompt.rstrip().endswith(suffix):
            result.prompt = result.prompt.rstrip() + ", " + suffix
        negative = ", ".join(
            filter(None, [self.config.local_image.negative_prompt, negative_constraints(locks)])
        )
        return result, {
            "version": "portrait-attributes-v3",
            "protected_attributes": locks,
            "negative_prompt": negative,
            "model_prompt_suffix": suffix,
        }

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
        runtime_prompt = plan.prompt
        if self.lora is not None:
            if hashlib.sha256(self.lora.read_bytes()).hexdigest() != self.lora_metadata["sha256"]:
                raise ProviderError("LoRA file changed")
            runtime_prompt += f"\n<lora:{self.lora.stem}:{self.lora_metadata['strength']}>"
        prompt.write_text(runtime_prompt, encoding="utf-8")
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
            "--clip-skip",
            str(local.clip_skip),
            "--negative-prompt",
            ", ".join(
                filter(
                    None,
                    [
                        local.negative_prompt,
                        negative_constraints(portrait_locks(plan.prompt.splitlines()[0])),
                    ],
                )
            ),
        ]
        if self.inpaint is not None:
            init, mask, strength = self.inpaint
            command += ["--init-img", str(init), "--mask", str(mask), "--strength", str(strength)]
        if self.lora is not None:
            command += ["--lora-model-dir", str(self.lora.parent)]
        if self.reference is not None:
            if (
                hashlib.sha256(self.reference.read_bytes()).hexdigest()
                != self.reference_metadata["sha256"]
            ):
                raise ProviderError("Reference image changed")
            if not local.ip_adapter_path.is_file() or not local.clip_vision_path.is_file():
                raise ProviderError(
                    "IP-Adapter weights missing; run scripts/setup-reference-adapter.py"
                )
            command += [
                "--ip-adapter",
                str(local.ip_adapter_path.resolve()),
                "--clip_vision",
                str(local.clip_vision_path.resolve()),
                "--ip-adapter-image",
                str(self.reference.resolve()),
                "--ip-adapter-strength",
                str(self.reference_metadata["strength"]),
            ]
        (run_dir / "invocation.json").write_text(
            json.dumps(
                {
                    "command": command,
                    "reference": self.reference_metadata,
                    "lora": self.lora_metadata,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
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
