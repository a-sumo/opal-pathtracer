// opal-volume-baker.js
// Bakes a 3D volume texture for the opal cabochon.
// Each voxel stores RGB = crystal normal (packed), A = 255 if inside ellipsoid.
//
// Uses the full Yokota pipeline:
//   - Additively weighted Voronoi with Perlin noise
//   - Full union-find bond percolation (not hop-limited)
//   - Shoemake uniform random crystal tilts
//   - Spatially varying percolation threshold via Perlin noise

// ── Hash utilities (same as slab-voronoi-baker) ──
function ihash(x) {
  x = ((x >> 16) ^ x) * 0x45d9f3b | 0;
  x = ((x >> 16) ^ x) * 0x45d9f3b | 0;
  x = (x >> 16) ^ x;
  return x;
}

function hash33(ix, iy, iz) {
  const seed = (ix * 374761393 + iy * 668265263 + iz * 1274126177) | 0;
  const h1 = ihash(seed);
  const h2 = ihash(seed + 0x9E3779B9 | 0);
  const h3 = ihash(seed + 0x517CC1B7 | 0);
  return [
    (h1 & 0xFFFF) / 32768.0 - 1.0,
    (h2 & 0xFFFF) / 32768.0 - 1.0,
    (h3 & 0xFFFF) / 32768.0 - 1.0,
  ];
}

function hashFloat(ix, iy, iz, ch) {
  const seed = (ix * 374761393 + iy * 668265263 + iz * 1274126177 + ch * 0x27d4eb2d) | 0;
  return (ihash(seed) & 0xFFFF) / 65535.0;
}

// ── Perlin noise ──
function fade(t) { return t * t * t * (t * (t * 6 - 15) + 10); }
function lerp(a, b, t) { return a + t * (b - a); }
function grad3(ix, iy, iz, fx, fy, fz) {
  const h = hash33(ix, iy, iz);
  return h[0] * fx + h[1] * fy + h[2] * fz;
}
function perlin3(x, y, z) {
  const ix = Math.floor(x), iy = Math.floor(y), iz = Math.floor(z);
  const fx = x - ix, fy = y - iy, fz = z - iz;
  const u = fade(fx), v = fade(fy), w = fade(fz);
  return lerp(
    lerp(lerp(grad3(ix,iy,iz,fx,fy,fz), grad3(ix+1,iy,iz,fx-1,fy,fz), u),
         lerp(grad3(ix,iy+1,iz,fx,fy-1,fz), grad3(ix+1,iy+1,iz,fx-1,fy-1,fz), u), v),
    lerp(lerp(grad3(ix,iy,iz+1,fx,fy,fz-1), grad3(ix+1,iy,iz+1,fx-1,fy,fz-1), u),
         lerp(grad3(ix,iy+1,iz+1,fx,fy-1,fz-1), grad3(ix+1,iy+1,iz+1,fx-1,fy-1,fz-1), u), v),
    w);
}

// ── Shoemake uniform quaternion → [111] normal ──
function randomGrainNormal(ix, iy, iz) {
  const u0 = hashFloat(ix, iy, iz, 100);
  const u1 = hashFloat(ix, iy, iz, 101);
  const u2 = hashFloat(ix, iy, iz, 102);
  const s1 = Math.sqrt(1 - u0), s0 = Math.sqrt(u0);
  const a1 = 2 * Math.PI * u1, a2 = 2 * Math.PI * u2;
  const qw = s1 * Math.cos(a1), qx = s1 * Math.sin(a1);
  const qy = s0 * Math.cos(a2), qz = s0 * Math.sin(a2);
  const inv3 = 1 / Math.sqrt(3);
  const vx = inv3, vy = inv3, vz = inv3;
  const tx = 2 * (qy * vz - qz * vy);
  const ty = 2 * (qz * vx - qx * vz);
  const tz = 2 * (qx * vy - qy * vx);
  let nx = vx + qw * tx + (qy * tz - qz * ty);
  let ny = vy + qw * ty + (qz * tx - qx * tz);
  let nz = vz + qw * tz + (qx * ty - qy * tx);
  const len = Math.sqrt(nx*nx + ny*ny + nz*nz) || 1;
  return [nx/len, ny/len, nz/len];
}

