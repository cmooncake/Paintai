"""Live pixel-input smoke check: identical question, two different local images."""

import argparse
import base64
import hashlib
import json
from pathlib import Path

import httpx
from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data/local-vision"


def main():
    parser = argparse.ArgumentParser(description="Check visual input with two different images")
    parser.add_argument("--image", type=Path, required=True, help="Generated image to inspect")
    args = parser.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    source = args.image.resolve()
    if not source.is_file():
        raise SystemExit("Input image does not exist")
    control = OUT / "vision-control.png"
    canvas = Image.new("RGB", (512, 512), "white")
    draw = ImageDraw.Draw(canvas)
    draw.ellipse((40, 260, 230, 450), fill="blue")
    draw.rectangle((290, 40, 460, 210), fill="red")
    canvas.save(control)
    question = "Describe only what you can see in this image, including objects, colors, and layout. Be concise."
    results = []
    with httpx.Client(timeout=600) as client:
        for path in (source, control):
            pixels = path.read_bytes()
            response = client.post(
                "http://127.0.0.1:18081/v1/chat/completions",
                json={
                    "model": "paintai-vision",
                    "temperature": 0,
                    "max_completion_tokens": 300,
                    "messages": [
                        {
                            "role": "user",
                            "content": [
                                {"type": "text", "text": question},
                                {
                                    "type": "image_url",
                                    "image_url": {
                                        "url": "data:image/png;base64,"
                                        + base64.b64encode(pixels).decode()
                                    },
                                },
                            ],
                        }
                    ],
                },
            )
            response.raise_for_status()
            message = response.json()["choices"][0]["message"]["content"]
            results.append(
                {
                    "image_path": str(path),
                    "image_sha256": hashlib.sha256(pixels).hexdigest(),
                    "question": question,
                    "answer": message,
                }
            )
            print(message, flush=True)
    (OUT / "verification.json").write_text(
        json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    if results[0]["answer"] == results[1]["answer"]:
        raise SystemExit("Descriptions were identical; manual inspection required")
    print(
        "Two different descriptions saved. Inspect them against both images; this is not a quality benchmark."
    )


if __name__ == "__main__":
    main()
