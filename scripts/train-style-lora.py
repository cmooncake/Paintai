"""Bounded CPU SD1.5/SDXL LoRA training. Never changes the active painter/model.

Uses existing checkpoints and references; downloads only pinned configs/tokenizers.
Outputs actual trained attention adapters, provenance, losses and a reload check.
"""

import argparse
import hashlib
import json
import math
import os
import platform
import time
from importlib.metadata import version
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
os.environ.setdefault("HF_HOME", str(ROOT / "data/lora-cache"))
os.environ.setdefault("HF_HUB_DISABLE_IMPLICIT_TOKEN", "1")


def digest(path):
    with Path(path).open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def region_weights(regions, width, height):
    """Post-crop normalized rectangles; max weighting avoids double-counting overlaps."""
    grid = [[1.0 for _ in range(width)] for _ in range(height)]
    for region in regions:
        x0, y0, x1, y1 = region["box"]
        weight = region["weight"]
        if not (0 <= x0 < x1 <= 1 and 0 <= y0 < y1 <= 1 and 1 <= weight <= 8):
            raise ValueError("Invalid loss region")
        for y in range(height):
            for x in range(width):
                if x0 <= (x + 0.5) / width < x1 and y0 <= (y + 0.5) / height < y1:
                    grid[y][x] = max(grid[y][x], weight)
    return grid