/**
 * Bake a 3D volume texture for an opal cabochon.
 * @param {number} res - Resolution per axis (e.g. 64 for 64^3)
 * @param {number} domainScale - Voronoi cells per unit (higher = more cells)
 * @param {number} percolation - Bond percolation threshold (0-0.5)
 * @param {number} noiseFreq - Perlin noise frequency for weights
 * @param {number} noiseAmp - Perlin noise amplitude for Voronoi weights
 * @returns {{ data: Uint8Array, resolution: number }}
 */
export function bakeOpalVolume(res = 64, domainScale = 24, percolation = 0.4, noiseFreq = 0.4, noiseAmp = 0.3) {
  const R = res;
  const data = new Uint8Array(R * R * R * 4);
  const jitterAmp = 0.45;

  // Integer-packed cell keys instead of string keys.
  // Cell coords range from -iMax to +iMax on each axis.
  const iMax = Math.ceil(domainScale) + 1;
  const STRIDE = 2 * iMax + 1;
  const S2 = STRIDE * STRIDE;
  const packKey = (ix, iy, iz) => (ix + iMax) * S2 + (iy + iMax) * STRIDE + (iz + iMax);

  // Flat Int32Array union-find: -1 = not a cell, else parent key.
  // ~500 KB vs the old 26 MB (cellForVoxel + insideVoxel + string Maps).
  const parent = new Int32Array(STRIDE * S2).fill(-1);
  let cellCount = 0;

  // Enumerate cells inside the sphere (with margin for jitter)
  for (let ix = -iMax; ix <= iMax; ix++)
  for (let iy = -iMax; iy <= iMax; iy++)
  for (let iz = -iMax; iz <= iMax; iz++) {
    const nx = ix / domainScale, ny = iy / domainScale, nz = iz / domainScale;
    if (nx * nx + ny * ny + nz * nz <= 1.5) {
      const key = packKey(ix, iy, iz);
      parent[key] = key;
      cellCount++;
    }
  }

  console.log(`[opal-baker] ${cellCount} Voronoi cells found`);

  // Union-find with path splitting
  function find(k) {
    while (parent[k] !== parent[parent[k]]) parent[k] = parent[parent[k]];
    let p = k; while (parent[p] !== p) p = parent[p];
    return p;
  }
  function union(a, b) {
    const ra = find(a), rb = find(b);
    if (ra !== rb) parent[rb] = ra;
  }

  // Bond percolation with spatially varying threshold
  for (let ix = -iMax; ix <= iMax; ix++)
  for (let iy = -iMax; iy <= iMax; iy++)
  for (let iz = -iMax; iz <= iMax; iz++) {
    const key = packKey(ix, iy, iz);
    if (parent[key] === -1) continue;
    for (let d = 0; d < 3; d++) {
      const dx = d === 0 ? 1 : 0, dy = d === 1 ? 1 : 0, dz = d === 2 ? 1 : 0;
      const ni = ix + dx, nj = iy + dy, nk = iz + dz;
      if (ni > iMax || nj > iMax || nk > iMax) continue;
      const nkey = packKey(ni, nj, nk);
      if (parent[nkey] === -1) continue;
      const localP = percolation + perlin3(ix * noiseFreq * 0.7, iy * noiseFreq * 0.7, iz * noiseFreq * 0.7) * 0.15;
      const lo = Math.min(ix, ni) * 1000 + Math.max(ix, ni);
      const hi = Math.min(iy, nj) * 1000 + Math.max(iy, nj);
      const edgeVal = hashFloat(lo, hi, Math.min(iz, nk) * 1000 + Math.max(iz, nk), 200);
      if (edgeVal < Math.max(0, Math.min(0.55, localP))) {
        union(key, nkey);
      }
    }
  }

  // Count clusters
  const roots = new Set();
  for (let i = 0; i < parent.length; i++) if (parent[i] !== -1) roots.add(find(i));
  console.log(`[opal-baker] ${roots.size} grains after percolation`);

  // Grain normals cache keyed by root integer
  const normalCache = new Map();
  function getGrainNormal(key) {
    const root = find(key);
    let cached = normalCache.get(root);
    if (cached) return cached;
    // Unpack root → cell coordinates
    const kz = (root % STRIDE) - iMax;
    const ky = (Math.floor(root / STRIDE) % STRIDE) - iMax;
    const kx = Math.floor(root / S2) - iMax;
    cached = randomGrainNormal(kx, ky, kz);
    normalCache.set(root, cached);
    return cached;
  }

  // Write voxels: re-run Voronoi per voxel (trades ~2× CPU for 26 MB less RAM)
  for (let iz = 0; iz < R; iz++) {
    const nz = (iz + 0.5) / R * 2 - 1;
    for (let iy = 0; iy < R; iy++) {
      const ny = (iy + 0.5) / R * 2 - 1;
      for (let ix = 0; ix < R; ix++) {
        const nx = (ix + 0.5) / R * 2 - 1;
        const base = (iz * R * R + iy * R + ix) * 4;

        if (nx * nx + ny * ny + nz * nz > 1.0) {
          data[base] = 128; data[base + 1] = 128; data[base + 2] = 128; data[base + 3] = 0;
          continue;
        }

        const px = nx * domainScale, py = ny * domainScale, pz = nz * domainScale;
        const cix = Math.floor(px), ciy = Math.floor(py), ciz = Math.floor(pz);
        const fx = px - cix, fy = py - ciy, fz = pz - ciz;

        let d1 = 100;
        let cellX = 0, cellY = 0, cellZ = 0;

        for (let nnx = -1; nnx <= 1; nnx++)
        for (let nny = -1; nny <= 1; nny++)
        for (let nnz = -1; nnz <= 1; nnz++) {
          const cx = cix + nnx, cy = ciy + nny, cz = ciz + nnz;
          const h = hash33(cx, cy, cz);
          const sx = nnx + (h[0] * 0.5 + 0.5) * jitterAmp * 2 + (1 - jitterAmp) * 0.5 - fx;
          const sy = nny + (h[1] * 0.5 + 0.5) * jitterAmp * 2 + (1 - jitterAmp) * 0.5 - fy;
          const sz = nnz + (h[2] * 0.5 + 0.5) * jitterAmp * 2 + (1 - jitterAmp) * 0.5 - fz;
          const eucDist = Math.sqrt(sx * sx + sy * sy + sz * sz);
          const weight = perlin3(cx * noiseFreq, cy * noiseFreq, cz * noiseFreq) * noiseAmp;
          const weightedDist = eucDist - weight;

          if (weightedDist < d1) {
            d1 = weightedDist;
            cellX = cx; cellY = cy; cellZ = cz;
          }
        }

        const n = getGrainNormal(packKey(cellX, cellY, cellZ));
        data[base]     = Math.round((n[0] * 0.5 + 0.5) * 255);
        data[base + 1] = Math.round((n[1] * 0.5 + 0.5) * 255);
        data[base + 2] = Math.round((n[2] * 0.5 + 0.5) * 255);
        data[base + 3] = 255;
      }
    }
  }

  return { data, resolution: R };
}

