"""Download the author's pinned Animagine XL 4.0 Opt fp16 weights with SHA256 verification."""

import hashlib
import shutil
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1] / "data/local-image"
PARTS = ROOT / "animagine-download64"
NAME = "animagine-xl-4.0-opt.safetensors"
SIZE = 6938350040
CHUNKS = 64
SHA256 = "6327eca98bfb6538dd7a4edce22484a1bbc57a8cff6b11d075d40da1afb847ac"
URL = (
    "https://huggingface.co/cagliostrolab/animagine-xl-4.0/resolve/"
    "2b7c1b397761bf5bd3cc42e5b39ec99314a75a96/" + NAME
)


def digest(path):
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def part(index):
    start, end = SIZE * index // CHUNKS, SIZE * (index + 1) // CHUNKS - 1
    path = PARTS / f"{NAME}.part{index}"
    for attempt in range(4):
        have = path.stat().st_size if path.exists() else 0
        if have == end - start + 1:
            return path
        if have > end - start + 1:
            raise ValueError("Invalid partial download size")
        try:
            with httpx.stream(
                "GET",
                URL + f"?part={index}-{have}",
                headers={"Range": f"bytes={start + have}-{end}"},
                follow_redirects=True,
                timeout=90,
            ) as response:
                response.raise_for_status()
                if response.headers.get("content-range") != f"bytes {start + have}-{end}/{SIZE}":
                    raise ValueError("Unexpected download range")
                with path.open("ab") as out:
                    for block in response.iter_bytes(1024 * 1024):
                        out.write(block)
            if path.stat().st_size != end - start + 1:
                raise ValueError("Incomplete download")
            return path
        except httpx.HTTPError:
            if attempt == 3:
                raise
            time.sleep(2)


if __name__ == "__main__":
    ROOT.mkdir(parents=True, exist_ok=True)
    PARTS.mkdir(parents=True, exist_ok=True)
    target = ROOT / NAME
    if target.exists() and digest(target) == SHA256:
        print("Verified model already installed.")
    else:
        with ThreadPoolExecutor(max_workers=CHUNKS) as pool:
            futures = [pool.submit(part, i) for i in range(CHUNKS)]
            while not all(f.done() for f in futures):
                count = sum(p.stat().st_size for p in PARTS.glob(f"{NAME}.part*"))
                print(f"Download: {count / SIZE:.1%}", flush=True)
                time.sleep(15)
            parts = [f.result() for f in futures]
        temporary = ROOT / (NAME + ".download")
        with temporary.open("wb") as out:
            for p in parts:
                with p.open("rb") as source:
                    shutil.copyfileobj(source, out)
        if digest(temporary) != SHA256:
            raise ValueError("Model SHA256 mismatch")
        temporary.replace(target)
        for p in parts:
            p.unlink()
        print("Animagine XL 4.0 Opt installed; SHA256 verified.", flush=True)
