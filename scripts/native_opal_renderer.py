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


MAX_NATIVE_DOMAIN_SCALE = 15.0
MAX_NATIVE_GRID_STRIDE = 33
MAX_NATIVE_GRID_OFFSET = MAX_NATIVE_GRID_STRIDE // 2
MAX_NATIVE_GRAINS = MAX_NATIVE_GRID_STRIDE * MAX_NATIVE_GRID_STRIDE * MAX_NATIVE_GRID_STRIDE


PRESETS = {
    "black": {
        "diameter_nm": 345.0,
        "domain_scale": 8.0,
        "percolation": 0.38,
        "region_blur": 0.42,
        "body": (0.018, 0.015, 0.022),
        "body_weight": 0.34,
        "play": 2.15,
        "sigma": 2.8,
        "spec": 1.1,
    },
    "white": {
        "diameter_nm": 275.0,
        "domain_scale": 12.0,
        "percolation": 0.30,
        "region_blur": 0.62,
        "body": (0.86, 0.84, 0.76),
        "body_weight": 0.74,
        "play": 0.95,
        "sigma": 0.35,
        "spec": 0.75,
    },
    "crystal": {
        "diameter_nm": 255.0,
        "domain_scale": 9.0,
        "percolation": 0.34,
        "region_blur": 0.48,
        "body": (0.18, 0.24, 0.30),
        "body_weight": 0.22,
        "play": 1.55,
        "sigma": 0.55,
        "spec": 1.25,
    },
    "fire": {
        "diameter_nm": 430.0,
        "domain_scale": 7.0,
        "percolation": 0.40,
        "region_blur": 0.38,
        "body": (0.95, 0.22, 0.035),
        "body_weight": 0.52,
        "play": 1.15,
        "sigma": 0.9,
        "spec": 0.85,
    },
    "galaxy": {
        "diameter_nm": 320.0,
        "domain_scale": 6.5,
        "percolation": 0.46,
        "region_blur": 0.95,
        "body": (0.012, 0.013, 0.020),
        "body_weight": 0.30,
        "play": 1.95,
        "sigma": 1.9,
        "spec": 1.05,
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
    parser.add_argument("--domain-scale", type=float, default=0.0, help="Override preset domain scale. Lower values make larger regions.")
    parser.add_argument("--percolation", type=float, default=-1.0, help="Override preset bond threshold, 0-1.")
    parser.add_argument("--region-blur", type=float, default=-1.0, help="Override preset soft boundary width in domain units.")
    parser.add_argument("--growth-noise", type=float, default=0.22, help="Low-frequency modulation of the bond threshold.")
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


def py_fract(x: float) -> float:
    return x - math.floor(x)


def py_hash_float(ix: int, iy: int, iz: int, ch: int = 0) -> float:
    n = ix * 127.1 + iy * 311.7 + iz * 74.7 + ch * 43.3
    return py_fract(math.sin(n) * 43758.5453123)


def py_value_noise(x: float, y: float, z: float) -> float:
    ix = math.floor(x)
    iy = math.floor(y)
    iz = math.floor(z)
    fx = x - ix
    fy = y - iy
    fz = z - iz

    def fade(t: float) -> float:
        return t * t * t * (t * (t * 6.0 - 15.0) + 10.0)

    def lerp(a: float, b: float, t: float) -> float:
        return a + (b - a) * t

    u = fade(fx)
    v = fade(fy)
    w = fade(fz)
    c000 = py_hash_float(ix, iy, iz, 401)
    c100 = py_hash_float(ix + 1, iy, iz, 401)
    c010 = py_hash_float(ix, iy + 1, iz, 401)
    c110 = py_hash_float(ix + 1, iy + 1, iz, 401)
    c001 = py_hash_float(ix, iy, iz + 1, 401)
    c101 = py_hash_float(ix + 1, iy, iz + 1, 401)
    c011 = py_hash_float(ix, iy + 1, iz + 1, 401)
    c111 = py_hash_float(ix + 1, iy + 1, iz + 1, 401)
    x00 = lerp(c000, c100, u)
    x10 = lerp(c010, c110, u)
    x01 = lerp(c001, c101, u)
    x11 = lerp(c011, c111, u)
    return lerp(lerp(x00, x10, v), lerp(x01, x11, v), w) * 2.0 - 1.0


def py_random_normal(ix: int, iy: int, iz: int) -> tuple[float, float, float]:
    z = py_hash_float(ix, iy, iz, 100) * 2.0 - 1.0
    a = py_hash_float(ix, iy, iz, 101) * math.tau
    r = math.sqrt(max(0.0, 1.0 - z * z))
    return (math.cos(a) * r, math.sin(a) * r, z)


def bake_cluster_grid(
    domain_scale: float,
    percolation: float,
    growth_noise: float,
) -> tuple[np.ndarray, np.ndarray, int, int, int]:
    """Bake real bond-percolated Voronoi cluster IDs for the native kernel.

    The previous native renderer did a shader-local one-hop redirect. That
    produced bigger-looking cells in a few places, but it never created a real
    grown grain with a persistent identity. This is the actual Yokota-style
    bond-percolation step: neighboring cells are connected by deterministic
    bonds, union-find collapses connected components, and each component gets a
    single orientation.
    """
    i_max = math.ceil(domain_scale) + 1
    stride = 2 * i_max + 1
    if stride > MAX_NATIVE_GRID_STRIDE:
        raise SystemExit(
            f"domain scale {domain_scale:.2f} needs stride {stride}, "
            f"but the native renderer is fixed at {MAX_NATIVE_GRID_STRIDE}. "
            f"Use --domain-scale <= {MAX_NATIVE_DOMAIN_SCALE:.1f}."
        )
    total = stride * stride * stride

    def pack(ix: int, iy: int, iz: int) -> int:
        return (ix + i_max) * stride * stride + (iy + i_max) * stride + (iz + i_max)

    def unpack(key: int) -> tuple[int, int, int]:
        z = key % stride - i_max
        y = (key // stride) % stride - i_max
        x = key // (stride * stride) - i_max
        return x, y, z

    parent = np.full(total, -1, dtype=np.int32)
    size = np.ones(total, dtype=np.int32)
    for ix in range(-i_max, i_max + 1):
        for iy in range(-i_max, i_max + 1):
            for iz in range(-i_max, i_max + 1):
                nx = ix / domain_scale
                ny = iy / domain_scale
                nz = iz / domain_scale
                if nx * nx + ny * ny + nz * nz <= 1.5:
                    key = pack(ix, iy, iz)
                    parent[key] = key

    def find(k: int) -> int:
        while parent[k] != parent[parent[k]]:
            parent[k] = parent[parent[k]]
        while parent[k] != k:
            k = int(parent[k])
        return k

    def union(a: int, b: int) -> None:
        ra = find(a)
        rb = find(b)
        if ra == rb:
            return
        if size[ra] < size[rb]:
            ra, rb = rb, ra
        parent[rb] = ra
        size[ra] += size[rb]

    p = max(0.0, min(1.0, percolation))
    for ix in range(-i_max, i_max + 1):
        for iy in range(-i_max, i_max + 1):
            for iz in range(-i_max, i_max + 1):
                key = pack(ix, iy, iz)
                if parent[key] < 0:
                    continue
                # Spatially varying threshold creates growth patches instead
                # of uniform salt-and-pepper bonds.
                patch = py_value_noise(ix * 0.21, iy * 0.21, iz * 0.21)
                local_p = max(0.0, min(1.0, p + patch * growth_noise))
                for dx, dy, dz, ch in ((1, 0, 0, 10), (0, 1, 0, 20), (0, 0, 1, 30)):
                    nx = ix + dx
                    ny = iy + dy
                    nz = iz + dz
                    if nx > i_max or ny > i_max or nz > i_max:
                        continue
                    nkey = pack(nx, ny, nz)
                    if parent[nkey] < 0:
                        continue
                    edge = py_hash_float(min(ix, nx), min(iy, ny), min(iz, nz), ch)
                    if edge < local_p:
                        union(key, nkey)

    root_to_id: dict[int, int] = {}
    grid = np.full((stride, stride, stride), -1, dtype=np.int32)
    for key in range(total):
        if parent[key] < 0:
            continue
        root = find(key)
        if root not in root_to_id:
            root_to_id[root] = len(root_to_id)
        ix, iy, iz = unpack(key)
        grid[ix + i_max, iy + i_max, iz + i_max] = root_to_id[root]

    normals = np.zeros((max(1, len(root_to_id)), 3), dtype=np.float32)
    for root, grain_id in root_to_id.items():
        ix, iy, iz = unpack(root)
        normals[grain_id] = py_random_normal(ix, iy, iz)

    return grid, normals, i_max, stride, len(root_to_id)


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
def lookup_grain(grain_ids: ti.template(), cx, cy, cz, cell_offset, cell_stride):
    gx = cx + cell_offset
    gy = cy + cell_offset
    gz = cz + cell_offset
    gid = -1
    if gx >= 0 and gy >= 0 and gz >= 0 and gx < cell_stride and gy < cell_stride and gz < cell_stride:
        gid = grain_ids[gx, gy, gz]
    return gid


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
def bragg_rgb(crystal, view, d111, n_eff):
    cos_theta = ti.max(ti.abs(view.dot(crystal)), 0.035)
    lam = 2.0 * d111 * n_eff * cos_theta
    return wavelength_rgb(lam)


@ti.func
def sample_region_rgb(
    p,
    view,
    grain_ids: ti.template(),
    grain_normals: ti.template(),
    cell_offset,
    cell_stride,
    region_blur,
    d111,
    n_eff,
):
    ix = ti.cast(ti.floor(p.x), ti.i32)
    iy = ti.cast(ti.floor(p.y), ti.i32)
    iz = ti.cast(ti.floor(p.z), ti.i32)
    f = p - ti.Vector([ti.cast(ix, ti.f32), ti.cast(iy, ti.f32), ti.cast(iz, ti.f32)])

    best = 1.0e9
    second = 1.0e9
    best_col = ti.Vector([0.0, 0.0, 0.0])
    soft_col = ti.Vector([0.0, 0.0, 0.0])
    soft_w = 0.0
    blur = ti.max(region_blur, 0.0)

    for dx in ti.static(range(-1, 2)):
        for dy in ti.static(range(-1, 2)):
            for dz in ti.static(range(-1, 2)):
                cx = ix + dx
                cy = iy + dy
                cz = iz + dz
                gid = lookup_grain(grain_ids, cx, cy, cz, cell_offset, cell_stride)
                if gid >= 0:
                    jitter = ti.Vector(
                        [
                            hash41(cx, cy, cz, 0) * 0.9 + 0.05,
                            hash41(cx, cy, cz, 1) * 0.9 + 0.05,
                            hash41(cx, cy, cz, 2) * 0.9 + 0.05,
                        ]
                    )
                    delta = ti.Vector([ti.cast(dx, ti.f32), ti.cast(dy, ti.f32), ti.cast(dz, ti.f32)]) + jitter - f
                    dist = ti.sqrt(ti.max(delta.dot(delta), 0.0))
                    crystal = grain_normals[gid]
                    rgb = bragg_rgb(crystal, view, d111, n_eff)
                    if dist < best:
                        second = best
                        best = dist
                        best_col = rgb
                    elif dist < second:
                        second = dist
                    if blur > 0.001:
                        # Region blur in domain units. This intentionally
                        # averages nearby percolated grains in the Voronoi
                        # neighbourhood, so high values give the softer,
                        # cloudy/galaxy look instead of hard confetti cells.
                        w = ti.exp(-dist / blur)
                        soft_col += rgb * w
                        soft_w += w

    col = best_col
    if blur > 0.001 and soft_w > 0.00001:
        col = soft_col / soft_w

    boundary = ti.max(second - best, 0.0)
    edge = 1.0 - ti.min(ti.max((boundary - 0.02) / 0.18, 0.0), 1.0)
    sparkle = 0.55 + 0.45 * edge
    sparkle *= 1.0 - ti.min(blur * 0.22, 0.35)
    return col * sparkle


@ti.func
def aces(c):
    return ti.min((c * (2.51 * c + 0.03)) / (c * (2.43 * c + 0.59) + 0.14), 1.0)


@ti.kernel
def render_atlas(
    out: ti.template(),
    grain_ids: ti.template(),
    grain_normals: ti.template(),
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
    cell_offset: ti.i32,
    cell_stride: ti.i32,
    region_blur: ti.f32,
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
                            grain_col += sample_region_rgb(
                                q,
                                view,
                                grain_ids,
                                grain_normals,
                                cell_offset,
                                cell_stride,
                                region_blur,
                                d111,
                                n_eff,
                            )
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


def atlas_dimensions(args: argparse.Namespace) -> tuple[int, int, int, int, int]:
    yaw_angles = args.yaw_angles or args.angles
    total_views = yaw_angles * args.pitch_rows if args.view_mode == "multiview" else args.angles
    rows = math.ceil(total_views / args.cols)
    width = args.cols * args.frame_size
    height = rows * args.frame_size
    return yaw_angles, total_views, rows, width, height


def render_one(
    args: argparse.Namespace,
    preset_name: str,
    out_field,
    grain_ids,
    grain_normals,
) -> Path:
    params = PRESETS[preset_name]
    domain_scale = args.domain_scale if args.domain_scale > 0.0 else params["domain_scale"]
    percolation = args.percolation if args.percolation >= 0.0 else params["percolation"]
    region_blur = args.region_blur if args.region_blur >= 0.0 else params["region_blur"]
    yaw_angles, total_views, rows, width, height = atlas_dimensions(args)

    cluster_grid, normals, cell_offset, cell_stride, grain_count = bake_cluster_grid(
        domain_scale,
        percolation,
        args.growth_noise,
    )
    print(
        f"{preset_name}: scale={domain_scale:.2f}, p={percolation:.2f}, "
        f"blur={region_blur:.2f}, grains={grain_count}, stride={cell_stride}"
    )
    fixed_grid = np.full(
        (MAX_NATIVE_GRID_STRIDE, MAX_NATIVE_GRID_STRIDE, MAX_NATIVE_GRID_STRIDE),
        -1,
        dtype=np.int32,
    )
    start = MAX_NATIVE_GRID_OFFSET - cell_offset
    end = start + cell_stride
    fixed_grid[start:end, start:end, start:end] = cluster_grid

    fixed_normals = np.zeros((MAX_NATIVE_GRAINS, 3), dtype=np.float32)
    fixed_normals[: normals.shape[0], :] = normals

    grain_ids.from_numpy(fixed_grid)
    grain_normals.from_numpy(fixed_normals)

    t0 = time.time()
    render_atlas(
        out_field,
        grain_ids,
        grain_normals,
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
        domain_scale,
        MAX_NATIVE_GRID_OFFSET,
        MAX_NATIVE_GRID_STRIDE,
        region_blur,
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
    _, _, _, width, height = atlas_dimensions(args)
    out_field = ti.Vector.field(4, dtype=ti.u8, shape=(height, width))
    grain_ids = ti.field(
        dtype=ti.i32,
        shape=(MAX_NATIVE_GRID_STRIDE, MAX_NATIVE_GRID_STRIDE, MAX_NATIVE_GRID_STRIDE),
    )
    grain_normals = ti.Vector.field(3, dtype=ti.f32, shape=(MAX_NATIVE_GRAINS,))
    for preset in preset_names:
        render_one(args, preset, out_field, grain_ids, grain_normals)


if __name__ == "__main__":
    main()