/**
 * Bake an opal volume with grain-ID + codebook format for Ewald-construction rendering.
 *
 * Volume texture (RGBA uint8):
 *   R, G = grain ID (16-bit, little-endian: ID = R + G*256)
 *   B    = distance to nearest Voronoi boundary (0-255, normalised to cell radius)
 *   A    = 255 inside ellipsoid, 0 outside
 *
 * Codebook (Float32Array, 4 floats per grain):
 *   [qx, qy, qz, qw] — full Shoemake quaternion for the grain tilt
 *
 * @returns {{ data: Uint8Array, codebook: Float32Array, grainCount: number, resolution: number }}
 */
export function bakeOpalVolumeEwald(res = 128, domainScale = 12, percolation = 0.42, noiseFreq = 0.35, noiseAmp = 0.25) {
  const R = res;
  const data = new Uint8Array(R * R * R * 4);
  const jitterAmp = 0.45;

  const iMax = Math.ceil(domainScale) + 1;
  const STRIDE = 2 * iMax + 1;
  const S2 = STRIDE * STRIDE;
  const packKey = (ix, iy, iz) => (ix + iMax) * S2 + (iy + iMax) * STRIDE + (iz + iMax);

  // Union-find
  const parent = new Int32Array(STRIDE * S2).fill(-1);
  let cellCount = 0;

  for (let ix = -iMax; ix <= iMax; ix++)
  for (let iy = -iMax; iy <= iMax; iy++)
  for (let iz = -iMax; iz <= iMax; iz++) {
    const nx = ix / domainScale, ny = iy / domainScale, nz = iz / domainScale;
    if (nx * nx + ny * ny + nz * nz <= 1.5) {
      parent[packKey(ix, iy, iz)] = packKey(ix, iy, iz);
      cellCount++;
    }
  }

  function find(k) {
    while (parent[k] !== parent[parent[k]]) parent[k] = parent[parent[k]];
    let p = k; while (parent[p] !== p) p = parent[p];
    return p;
  }
  function union(a, b) {
    const ra = find(a), rb = find(b);
    if (ra !== rb) parent[rb] = ra;
  }

  // Bond percolation
  for (let ix = -iMax; ix <= iMax; ix++)
  for (let iy = -iMax; iy <= iMax; iy++)
  for (let iz = -iMax; iz <= iMax; iz++) {
    const key = packKey(ix, iy, iz);
    if (parent[key] === -1) continue;
    for (let d = 0; d < 3; d++) {
      const dx = d === 0 ? 1 : 0, dy = d === 1 ? 1 : 0, dz = d === 2 ? 1 : 0;
      const ni = ix + dx, nj = iy + dy, nk = iz + dz;
      if (ni > iMax || nj > iMax || nk > iMax) continue;
      const nkey = packKey(ni, nj, nk);
      if (parent[nkey] === -1) continue;
      const localP = percolation + perlin3(ix * noiseFreq * 0.7, iy * noiseFreq * 0.7, iz * noiseFreq * 0.7) * 0.15;
      const lo = Math.min(ix, ni) * 1000 + Math.max(ix, ni);
      const hi = Math.min(iy, nj) * 1000 + Math.max(iy, nj);
      const edgeVal = hashFloat(lo, hi, Math.min(iz, nk) * 1000 + Math.max(iz, nk), 200);
      // Clamp to [0, 1] so the slider covers the full range. A previous revision
      // capped this at 0.55, which silently pinned every "high percolation"
      // slider position to the same cluster size — user could never reach the
      // merged-amalgamation regime. See docs/opal-pathtracer-rendering.md §1.2.
      if (edgeVal < Math.max(0, Math.min(1, localP))) {
        union(key, nkey);
      }
    }
  }

  // Assign sequential IDs to grain roots and build quaternion codebook
  const rootToID = new Map();
  let nextID = 0;
  for (let i = 0; i < parent.length; i++) {
    if (parent[i] === -1) continue;
    const root = find(i);
    if (!rootToID.has(root)) rootToID.set(root, nextID++);
  }
  const grainCount = nextID;
  console.log(`[opal-baker-ewald] ${grainCount} grains after percolation`);

  // Build codebook: full Shoemake quaternion per grain
  const codebook = new Float32Array(grainCount * 4);
  for (const [root, id] of rootToID) {
    const kz = (root % STRIDE) - iMax;
    const ky = (Math.floor(root / STRIDE) % STRIDE) - iMax;
    const kx = Math.floor(root / S2) - iMax;
    // Shoemake uniform quaternion
    const u0 = hashFloat(kx, ky, kz, 100);
    const u1 = hashFloat(kx, ky, kz, 101);
    const u2 = hashFloat(kx, ky, kz, 102);
    const s1 = Math.sqrt(1 - u0), s0 = Math.sqrt(u0);
    const a1 = 2 * Math.PI * u1, a2 = 2 * Math.PI * u2;
    codebook[id * 4]     = s1 * Math.sin(a1); // qx
    codebook[id * 4 + 1] = s0 * Math.cos(a2); // qy
    codebook[id * 4 + 2] = s0 * Math.sin(a2); // qz
    codebook[id * 4 + 3] = s1 * Math.cos(a1); // qw
  }

  // Grain ID cache keyed by cell coordinate key
  const idCache = new Map();
  function getGrainID(cellKey) {
    let cached = idCache.get(cellKey);
    if (cached !== undefined) return cached;
    if (parent[cellKey] === -1) { idCache.set(cellKey, -1); return -1; }
    const id = rootToID.get(find(cellKey));
    idCache.set(cellKey, id !== undefined ? id : -1);
    return id !== undefined ? id : -1;
  }

  // Write voxels
  for (let iz = 0; iz < R; iz++) {
    const nz = (iz + 0.5) / R * 2 - 1;
    for (let iy = 0; iy < R; iy++) {
      const ny = (iy + 0.5) / R * 2 - 1;
      for (let ix = 0; ix < R; ix++) {
        const nx = (ix + 0.5) / R * 2 - 1;
        const base = (iz * R * R + iy * R + ix) * 4;

        if (nx * nx + ny * ny + nz * nz > 1.0) {
          data[base] = 0; data[base + 1] = 0; data[base + 2] = 0; data[base + 3] = 0;
          continue;
        }

        const px = nx * domainScale, py = ny * domainScale, pz = nz * domainScale;
        const cix = Math.floor(px), ciy = Math.floor(py), ciz = Math.floor(pz);
        const fx = px - cix, fy = py - ciy, fz = pz - ciz;

        let d1 = 1e9;
        let cellX = 0, cellY = 0, cellZ = 0;

        for (let nnx = -1; nnx <= 1; nnx++)
        for (let nny = -1; nny <= 1; nny++)
        for (let nnz = -1; nnz <= 1; nnz++) {
          const cx = cix + nnx, cy = ciy + nny, cz = ciz + nnz;
          const h = hash33(cx, cy, cz);
          const sx = nnx + (h[0] * 0.5 + 0.5) * jitterAmp * 2 + (1 - jitterAmp) * 0.5 - fx;
          const sy = nny + (h[1] * 0.5 + 0.5) * jitterAmp * 2 + (1 - jitterAmp) * 0.5 - fy;
          const sz = nnz + (h[2] * 0.5 + 0.5) * jitterAmp * 2 + (1 - jitterAmp) * 0.5 - fz;
          const eucDist = Math.sqrt(sx * sx + sy * sy + sz * sz);
          const weight = perlin3(cx * noiseFreq, cy * noiseFreq, cz * noiseFreq) * noiseAmp;
          const weightedDist = eucDist - weight;

          if (weightedDist < d1) {
            d1 = weightedDist;
            cellX = cx; cellY = cy; cellZ = cz;
          }
        }

        const grainID = getGrainID(packKey(cellX, cellY, cellZ));
        const id = grainID >= 0 ? grainID : 0;

        data[base]     = id & 0xFF;          // grain ID low
        data[base + 1] = (id >> 8) & 0xFF;   // grain ID high
        data[base + 2] = 0;                   // filled by distance transform below
        data[base + 3] = 255;                 // inside
      }
    }
  }

  // ── Grain-boundary distance transform ──
  // Compute Euclidean distance (in voxels) from each inside voxel to the nearest
  // voxel with a different grain ID. This gives smooth, grain-level boundaries
  // that correspond to actual percolated domains (not raw Voronoi cells).
  //
  // Uses a separable exact EDT (Felzenszwalb & Huttenlocher 2012).

  // Helper: 1D exact EDT using parabola envelope
  function edt1d(f, d, n) {
    // f[i] = squared distance input, d[i] = squared distance output
    const v = new Int32Array(n);  // locations of parabolas
    const z = new Float32Array(n + 1);  // boundaries between parabolas
    let k = 0;
    v[0] = 0;
    z[0] = -1e20;
    z[1] = 1e20;
    for (let q = 1; q < n; q++) {
      let s = ((f[q] + q * q) - (f[v[k]] + v[k] * v[k])) / (2 * q - 2 * v[k]);
      while (s <= z[k]) {
        k--;
        s = ((f[q] + q * q) - (f[v[k]] + v[k] * v[k])) / (2 * q - 2 * v[k]);
      }
      k++;
      v[k] = q;
      z[k] = s;
      z[k + 1] = 1e20;
    }
    k = 0;
    for (let q = 0; q < n; q++) {
      while (z[k + 1] < q) k++;
      d[q] = (q - v[k]) * (q - v[k]) + f[v[k]];
    }
  }

  const INF = 1e10;
  const dist = new Float32Array(R * R * R);

  // Seed: boundary voxels (6-connected neighbor has different grain or is outside)
  for (let iz = 0; iz < R; iz++)
  for (let iy = 0; iy < R; iy++)
  for (let ix = 0; ix < R; ix++) {
    const idx = iz * R * R + iy * R + ix;
    if (data[idx * 4 + 3] === 0) { dist[idx] = 0; continue; } // outside
    const myID = data[idx * 4] + data[idx * 4 + 1] * 256;
    let onBoundary = false;
    for (const [dx, dy, dz] of [[1,0,0],[-1,0,0],[0,1,0],[0,-1,0],[0,0,1],[0,0,-1]]) {
      const nx = ix+dx, ny = iy+dy, nz = iz+dz;
      if (nx < 0 || nx >= R || ny < 0 || ny >= R || nz < 0 || nz >= R) { onBoundary = true; break; }
      const nIdx = nz * R * R + ny * R + nx;
      if (data[nIdx * 4 + 3] === 0) { onBoundary = true; break; }
      const nID = data[nIdx * 4] + data[nIdx * 4 + 1] * 256;
      if (nID !== myID) { onBoundary = true; break; }
    }
    dist[idx] = onBoundary ? 0 : INF;
  }

  // Separable EDT: X pass, Y pass, Z pass
  const lineF = new Float32Array(R);
  const lineD = new Float32Array(R);

  // X pass
  for (let iz = 0; iz < R; iz++)
  for (let iy = 0; iy < R; iy++) {
    const base = iz * R * R + iy * R;
    for (let ix = 0; ix < R; ix++) lineF[ix] = dist[base + ix];
    edt1d(lineF, lineD, R);
    for (let ix = 0; ix < R; ix++) dist[base + ix] = lineD[ix];
  }
  // Y pass
  for (let iz = 0; iz < R; iz++)
  for (let ix = 0; ix < R; ix++) {
    for (let iy = 0; iy < R; iy++) lineF[iy] = dist[iz * R * R + iy * R + ix];
    edt1d(lineF, lineD, R);
    for (let iy = 0; iy < R; iy++) dist[iz * R * R + iy * R + ix] = lineD[iy];
  }
  // Z pass
  for (let iy = 0; iy < R; iy++)
  for (let ix = 0; ix < R; ix++) {
    for (let iz = 0; iz < R; iz++) lineF[iz] = dist[iz * R * R + iy * R + ix];
    edt1d(lineF, lineD, R);
    for (let iz = 0; iz < R; iz++) dist[iz * R * R + iy * R + ix] = lineD[iz];
  }

  // Write B channel: sqrt(squared distance) → [0, 255], scaled so max useful range ≈ 8 voxels
  const distScale = 255.0 / 8.0;  // 8 voxels = max distance we care about
  for (let i = 0; i < R * R * R; i++) {
    if (data[i * 4 + 3] === 0) continue;
    const d = Math.sqrt(dist[i]);
    data[i * 4 + 2] = Math.min(Math.round(d * distScale), 255);
  }

  return { data, codebook, grainCount, resolution: R };
}

