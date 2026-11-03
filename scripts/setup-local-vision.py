"""Download pinned local vision weights with resume, size and SHA256 validation."""

import hashlib
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1] / "data" / "local-vision"
ASSETS = [
    (
        "Qwen3-VL-2B-Instruct-Q4_K_M.gguf",
        1107410624,
        "858fcf2a39dc73b26dd86592cb0a5f949b59d1edb365d1dea98e46b02e955e56",
    ),
    (
        "mmproj-F16.gguf",
        819395232,
        "cd5a851d3928697fa1bd76d459d2cc409b6cf40c9d9682b2f5c8e7c6a9f9630f",
    ),
]


def verified(path, size, digest):
    if not path.is_file() or path.stat().st_size != size:
        return False
    with path.open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest() == digest


def download(asset):
    name, size, digest = asset
    target = ROOT / name
    if verified(target, size, digest):
        return target
    partial = ROOT / (name + ".download")
    for attempt in range(4):
        have = partial.stat().st_size if partial.exists() else 0
        if have > size:
            raise RuntimeError(f"Invalid partial size: {partial}")
        if have < size:
            try:
                url = (
                    f"https://huggingface.co/unsloth/Qwen3-VL-2B-Instruct-GGUF/resolve/main/{name}"
                )
                with httpx.stream(
                    "GET",
                    url + f"?resume={have}",
                    headers={"Range": f"bytes={have}-"},
                    follow_redirects=True,
                    timeout=60,
                ) as response:
                    response.raise_for_status()
                    if response.status_code == 206:
                        expected = f"bytes {have}-{size - 1}/{size}"
                        if response.headers.get("content-range") != expected:
                            raise RuntimeError("Unexpected download range")
                    elif have or response.status_code != 200:
                        raise RuntimeError("Server does not support this resume request")
                    with partial.open("ab") as out:
                        for block in response.iter_bytes(1024 * 1024):
                            out.write(block)
            except httpx.HTTPError:
                if attempt == 3:
                    raise
                time.sleep(2)
                continue
        if not verified(partial, size, digest):
            raise RuntimeError(f"Size or SHA256 mismatch: {partial}")
        partial.replace(target)
        return target


def main():
    ROOT.mkdir(parents=True, exist_ok=True)
    total = sum(asset[1] for asset in ASSETS)
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(download, asset) for asset in ASSETS]
        while not all(future.done() for future in futures):
            done = sum(path.stat().st_size for path in ROOT.glob("*.gguf*"))
            print(
                f"Vision weights: {done / total:.1%} ({done // 1000000}/{total // 1000000} MB)",
                flush=True,
            )
            time.sleep(15)
        for future in futures:
            print(f"Verified: {future.result().name}", flush=True)


if __name__ == "__main__":
    main()
