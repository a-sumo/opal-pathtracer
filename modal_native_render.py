"""Modal entrypoint for the native Taichi opal renderer.

Unlike modal_render.py, this does not launch Chromium. A Modal GPU runs
scripts/native_opal_renderer.py directly and returns the finished atlas files.

Example:
    modal run modal_native_render.py \
      --samples 1 \
      --angles 144 \
      --frame-size 320 \
      --presets black,white,crystal,fire \
      --output-dir renders/native-preset-atlases
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import modal


ROOT = Path(__file__).parent

image = (
    modal.Image.from_registry("nvidia/cuda:12.4.1-devel-ubuntu22.04", add_python="3.11")
    .apt_install("libx11-6", "libxext6", "libxrender1", "libsm6", "libglib2.0-0", "libgl1")
    .pip_install("taichi==1.7.4", "numpy==2.2.6", "pillow==12.2.0")
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
)

app = modal.App("opal-native-render", image=image)


@app.function(timeout=7200, cpu=4.0, memory=8192, gpu="T4")
def render_native(
    samples: int = 1,
    angles: int = 144,
    cols: int = 12,
    frame_size: int = 320,
    ray_steps: int = 3,
    presets: str = "black",
    quality: int = 92,
    view_mode: str = "turntable",
    yaw_angles: int = 0,
    pitch_rows: int = 1,
    pitch_min: float = 0.0,
    pitch_max: float = 0.0,
) -> list[dict]:
    out_dir = Path("/tmp/native-opal-atlases")
    cmd = [
        "python",
        "scripts/native_opal_renderer.py",
        "--arch",
        "cuda",
        "--presets",
        presets,
        "--output-dir",
        str(out_dir),
        "--samples",
        str(samples),
        "--angles",
        str(angles),
        "--cols",
        str(cols),
        "--frame-size",
        str(frame_size),
        "--ray-steps",
        str(ray_steps),
        "--quality",
        str(quality),
        "--view-mode",
        view_mode,
        "--yaw-angles",
        str(yaw_angles),
        "--pitch-rows",
        str(pitch_rows),
        f"--pitch-min={pitch_min}",
        f"--pitch-max={pitch_max}",
    ]
    proc = subprocess.run(
        cmd,
        cwd="/app",
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    print(proc.stdout, end="", flush=True)
    if proc.returncode != 0:
        raise RuntimeError(f"native renderer failed with exit code {proc.returncode}")
    files = []
    for file_path in sorted(out_dir.glob("*.webp")):
        data = file_path.read_bytes()
        files.append({"filename": file_path.name, "data": data, "bytes": len(data)})
    return files


@app.local_entrypoint()
def main(
    samples: int = 1,
    output_dir: str = "renders/native-preset-atlases",
    angles: int = 144,
    cols: int = 12,
    frame_size: int = 320,
    ray_steps: int = 3,
    presets: str = "black,white,crystal,fire",
    quality: int = 92,
    view_mode: str = "turntable",
    yaw_angles: int = 0,
    pitch_rows: int = 1,
    pitch_min: float = 0.0,
    pitch_max: float = 0.0,
) -> None:
    files = render_native.remote(
        samples=samples,
        angles=angles,
        cols=cols,
        frame_size=frame_size,
        ray_steps=ray_steps,
        presets=presets,
        quality=quality,
        view_mode=view_mode,
        yaw_angles=yaw_angles,
        pitch_rows=pitch_rows,
        pitch_min=pitch_min,
        pitch_max=pitch_max,
    )
    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    for item in files:
        out = out_dir / item["filename"]
        out.write_bytes(item["data"])
        print(f"saved {out} ({item['bytes'] / 1024 / 1024:.2f} MB)")