def main():
    source_bytes = Path(__file__).read_bytes()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--architecture", choices=["sd15", "sdxl"], default="sd15")
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--steps", type=int, default=12)
    parser.add_argument("--resolution", type=int, choices=[256, 384, 512], default=256)
    parser.add_argument("--threads", type=int, default=6)
    parser.add_argument("--resume-training", type=Path)
    parser.add_argument("--learning-rate", type=float, default=0.0001)
    args = parser.parse_args()
    if not 1 <= args.steps <= 1000 or not 1 <= args.threads <= 16:
        parser.error("CPU run requires 1..1000 steps and 1..16 threads")
    if not math.isfinite(args.learning_rate) or not 0 < args.learning_rate <= 0.001:
        parser.error("Learning rate must be finite and in (0, 0.001]")
    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    if manifest.get("architecture", "sd15") != args.architecture:
        raise ValueError("Manifest and requested architecture differ")
    validation = manifest.get("validation_images", [])
    if not manifest["images"]:
        raise ValueError("Empty training set")
    train_hashes = {item["sha256"] for item in manifest["images"]}
    valid_hashes = {item["sha256"] for item in validation}
    train_groups = {item.get("group", item["id"]) for item in manifest["images"]}
    valid_groups = {item.get("group", item["id"]) for item in validation}
    if train_hashes & valid_hashes or train_groups & valid_groups:
        raise ValueError("Training/validation leakage")
    parent = None
    if args.resume_training:
        parent = json.loads((args.resume_training / "report.json").read_text("utf-8"))
        if parent.get("architecture", "sd15") != args.architecture:
            raise ValueError("Parent adapter architecture differs")
        if (
            parent["status"] != "complete"
            or parent["manifest"]["base_model"] != manifest["base_model"]
        ):
            raise ValueError("Incompatible or incomplete parent training")
        parent_seen = {item["sha256"] for item in parent["manifest"]["images"]}
        parent_seen.update(parent.get("seen_training_hashes", []))
        parent_groups = {item.get("group", item["id"]) for item in parent["manifest"]["images"]}
        parent_groups.update(parent.get("seen_training_groups", []))
        if parent_seen & valid_hashes or parent_groups & valid_groups:
            raise ValueError("Validation image was used in parent training")
        train_hashes.update(parent_seen)
        train_groups.update(parent_groups)
        if (
            digest(args.resume_training / "adapter-peft.safetensors")
            != parent["artifacts"]["adapter-peft.safetensors"]
        ):
            raise ValueError("Parent adapter hash mismatch")
    for item in manifest["images"] + validation:
        region_weights(item.get("loss_regions", []), 8, 8)
        if item.get("loss_regions"):
            x0, y0, x1, y1 = item["crop"]
            if x1 - x0 != y1 - y0:
                raise ValueError(
                    "Annotated loss regions require a square crop to preserve coordinates"
                )
        if digest(ROOT / item["path"]) != item["sha256"]:
            raise ValueError("Reference hash mismatch")
    model = ROOT / manifest["base_model"]["path"]
    if digest(model) != manifest["base_model"]["sha256"]:
        raise ValueError("Base checkpoint hash mismatch")
    args.output.mkdir(parents=True, exist_ok=False)
    (args.output / "training-source.py").write_bytes(source_bytes)
    report = {
        "status": "initializing",
        "purpose": "CPU pilot training, style quality requires independent evaluation",
        "manifest": manifest,
        "steps": args.steps,
        "resolution": args.resolution,
        "architecture": args.architecture,
        "seed": 42,
        "rank": 4,
        "learning_rate": args.learning_rate,
        "losses": [],
        "seen_training_hashes": sorted(train_hashes),
        "validation_count": len(validation),
        "seen_training_groups": sorted(train_groups),
        "resume_training": str(args.resume_training) if parent else None,
        "parent_steps": parent["steps"] + parent.get("parent_steps", 0) if parent else 0,
        "optimizer": "fresh AdamW; adapter warm start only",
    }

    def save_report():
        temp = args.output / "report.json.tmp"
        temp.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        temp.replace(args.output / "report.json")

    save_report()
    try:
        import numpy as np
        import torch
        import torch.nn.functional as F
        from diffusers import DDPMScheduler, StableDiffusionPipeline, StableDiffusionXLPipeline
        from diffusers.utils import convert_state_dict_to_diffusers
        from diffusers.utils.state_dict_utils import convert_state_dict_to_kohya
        from huggingface_hub import snapshot_download
        from peft import LoraConfig
        from peft.utils import get_peft_model_state_dict, set_peft_model_state_dict
        from PIL import Image, ImageOps
        from safetensors.torch import load_file, save_file

        torch.set_num_threads(args.threads)
        torch.manual_seed(42)
        report["environment"] = {
            name: version(name)
            for name in ["torch", "diffusers", "peft", "transformers", "accelerate"]
        }
        report["environment"].update(python=platform.python_version(), threads=args.threads)
        report["script_sha256"] = hashlib.sha256(source_bytes).hexdigest()
        repo = "stable-diffusion-v1-5/stable-diffusion-v1-5"
        revision = "451f4fe16113bff5a5d2269ed5ad43b0592e9a14"
        if args.architecture == "sdxl":
            repo = "stabilityai/stable-diffusion-xl-base-1.0"
            revision = "462165984030d82259a11f4367a4eed129e94a7b"
        config = snapshot_download(
            repo,
            revision=revision,
            local_dir=ROOT / f"data/lora-{args.architecture}-config",
            allow_patterns=["*.json", "**/*.json", "tokenizer/*.txt", "tokenizer_2/*.txt"],
        )
        report["config_repo"] = repo
        report["config_revision"] = revision
        save_report()
        print("Loading verified base checkpoint on CPU:", args.architecture, flush=True)
        pipeline_class = (
            StableDiffusionXLPipeline if args.architecture == "sdxl" else StableDiffusionPipeline
        )
        extra = (
            {"add_watermarker": False}
            if args.architecture == "sdxl"
            else {
                "safety_checker": None,
                "feature_extractor": None,
                "requires_safety_checker": False,
            }
        )
        pipe = pipeline_class.from_single_file(
            str(model), config=config, local_files_only=True, torch_dtype=torch.float32, **extra
        )
        if args.architecture == "sdxl":
            if pipe.unet.config.addition_embed_type != "text_time":
                raise ValueError("Expected SDXL text/time conditioning")
            pipe.text_encoder_2.requires_grad_(False)
            pipe.text_encoder_2.eval()
            pipe.unet.enable_gradient_checkpointing()
        pipe.unet.requires_grad_(False)
        pipe.vae.requires_grad_(False)
        pipe.text_encoder.requires_grad_(False)
        pipe.vae.eval()
        pipe.text_encoder.eval()
        pipe.unet.add_adapter(
            LoraConfig(
                r=4,
                lora_alpha=4,
                init_lora_weights="gaussian",
                target_modules=["to_q", "to_k", "to_v", "to_out.0"],
            )
        )
        if parent:
            set_peft_model_state_dict(
                pipe.unet, load_file(str(args.resume_training / "adapter-peft.safetensors"))
            )
        trainable = [p for p in pipe.unet.parameters() if p.requires_grad]
        report["trainable_parameters"] = sum(p.numel() for p in trainable)
        report["frozen_parameters"] = sum(
            p.numel() for p in pipe.unet.parameters() if not p.requires_grad
        )
        initial = {k: v.detach().clone() for k, v in get_peft_model_state_dict(pipe.unet).items()}
        training_masks = []

        def encode(items, training):
            cached = []
            for item in items:
                with Image.open(ROOT / item["path"]) as source:
                    crop = source.convert("RGB").crop(item["crop"])
                    size = (args.resolution, args.resolution)
                    if manifest.get("aspect_buckets"):
                        ratio = crop.width / crop.height
                        if ratio < 0.85:
                            size = (round(args.resolution * 2 / 3 / 64) * 64, args.resolution)
                        elif ratio > 1.18:
                            size = (args.resolution, round(args.resolution * 2 / 3 / 64) * 64)
                    im = ImageOps.fit(crop, size)
                    pixels = (
                        torch.from_numpy(np.array(im).copy()).permute(2, 0, 1).float()[None] / 127.5
                        - 1
                    )
                distribution = pipe.vae.encode(pixels).latent_dist
                latent = (
                    distribution.sample() if training else distribution.mode()
                ) * pipe.vae.config.scaling_factor
                token_count = max(
                    len(tokenizer(item["caption"]).input_ids)
                    for tokenizer in (
                        [pipe.tokenizer, pipe.tokenizer_2]
                        if args.architecture == "sdxl"
                        else [pipe.tokenizer]
                    )
                )
                if token_count > 77:
                    raise ValueError(
                        f"Caption would be truncated: {item['id']} ({token_count} tokens)"
                    )
                report.setdefault("caption_tokens", {})[item["id"]] = token_count
                tokens = pipe.tokenizer(
                    item["caption"],
                    padding="max_length",
                    truncation=True,
                    max_length=77,
                    return_tensors="pt",
                ).input_ids
                # Match the application's clip_skip=2 convention.
                hidden = pipe.text_encoder(tokens, output_hidden_states=True).hidden_states[-2]
                if args.architecture == "sd15":
                    hidden = pipe.text_encoder.text_model.final_layer_norm(hidden)
                added = {}
                if args.architecture == "sdxl":
                    # SDXL concatenates unnormalized penultimate CLIP hidden states.
                    hidden_one = hidden
                    tokens_two = pipe.tokenizer_2(
                        item["caption"],
                        padding="max_length",
                        truncation=True,
                        max_length=77,
                        return_tensors="pt",
                    ).input_ids
                    encoded_two = pipe.text_encoder_2(tokens_two, output_hidden_states=True)
                    hidden = torch.cat([hidden_one, encoded_two.hidden_states[-2]], dim=-1)
                    # Manual square crop is the source image presented to the training transform.
                    added = {
                        "text_embeds": encoded_two.text_embeds,
                        "time_ids": torch.tensor(
                            [[crop.height, crop.width, 0, 0, size[1], size[0]]], dtype=hidden.dtype
                        ),
                    }
                    if hidden.shape != (1, 77, 2048) or added["text_embeds"].shape != (1, 1280):
                        raise ValueError("Invalid dual-encoder SDXL conditioning")
                cached.append((latent, hidden, added))
                if training:
                    mask = torch.tensor(
                        region_weights(
                            item.get("loss_regions", []), latent.shape[-1], latent.shape[-2]
                        )
                    )[None, None]
                    training_masks.append(mask)
                    report.setdefault("loss_region_coverage", {})[item["id"]] = {
                        "fraction_emphasized": (mask > 1).float().mean().item(),
                        "mean_weight": mask.mean().item(),
                    }
                print("Cached reference", item["id"], flush=True)
            return cached

        with torch.no_grad():
            cache = encode(manifest["images"], True)
            validation_cache = encode(validation, False)
        if args.architecture == "sdxl":
            # Cached conditioning/latents allow releasing frozen encoders before backward.
            import gc

            pipe.register_modules(text_encoder=None, text_encoder_2=None, vae=None)
            gc.collect()
        scheduler = DDPMScheduler.from_config(pipe.scheduler.config)
        if scheduler.config.prediction_type != "epsilon":
            raise ValueError("Only epsilon prediction checkpoints supported")

        def evaluate_validation():
            # Identical noise/timesteps on never-trained images. Denoising loss is NOT style similarity.
            pipe.unet.eval()
            fixed = torch.Generator().manual_seed(20261104)
            rows = []
            with torch.no_grad():
                for item, (latent, hidden, added) in zip(validation, validation_cache):
                    values = []
                    for t in [250, 750]:
                        noise = torch.randn(latent.shape, generator=fixed)
                        timestep = torch.tensor([t])
                        prediction = pipe.unet(
                            scheduler.add_noise(latent, noise, timestep),
                            timestep,
                            hidden,
                            added_cond_kwargs=added,
                        ).sample
                        values.append(F.mse_loss(prediction, noise).item())
                    rows.append({"id": item["id"], "mse": sum(values) / len(values)})
            return {
                "metric": "fixed-noise denoising MSE, not style similarity",
                "images": rows,
                "mean": sum(r["mse"] for r in rows) / len(rows) if rows else None,
            }

        if validation:
            pipe.unet.disable_adapters()
            report["validation_base"] = evaluate_validation()
            pipe.unet.enable_adapters()
            report["validation_before"] = evaluate_validation()
            save_report()
        optimizer = torch.optim.AdamW(trainable, lr=args.learning_rate)
        pipe.unet.train()
        report["status"] = "training"
        save_report()
        start = time.monotonic()
        order = list(range(len(cache)))
        for step in range(args.steps):
            if validation and step % len(cache) == 0:
                order = torch.randperm(len(cache)).tolist()
            item_index = order[step % len(cache)]
            latent, hidden, added = cache[item_index]
            noise = torch.randn_like(latent)
            timestep = torch.randint(0, scheduler.config.num_train_timesteps, (1,)).long()
            prediction = pipe.unet(
                scheduler.add_noise(latent, noise, timestep),
                timestep,
                hidden,
                added_cond_kwargs=added,
            ).sample
            squared_error = F.mse_loss(prediction.float(), noise.float(), reduction="none")
            mask = training_masks[item_index]
            loss = (squared_error * mask).mean() / mask.mean()
            if not torch.isfinite(loss):
                raise ValueError("Nonfinite loss")
            loss.backward()
            norm = torch.nn.utils.clip_grad_norm_(trainable, 1.0, error_if_nonfinite=True)
            optimizer.step()
            optimizer.zero_grad(set_to_none=True)
            row = {
                "step": step + 1,
                "loss": loss.item(),
                "gradient_norm": norm.item(),
                "unweighted_loss": squared_error.mean().item(),
                "training_image_id": manifest["images"][item_index]["id"],
                "elapsed_seconds": round(time.monotonic() - start, 2),
            }
            report["losses"].append(row)
            save_report()
            print(json.dumps(row), flush=True)
        if validation:
            report["validation_after"] = evaluate_validation()
        state = {
            k: v.detach().cpu().contiguous()
            for k, v in get_peft_model_state_dict(pipe.unet).items()
        }
        report["max_parameter_change"] = max(
            (state[k] - initial[k]).abs().max().item() for k in state
        )
        if report["max_parameter_change"] <= 0:
            raise ValueError("LoRA parameters did not change")
        save_file(state, str(args.output / "adapter-peft.safetensors"))
        pipeline_class.save_lora_weights(
            args.output,
            unet_lora_layers=convert_state_dict_to_diffusers(state),
            safe_serialization=True,
        )
        # Kohya naming is understood by stable-diffusion.cpp; retain alpha explicitly.
        kohya = convert_state_dict_to_kohya({"unet." + key: value for key, value in state.items()})
        save_file(
            kohya,
            str(args.output / "xiaoxiao-smoke.safetensors"),
            metadata={
                "ss_network_module": "networks.lora",
                "ss_network_dim": "4",
                "ss_network_alpha": "4",
                "paintai_purpose": "CPU pilot training; inspect independent validation before use",
            },
        )
        pipe.unet.eval()
        with torch.no_grad():
            probe, hidden, added = cache[0]
            timestep = torch.tensor([500])
            before = pipe.unet(probe, timestep, hidden, added_cond_kwargs=added).sample
            pipe.unet.delete_adapters("default")
            pipe.unet.add_adapter(
                LoraConfig(r=4, lora_alpha=4, target_modules=["to_q", "to_k", "to_v", "to_out.0"])
            )
            set_peft_model_state_dict(
                pipe.unet, load_file(str(args.output / "adapter-peft.safetensors"))
            )
            after = pipe.unet(probe, timestep, hidden, added_cond_kwargs=added).sample
            report["reload_max_error"] = (before - after).abs().max().item()
            pipe.unet.disable_adapters()
            baseline = pipe.unet(probe, timestep, hidden, added_cond_kwargs=added).sample
            report["adapter_prediction_mean_delta"] = (after - baseline).abs().mean().item()
            pipe.unet.enable_adapters()
        if report["reload_max_error"] > 1e-5:
            raise ValueError("Reload changed predictions")
        report["artifacts"] = {p.name: digest(p) for p in args.output.glob("*.safetensors")}
        report["status"] = "complete"
        save_report()
        print("Actual LoRA weights saved and reload verified", flush=True)
    except BaseException as exc:
        report["status"] = "failed"
        report["error"] = str(exc)
        save_report()
        raise


if __name__ == "__main__":
    main()