/**
 * Bake cell-level textures for analytical Voronoi evaluation in the shader.
 *
 * Instead of a large voxel volume, outputs two small 3D textures (STRIDE³ each):
 *   siteData (Float32 RGBA): [offsetX, offsetY, offsetZ, perlinWeight] per cell
 *   grainData (Uint8 RGBA):  [idLow, idHigh, 0, 255] per cell (0,0,0,0 if invalid)
 *
 * The shader reconstructs exact Voronoi boundaries analytically using these
 * pre-computed site positions and grain IDs. No voxel quantization.
 *
 * @returns {{ siteData, grainData, codebook, grainCount, stride, offset }}
 */
// Hard cap so we never allocate insane cell-texture buffers. At scale=64,
// totalCells = 131³ ≈ 2.25M and siteData (Float32 RGBA) is ~36 MB — already
// the upper end of what the page can rebake without freezing. The slider in
// opal-pathtracer.html is capped to match, but defend the worker too in case
// a paste/preset slips a higher value through.
const MAX_DOMAIN_SCALE = 64;

export function bakeOpalCellTextures(domainScale = 24, percolation = 0.42, noiseFreq = 0.35, noiseAmp = 0.25) {
  if (domainScale > MAX_DOMAIN_SCALE) {
    console.warn(`[opal-cell-baker] clamping domainScale ${domainScale} → ${MAX_DOMAIN_SCALE}`);
    domainScale = MAX_DOMAIN_SCALE;
  }
  const jitterAmp = 0.45;
  const iMax = Math.ceil(domainScale) + 1;
  const STRIDE = 2 * iMax + 1;
  const S2 = STRIDE * STRIDE;
  const totalCells = STRIDE * S2;
  const packKey = (ix, iy, iz) => (ix + iMax) * S2 + (iy + iMax) * STRIDE + (iz + iMax);

  // Union-find
  const parent = new Int32Array(totalCells).fill(-1);

  for (let ix = -iMax; ix <= iMax; ix++)
  for (let iy = -iMax; iy <= iMax; iy++)
  for (let iz = -iMax; iz <= iMax; iz++) {
    const nx = ix / domainScale, ny = iy / domainScale, nz = iz / domainScale;
    if (nx * nx + ny * ny + nz * nz <= 1.5) {
      parent[packKey(ix, iy, iz)] = packKey(ix, iy, iz);
    }
  }

  function find(k) {
    while (parent[k] !== parent[parent[k]]) parent[k] = parent[parent[k]];
    let p = k; while (parent[p] !== p) p = parent[p];
    return p;
  }
  function union(a, b) {
    const ra = find(a), rb = find(b);
    if (ra !== rb) parent[rb] = ra;
  }

  // Bond percolation (same as bakeOpalVolumeEwald)
  for (let ix = -iMax; ix <= iMax; ix++)
  for (let iy = -iMax; iy <= iMax; iy++)
  for (let iz = -iMax; iz <= iMax; iz++) {
    const key = packKey(ix, iy, iz);
    if (parent[key] === -1) continue;
    for (let d = 0; d < 3; d++) {
      const dx = d === 0 ? 1 : 0, dy = d === 1 ? 1 : 0, dz = d === 2 ? 1 : 0;
      const ni = ix + dx, nj = iy + dy, nk = iz + dz;
      if (ni > iMax || nj > iMax || nk > iMax) continue;
      const nkey = packKey(ni, nj, nk);
      if (parent[nkey] === -1) continue;
      const localP = percolation + perlin3(ix * noiseFreq * 0.7, iy * noiseFreq * 0.7, iz * noiseFreq * 0.7) * 0.15;
      const lo = Math.min(ix, ni) * 1000 + Math.max(ix, ni);
      const hi = Math.min(iy, nj) * 1000 + Math.max(iy, nj);
      const edgeVal = hashFloat(lo, hi, Math.min(iz, nk) * 1000 + Math.max(iz, nk), 200);
      // Clamp to [0, 1]. The old 0.55 cap pinned the high-slider regime to a
      // single cluster size and made the slider feel "erratic above half" —
      // see the fix note in bakeOpalVolumeEwald for the full explanation.
      if (edgeVal < Math.max(0, Math.min(1, localP))) {
        union(key, nkey);
      }
    }
  }

  // Assign grain IDs + build codebook
  const rootToID = new Map();
  let nextID = 0;
  for (let i = 0; i < totalCells; i++) {
    if (parent[i] === -1) continue;
    const root = find(i);
    if (!rootToID.has(root)) rootToID.set(root, nextID++);
  }
  const grainCount = nextID;
  console.log(`[opal-cell-baker] ${grainCount} grains after percolation`);

  const codebook = new Float32Array(grainCount * 4);
  for (const [root, id] of rootToID) {
    const kz = (root % STRIDE) - iMax;
    const ky = (Math.floor(root / STRIDE) % STRIDE) - iMax;
    const kx = Math.floor(root / S2) - iMax;
    const u0 = hashFloat(kx, ky, kz, 100);
    const u1 = hashFloat(kx, ky, kz, 101);
    const u2 = hashFloat(kx, ky, kz, 102);
    const s1 = Math.sqrt(1 - u0), s0 = Math.sqrt(u0);
    const a1 = 2 * Math.PI * u1, a2 = 2 * Math.PI * u2;
    codebook[id * 4]     = s1 * Math.sin(a1);
    codebook[id * 4 + 1] = s0 * Math.cos(a2);
    codebook[id * 4 + 2] = s0 * Math.sin(a2);
    codebook[id * 4 + 3] = s1 * Math.cos(a1);
  }

  // Build cell textures
  const siteData = new Float32Array(totalCells * 4);
  const grainData = new Uint8Array(totalCells * 4);

  for (let ix = -iMax; ix <= iMax; ix++)
  for (let iy = -iMax; iy <= iMax; iy++)
  for (let iz = -iMax; iz <= iMax; iz++) {
    const key = packKey(ix, iy, iz);
    const base = key * 4;

    // Site position offset within cell [0, 1]
    const h = hash33(ix, iy, iz);
    siteData[base]     = (h[0] * 0.5 + 0.5) * jitterAmp * 2 + (1 - jitterAmp) * 0.5;
    siteData[base + 1] = (h[1] * 0.5 + 0.5) * jitterAmp * 2 + (1 - jitterAmp) * 0.5;
    siteData[base + 2] = (h[2] * 0.5 + 0.5) * jitterAmp * 2 + (1 - jitterAmp) * 0.5;
    // Perlin weight for additive Voronoi
    siteData[base + 3] = perlin3(ix * noiseFreq, iy * noiseFreq, iz * noiseFreq) * noiseAmp;

    // Grain ID
    if (parent[key] === -1) {
      grainData[base] = 0; grainData[base+1] = 0; grainData[base+2] = 0; grainData[base+3] = 0;
    } else {
      const root = find(key);
      const id = rootToID.get(root);
      grainData[base]     = id & 0xFF;
      grainData[base + 1] = (id >> 8) & 0xFF;
      grainData[base + 2] = 0;
      grainData[base + 3] = 255;
    }
  }

  return { siteData, grainData, codebook, grainCount, stride: STRIDE, offset: iMax };
}
