"""Install pinned official IP-Adapter Plus SD1.5 and ViT-H safe tensors."""

import hashlib
import shutil
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1] / "data/local-image/ip-adapter"
REVISION = "018e402774aeeddd60609b4ecdb7e298259dc729"
FILES = [
    (
        "models/ip-adapter-plus_sd15.safetensors",
        "ip-adapter-plus_sd15.safetensors",
        98183288,
        "a1c250be40455cc61a43da1201ec3f1edaea71214865fb47f57927e06cbe4996",
    ),
    (
        "models/image_encoder/model.safetensors",
        "clip-vision-h.safetensors",
        2528373448,
        "6ca9667da1ca9e0b0f75e46bb030f7e011f44f86cbfb8d5a36590fcd7507b030",
    ),
]


def digest(path):
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def install(source, name, size, expected):
    target = ROOT / name
    if target.exists() and digest(target) == expected:
        print(name, "verified", flush=True)
        return

    def part(index):
        start, end = size * index // 16, size * (index + 1) // 16 - 1
        path = ROOT / f"{name}.part{index}"
        for attempt in range(4):
            have = path.stat().st_size if path.exists() else 0
            if have == end - start + 1:
                return path
            if have > end - start + 1:
                raise ValueError("Invalid partial size")
            try:
                url = f"https://huggingface.co/h94/IP-Adapter/resolve/{REVISION}/{source}?part={index}-{have}"
                with httpx.stream(
                    "GET",
                    url,
                    headers={"Range": f"bytes={start + have}-{end}"},
                    follow_redirects=True,
                    timeout=90,
                ) as response:
                    response.raise_for_status()
                    if (
                        response.headers.get("content-range")
                        != f"bytes {start + have}-{end}/{size}"
                    ):
                        raise ValueError("Unexpected range")
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

    with ThreadPoolExecutor(max_workers=16) as pool:
        futures = [pool.submit(part, i) for i in range(16)]
        while not all(f.done() for f in futures):
            print(
                name,
                f"{sum(p.stat().st_size for p in ROOT.glob(name + '.part*')) / size:.1%}",
                flush=True,
            )
            time.sleep(15)
        parts = [f.result() for f in futures]
    temporary = ROOT / (name + ".download")
    with temporary.open("wb") as out:
        for p in parts:
            with p.open("rb") as src:
                shutil.copyfileobj(src, out)
    if digest(temporary) != expected:
        raise ValueError("SHA256 mismatch")
    temporary.replace(target)
    for p in parts:
        p.unlink()
    print(name, "installed and verified", flush=True)


if __name__ == "__main__":
    ROOT.mkdir(parents=True, exist_ok=True)
    for entry in FILES:
        install(*entry)
