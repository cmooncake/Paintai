"""Download the author's pinned Counterfeit V3 fp16 weights with SHA256 verification."""

import hashlib
import shutil
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1] / "data/local-image"
NAME = "Counterfeit-V3.0_fix_fp16.safetensors"
SIZE = 2132651162
CHUNKS = 16
SHA256 = "a54c944e4c04e9d9ca43468ef5b90ea0408bb6264829a4980de79a768df7179f"
URL = (
    "https://huggingface.co/gsdf/Counterfeit-V3.0/resolve/"
    "d8ce63b3eb8e9102e54b3fcc07f9cb7a28ef30fc/" + NAME
)


def digest(path):
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def part(index):
    start, end = SIZE * index // CHUNKS, SIZE * (index + 1) // CHUNKS - 1
    path = ROOT / f"{NAME}.part{index}"
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
    target = ROOT / NAME
    if target.exists() and digest(target) == SHA256:
        print("Verified model already installed.")
    else:
        with ThreadPoolExecutor(max_workers=CHUNKS) as pool:
            futures = [pool.submit(part, i) for i in range(CHUNKS)]
            while not all(f.done() for f in futures):
                count = sum(p.stat().st_size for p in ROOT.glob(f"{NAME}.part*"))
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
        print("Counterfeit V3 installed; SHA256 verified.", flush=True)
