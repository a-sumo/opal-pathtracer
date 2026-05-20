#!/usr/bin/env python3
"""Native opal atlas renderer.

This is the fast asset-generation path for Lens Studio turntables. It does not
spin up a browser, Vite, Puppeteer, or WebGL. Instead, Taichi compiles one
native kernel that renders a full atlas directly on CUDA, Metal, Vulkan, or CPU.

The model is deliberately compact: procedural 3D Voronoi domains, a Bragg-like
wavelength estimate per domain, body-tone absorption, and a specular cabochon
highlight. The heavier browser path remains the research renderer; this script
is the practical turntable/multiview generator.
"""

import argparse
import math
import time
from pathlib import Path

import numpy as np
from PIL import Image
import taichi as ti


PRESETS = {
    "black": {
        "diameter_nm": 345.0,
        "domain_scale": 13.0,
        "body": (0.018, 0.015, 0.022),
        "body_weight": 0.34,
        "play": 2.15,
        "sigma": 2.8,
        "spec": 1.1,
    },
    "white": {
        "diameter_nm": 275.0,
        "domain_scale": 24.0,
        "body": (0.86, 0.84, 0.76),
        "body_weight": 0.74,
        "play": 0.95,
        "sigma": 0.35,
        "spec": 0.75,
    },
    "crystal": {
        "diameter_nm": 255.0,
        "domain_scale": 17.0,
        "body": (0.18, 0.24, 0.30),
        "body_weight": 0.22,
        "play": 1.55,
        "sigma": 0.55,
        "spec": 1.25,
    },
    "fire": {
        "diameter_nm": 430.0,
        "domain_scale": 10.0,
        "body": (0.95, 0.22, 0.035),
        "body_weight": 0.52,
        "play": 1.15,
        "sigma": 0.9,
        "spec": 0.85,
    },
    "harlequin": {
        "diameter_nm": 315.0,
        "domain_scale": 6.5,
        "body": (0.020, 0.016, 0.020),
        "body_weight": 0.30,
        "play": 2.35,
        "sigma": 2.45,
        "spec": 1.15,
    },
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Render native opal atlases.")
    parser.add_argument("--presets", default="black", help="Comma-separated presets.")
    parser.add_argument("--output-dir", default="renders/native-opal-atlases")
    parser.add_argument("--angles", type=int, default=144, help="Turntable yaw angles.")
    parser.add_argument("--cols", type=int, default=12, help="Atlas columns.")
    parser.add_argument("--frame-size", type=int, default=320)
    parser.add_argument("--samples", type=int, default=1, help="Subpixel samples per atlas pixel.")
    parser.add_argument("--ray-steps", type=int, default=3, help="Domain samples along each chord.")
    parser.add_argument("--quality", type=int, default=92)
    parser.add_argument("--arch", default="auto", choices=["auto", "cuda", "metal", "vulkan", "cpu"])
    parser.add_argument("--view-mode", default="turntable", choices=["turntable", "multiview"])
    parser.add_argument("--yaw-angles", type=int, default=0)
    parser.add_argument("--pitch-rows", type=int, default=1)
    parser.add_argument("--pitch-min", type=float, default=0.0)
    parser.add_argument("--pitch-max", type=float, default=0.0)
    parser.add_argument("--distance", type=float, default=2.7)
    return parser.parse_args()


def init_taichi(arch_name: str) -> None:
    arch_map = {
        "cuda": ti.cuda,
        "metal": ti.metal,
        "vulkan": ti.vulkan,
        "cpu": ti.cpu,
    }
    if arch_name == "auto":
        ti.init(arch=ti.gpu, default_fp=ti.f32, offline_cache=True)
    else:
        ti.init(arch=arch_map[arch_name], default_fp=ti.f32, offline_cache=True)


@ti.func
def fract(x):
    return x - ti.floor(x)


@ti.func
def hash41(ix, iy, iz, ch):
    n = (
        ti.cast(ix, ti.f32) * 127.1
        + ti.cast(iy, ti.f32) * 311.7
        + ti.cast(iz, ti.f32) * 74.7
        + ti.cast(ch, ti.f32) * 43.3
    )
    return fract(ti.sin(n) * 43758.5453123)


@ti.func
def hash_normal(ix, iy, iz):
    n = ti.Vector(
        [
            hash41(ix, iy, iz, 11) * 2.0 - 1.0,
            hash41(ix, iy, iz, 17) * 2.0 - 1.0,
            hash41(ix, iy, iz, 23) * 2.0 - 1.0,
        ]
    )
    return n.normalized()


@ti.func
def edge_hash(ax, ay, az, bx, by, bz):
    lo_x = ti.min(ax, bx)
    lo_y = ti.min(ay, by)
    lo_z = ti.min(az, bz)
    hi_x = ti.max(ax, bx)
    hi_y = ti.max(ay, by)
    hi_z = ti.max(az, bz)
    return hash41(lo_x + hi_x * 7, lo_y + hi_y * 11, lo_z + hi_z * 13, 201)


@ti.func
def percolated_cell(cx, cy, cz, threshold):
    rx = cx
    ry = cy
    rz = cz
    for axis in ti.static(range(3)):
        for sgn in ti.static(range(2)):
            dx = 0
            dy = 0
            dz = 0
            d = -1
            if sgn == 1:
                d = 1
            if axis == 0:
                dx = d
            elif axis == 1:
                dy = d
            else:
                dz = d
            nx = cx + dx
            ny = cy + dy
            nz = cz + dz
            if edge_hash(cx, cy, cz, nx, ny, nz) < threshold:
                if nx < rx or (nx == rx and (ny < ry or (ny == ry and nz < rz))):
                    rx = nx
                    ry = ny
                    rz = nz
    return rx, ry, rz


@ti.func
def voronoi_cell(p, percolation):
    ix = ti.cast(ti.floor(p.x), ti.i32)
    iy = ti.cast(ti.floor(p.y), ti.i32)
    iz = ti.cast(ti.floor(p.z), ti.i32)
    f = p - ti.Vector([ti.cast(ix, ti.f32), ti.cast(iy, ti.f32), ti.cast(iz, ti.f32)])
    best = 1.0e9
    second = 1.0e9
    wx = ix
    wy = iy
    wz = iz
    for dx in ti.static(range(-1, 2)):
        for dy in ti.static(range(-1, 2)):
            for dz in ti.static(range(-1, 2)):
                cx = ix + dx
                cy = iy + dy
                cz = iz + dz
                jitter = ti.Vector(
                    [
                        hash41(cx, cy, cz, 0) * 0.9 + 0.05,
                        hash41(cx, cy, cz, 1) * 0.9 + 0.05,
                        hash41(cx, cy, cz, 2) * 0.9 + 0.05,
                    ]
                )
                d = ti.Vector([ti.cast(dx, ti.f32), ti.cast(dy, ti.f32), ti.cast(dz, ti.f32)]) + jitter - f
                dist = d.dot(d)
                if dist < best:
                    second = best
                    best = dist
                    wx = cx
                    wy = cy
                    wz = cz
                elif dist < second:
                    second = dist
    px, py, pz = percolated_cell(wx, wy, wz, percolation)
    boundary = ti.sqrt(ti.max(second, 0.0)) - ti.sqrt(ti.max(best, 0.0))
    return px, py, pz, boundary


@ti.func
def wavelength_rgb(wl):
    r = 0.0
    g = 0.0
    b = 0.0
    if wl >= 380.0 and wl < 440.0:
        r = -(wl - 440.0) / 60.0
        b = 1.0
    elif wl < 490.0:
        g = (wl - 440.0) / 50.0
        b = 1.0
    elif wl < 510.0:
        g = 1.0
        b = -(wl - 510.0) / 20.0
    elif wl < 580.0:
        r = (wl - 510.0) / 70.0
        g = 1.0
    elif wl < 645.0:
        r = 1.0
        g = -(wl - 645.0) / 65.0
    elif wl <= 780.0:
        r = 1.0
    edge = ti.min(ti.max((wl - 380.0) / 35.0, 0.0), 1.0) * (1.0 - ti.min(ti.max((wl - 720.0) / 60.0, 0.0), 1.0))
    return ti.Vector([r, g, b]) * edge


@ti.func
def aces(c):
    return ti.min((c * (2.51 * c + 0.03)) / (c * (2.43 * c + 0.59) + 0.14), 1.0)


@ti.kernel
def render_atlas(
    out: ti.template(),
    width: ti.i32,
    height: ti.i32,
    frame_size: ti.i32,
    cols: ti.i32,
    total_views: ti.i32,
    yaw_angles: ti.i32,
    pitch_rows: ti.i32,
    pitch_min: ti.f32,
    pitch_max: ti.f32,
    distance: ti.f32,
    samples: ti.i32,
    ray_steps: ti.i32,
    diameter_nm: ti.f32,
    domain_scale: ti.f32,
    percolation: ti.f32,
    body_r: ti.f32,
    body_g: ti.f32,
    body_b: ti.f32,
    body_weight: ti.f32,
    play: ti.f32,
    sigma: ti.f32,
    spec_strength: ti.f32,
):
    radius = 0.8
    tan_half_fov = ti.tan(20.0 * math.pi / 180.0)
    n_eff = 1.35
    d111 = diameter_nm * 0.8165

    for y, x in ti.ndrange(height, width):
        frame_x = x // frame_size
        frame_y = y // frame_size
        frame = frame_y * cols + frame_x
        rgba = ti.Vector([0, 0, 0, 0], dt=ti.u8)

        if frame < total_views:
            lx = x - frame_x * frame_size
            ly = y - frame_y * frame_size
            yaw_index = frame
            pitch_index = 0
            if pitch_rows > 1:
                yaw_index = frame % yaw_angles
                pitch_index = frame // yaw_angles
            yaw = ti.cast(yaw_index, ti.f32) / ti.cast(yaw_angles, ti.f32) * 2.0 * math.pi
            pitch = 0.0
            if pitch_rows > 1:
                pitch = (pitch_min + (pitch_max - pitch_min) * ti.cast(pitch_index, ti.f32) / ti.cast(pitch_rows - 1, ti.f32)) * math.pi / 180.0

            ring = distance * ti.cos(pitch)
            cam = ti.Vector([ring * ti.sin(yaw), distance * ti.sin(pitch), ring * ti.cos(yaw)])
            forward = (-cam).normalized()
            world_up = ti.Vector([0.0, 1.0, 0.0])
            right = world_up.cross(forward).normalized()
            up = forward.cross(right).normalized()

            color = ti.Vector([0.0, 0.0, 0.0])
            alpha_hit = 0

            for si in range(samples):
                jx = 0.5
                jy = 0.5
                if samples > 1:
                    jx = hash41(lx, ly, si, 301)
                    jy = hash41(lx, ly, si, 307)
                px = ((ti.cast(lx, ti.f32) + jx) / ti.cast(frame_size, ti.f32) * 2.0 - 1.0) * tan_half_fov
                py = (1.0 - (ti.cast(ly, ti.f32) + jy) / ti.cast(frame_size, ti.f32) * 2.0) * tan_half_fov
                rd = (forward + right * px + up * py).normalized()

                b = cam.dot(rd)
                c = cam.dot(cam) - radius * radius
                disc = b * b - c
                if disc > 0.0:
                    sq = ti.sqrt(disc)
                    t0 = -b - sq
                    t1 = -b + sq
                    if t1 > 0.0:
                        if t0 < 0.0:
                            t0 = 0.0
                        hit = cam + rd * t0
                        normal = hit.normalized()
                        view = (-rd).normalized()
                        path_len = ti.max(t1 - t0, 0.001)
                        grain_col = ti.Vector([0.0, 0.0, 0.0])
                        for st in range(ray_steps):
                            f = (ti.cast(st, ti.f32) + 0.5) / ti.cast(ray_steps, ti.f32)
                            p = cam + rd * (t0 + path_len * f)
                            q = p / radius * domain_scale
                            cx, cy, cz, boundary = voronoi_cell(q, percolation)
                            crystal = hash_normal(cx, cy, cz)
                            cos_theta = ti.max(ti.abs(view.dot(crystal)), 0.035)
                            lam = 2.0 * d111 * n_eff * cos_theta
                            rgb = wavelength_rgb(lam)
                            edge = 1.0 - ti.min(ti.max((boundary - 0.02) / 0.18, 0.0), 1.0)
                            sparkle = 0.55 + 0.45 * edge
                            grain_col += rgb * sparkle
                        grain_col /= ti.cast(ray_steps, ti.f32)

                        body = ti.Vector([body_r, body_g, body_b])
                        trans = ti.exp(-sigma * path_len)
                        ndv = ti.max(normal.dot(view), 0.0)
                        base = body * (body_weight * (0.35 + 0.65 * ndv)) * (0.25 + 0.75 * trans)
                        opal = grain_col * play * (1.0 - 0.45 * trans)
                        light = ti.Vector([0.45, 0.72, 0.53]).normalized()
                        half_v = (view + light).normalized()
                        spec = ti.pow(ti.max(normal.dot(half_v), 0.0), 90.0) * spec_strength
                        fres = ti.pow(1.0 - ndv, 4.0) * 0.12
                        color += base + opal + ti.Vector([spec + fres, spec + fres, spec + fres])
                        alpha_hit = 255

            if samples > 0:
                color /= ti.cast(samples, ti.f32)
            color = aces(color)
            color = ti.pow(ti.max(color, 0.0), ti.Vector([1.0 / 2.2, 1.0 / 2.2, 1.0 / 2.2]))
            rgba = ti.Vector(
                [
                    ti.cast(ti.min(ti.max(color.x, 0.0), 1.0) * 255.0, ti.u8),
                    ti.cast(ti.min(ti.max(color.y, 0.0), 1.0) * 255.0, ti.u8),
                    ti.cast(ti.min(ti.max(color.z, 0.0), 1.0) * 255.0, ti.u8),
                    ti.cast(alpha_hit, ti.u8),
                ]
            )

        out[y, x] = rgba


def render_one(args: argparse.Namespace, preset_name: str) -> Path:
    params = PRESETS[preset_name]
    yaw_angles = args.yaw_angles or args.angles
    total_views = yaw_angles * args.pitch_rows if args.view_mode == "multiview" else args.angles
    rows = math.ceil(total_views / args.cols)
    width = args.cols * args.frame_size
    height = rows * args.frame_size

    out_field = ti.Vector.field(4, dtype=ti.u8, shape=(height, width))
    t0 = time.time()
    render_atlas(
        out_field,
        width,
        height,
        args.frame_size,
        args.cols,
        total_views,
        yaw_angles,
        args.pitch_rows,
        args.pitch_min,
        args.pitch_max,
        args.distance,
        args.samples,
        args.ray_steps,
        params["diameter_nm"],
        params["domain_scale"],
        0.38,
        params["body"][0],
        params["body"][1],
        params["body"][2],
        params["body_weight"],
        params["play"],
        params["sigma"],
        params["spec"],
    )
    ti.sync()
    arr = out_field.to_numpy()

    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    mode_tag = f"{args.cols}x{rows}-{args.frame_size}"
    if args.view_mode == "multiview":
        mode_tag = f"multiview-{yaw_angles}x{args.pitch_rows}-{args.frame_size}"
    out_path = out_dir / f"opal-{preset_name}-native-{mode_tag}-{args.samples}spp-q{args.quality}.webp"
    Image.fromarray(arr, "RGBA").save(out_path, "WEBP", quality=args.quality, method=6)
    dt = time.time() - t0
    mb = out_path.stat().st_size / 1024 / 1024
    print(f"saved {out_path} ({mb:.2f} MB, {dt:.1f}s)")
    return out_path


def main() -> None:
    args = parse_args()
    init_taichi(args.arch)
    preset_names = [p.strip() for p in args.presets.split(",") if p.strip()]
    for preset in preset_names:
        if preset not in PRESETS:
            raise SystemExit(f"Unknown preset {preset!r}. Known: {', '.join(PRESETS)}")
    for preset in preset_names:
        render_one(args, preset)


if __name__ == "__main__":
    main()
