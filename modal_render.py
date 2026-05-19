"""Modal renderer for opal turntable frames.

Run:
    modal run modal_render.py --samples 100 --output-dir renders/opal-100spp-frames

Each Modal task renders one turntable angle, returns one image, and the local
entrypoint writes those frames to disk. Stitching can happen separately.
"""

from __future__ import annotations

import os
import subprocess
import time
import urllib.request
from pathlib import Path

import modal


ROOT = Path(__file__).parent

image = (
    modal.Image.from_registry("node:22-bookworm-slim", add_python="3.11")
    .apt_install(
        "ca-certificates",
        "chromium",
        "fonts-liberation",
        "libasound2",
        "libatk-bridge2.0-0",
        "libatk1.0-0",
        "libcairo2",
        "libcups2",
        "libdbus-1-3",
        "libdrm2",
        "libgbm1",
        "libglib2.0-0",
        "libgtk-3-0",
        "libnss3",
        "libpango-1.0-0",
        "libx11-xcb1",
        "libxcb1",
        "libxcomposite1",
        "libxdamage1",
        "libxext6",
        "libxfixes3",
        "libxkbcommon0",
        "libxrandr2",
        "mesa-utils",
    )
    .env(
        {
            "PUPPETEER_EXECUTABLE_PATH": "/usr/bin/chromium",
            "PUPPETEER_SKIP_DOWNLOAD": "true",
            "LIBGL_ALWAYS_SOFTWARE": "1",
            "GALLIUM_DRIVER": "llvmpipe",
        }
    )
    .add_local_dir(
        ROOT,
        remote_path="/app",
        copy=True,
        ignore=[
            ".git/**",
            "node_modules/**",
            "dist/**",
            "renders/**",
            "*.log",
        ],
    )
    .run_commands("cd /app && PUPPETEER_SKIP_DOWNLOAD=true npm ci")
)

app = modal.App("opal-pathtracer-render", image=image)


def _wait_for_vite(url: str, timeout_s: int = 60) -> None:
    deadline = time.time() + timeout_s
    last_error: Exception | None = None
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2) as response:
                if response.status < 500:
                    return
        except Exception as err:  # noqa: BLE001
            last_error = err
        time.sleep(0.5)
    raise TimeoutError(f"Vite did not respond at {url}: {last_error}")


@app.function(timeout=7200, cpu=4.0, memory=4096)
def render_frame(
    angle_index: int,
    samples: int = 100,
    angles: int = 72,
    frame_size: int = 512,
    preset: str = "black",
    fmt: str = "webp",
    quality: int = 90,
) -> dict:
    env = os.environ.copy()
    env["PUPPETEER_EXECUTABLE_PATH"] = "/usr/bin/chromium"
    env["OPAL_URL"] = "http://127.0.0.1:4200/pathtracer.html"

    server = subprocess.Popen(
        ["npm", "run", "dev", "--", "--host", "0.0.0.0", "--port", "4200"],
        cwd="/app",
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )

    try:
        _wait_for_vite(env["OPAL_URL"])
        ext = "jpg" if fmt == "jpeg" else fmt
        output = f"/tmp/opal-{preset}-frame-{angle_index:04d}-{frame_size}-{samples}spp.{ext}"
        cmd = [
            "node",
            "scripts/render-turntable.mjs",
            "frame",
            "--url",
            env["OPAL_URL"],
            "--output",
            output,
            "--samples",
            str(samples),
            "--angle-index",
            str(angle_index),
            "--angles",
            str(angles),
            "--frame",
            str(frame_size),
            "--format",
            fmt,
            "--quality",
            str(quality),
            "--preset",
            preset,
        ]
        render_proc = subprocess.Popen(
            cmd,
            cwd="/app",
            env=env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
        assert render_proc.stdout is not None
        try:
            for line in render_proc.stdout:
                print(line, end="", flush=True)
            returncode = render_proc.wait(timeout=7000)
        except subprocess.TimeoutExpired as err:
            render_proc.kill()
            raise TimeoutError("Render subprocess timed out") from err
        if returncode != 0:
            raise RuntimeError(f"Render failed with exit code {returncode}")

        data = Path(output).read_bytes()
        return {
            "filename": Path(output).name,
            "data": data,
            "bytes": len(data),
            "samples": samples,
            "angles": angles,
            "angle_index": angle_index,
            "frame_size": frame_size,
            "format": fmt,
        }
    finally:
        server.terminate()
        try:
            server.wait(timeout=5)
        except subprocess.TimeoutExpired:
            server.kill()


@app.local_entrypoint()
def main(
    samples: int = 100,
    output_dir: str = "renders/opal-100spp-frames",
    angles: int = 72,
    frame_size: int = 512,
    preset: str = "black",
    fmt: str = "webp",
    quality: int = 90,
    concurrency: int = 24,
) -> None:
    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    pending = []
    complete = 0
    for angle_index in range(angles):
      call = render_frame.spawn(
          angle_index,
          samples=samples,
          angles=angles,
          frame_size=frame_size,
          preset=preset,
          fmt=fmt,
          quality=quality,
      )
      pending.append(call)
      if len(pending) >= concurrency:
          result = pending.pop(0).get()
          out = out_dir / result["filename"]
          out.write_bytes(result["data"])
          complete += 1
          mb = result["bytes"] / 1024 / 1024
          print(f"[{complete}/{angles}] saved {out} ({mb:.2f} MB)")

    for call in pending:
        result = call.get()
        out = out_dir / result["filename"]
        out.write_bytes(result["data"])
        complete += 1
        mb = result["bytes"] / 1024 / 1024
        print(f"[{complete}/{angles}] saved {out} ({mb:.2f} MB)")
