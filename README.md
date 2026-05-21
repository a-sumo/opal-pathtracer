# Opal Path Tracer

## Live Demo

Try the interactive WebGL demo here: **[nano-optics-opal-pathtracer.pages.dev](https://nano-optics-opal-pathtracer.pages.dev/)**
I've also written an article on the subject: [Structural Color in Opals: From Silica Spheres to Photonic Crystals](https://armandsumo.com/posts/opals/).

![Fast opal path tracer preview](media/opal-fast-pathtracer-preview.gif)

## Run

```bash
npm install
npm run dev
```

Open the Vite URL and use:

- `fast-pathtracer.html` for the flagship live renderer.
- `pathtracer.html` for the deeper spectral reference renderer and turntable export.
- `scroll.html` for a lightweight atlas viewer.
- `index.html` for the project landing page.

## Export Frames

There are two render paths:

- The native renderer is the production batch path. It runs a Taichi GPU kernel directly on Metal, CUDA, Vulkan, or CPU and writes finished atlases without the need to launch a browser.
- The browser renderer, however, is the reference path. It reuses the live WebGL page, which is useful for fast iteration but heavier for cloud rendering due to Chromium startup costs and the fact that volume slabs are baked inside the webpage.

For quick local checks or cloud atlas generation, you can start with the native renderer:

```bash
python3 scripts/native_opal_renderer.py \
  --arch auto \
  --presets black,white,crystal,fire,harlequin \
  --angles 144 \
  --cols 12 \
  --frame-size 320 \
  --samples 1 \
  --ray-steps 3 \
  --output-dir renders/native-preset-atlases
```

On Modal, you can use the native entrypoint to avoid going through a browser.

```bash
modal run modal_native_render.py \
  --samples 1 \
  --angles 144 \
  --frame-size 320 \
  --cols 12 \
  --ray-steps 3 \
  --presets black,white,crystal,fire,harlequin \
  --output-dir renders/native-preset-atlases
```

On a Modal T4, the command above renders each 144-frame 320px atlas in roughly 17 to 19 seconds of kernel time. That is the path I'm currently using in game engines.

For a simple native multiview rig, the following script renders yaw rows at several elevations:

```bash
modal run modal_native_render.py \
  --samples 1 \
  --view-mode multiview \
  --yaw-angles 36 \
  --pitch-rows 5 \
  --pitch-min -45 \
  --pitch-max 45 \
  --frame-size 320 \
  --cols 12 \
  --ray-steps 3 \
  --presets black \
  --output-dir renders/native-multiview-black-36x5
```

The browser page can export an atlas directly. That path is usually best on a local machine with a real GPU, because the opal volume is baked once and then reused for every camera stop:

```bash
node scripts/render-turntable.mjs 100 \
  --output renders/opal-black-turntable-12x6-512-100spp.webp \
  --preset black --preset-defaults
```

The old browser-on-Modal path is still useful when you need exact parity with `pathtracer.html`, but there's the tradeoff that a one-frame-per-worker job starts a fresh browser and bakes the opal volume for every angle. The batch renderer reduces that waste by letting each worker bake once and capture a small run of views:

```bash
modal run modal_render.py \
  --samples 32 \
  --angles 144 \
  --frame-size 320 \
  --presets black,white,crystal,fire \
  --output-dir renders/preset-turntable-frames-32spp-144x320 \
  --batch-size 4 \
  --concurrency 12
```

For the browser path, a simple multiview rig renders yaw rows at several elevations. 
I haven't managed to implement 3DGS yet so his does not synthesize novel views, it only captures a denser camera set around the same baked opal. When integrating it you'd want to blend between those views.

```bash
modal run modal_render.py \
  --samples 16 \
  --view-mode multiview \
  --yaw-angles 36 \
  --pitch-rows 5 \
  --pitch-min -45 \
  --pitch-max 45 \
  --frame-size 320 \
  --presets black \
  --output-dir renders/multiview-black-36x5-16spp \
  --batch-size 6 \
  --concurrency 8
```

The current README preview was clipped from `Opal-Fast-PathTracer.mp4`:

```bash
ffmpeg -ss 00:00:17.4 -t 4.5 -i Opal-Fast-PathTracer.mp4 \
  -vf "fps=12,scale=720:-1:flags=lanczos,split[s0][s1];[s0]palettegen=max_colors=128[p];[s1][p]paletteuse=dither=bayer:bayer_scale=3" \
  media/opal-fast-pathtracer-preview.gif
```

## Current features

- Builds a compact internal grain field for the opal body.
- Gives each grain a local lattice orientation.
- Samples wavelengths during path tracing.
- Uses the grain orientation and ray direction to produce Bragg-like flashes.
- Accumulates spectral samples and converts the result to the final fragment color.
- Exports turntable imagery that can be used in game engines.

## Current Features

- Live WebGL renderer with controls for sphere diameter, body tone, domain scale, percolation, scattering, and sample count.
- Native Taichi atlas renderer for fast preset turntables and multiview sheets.
- Turntable atlas export from the browser.
- Optional Modal renderer for parallel frame or frame-batch jobs.
- Scroll viewer for exported atlases.

## Potential Improveements

- [ ] Neighboring grain orientations are too independent, which can make rotation feel jumpier than real opal footage.
- [ ] Cabochons, thin slabs, and cutaways need better path length handling than the current sphere-first turntable path.

## Files Structure

| File | Role |
| --- | --- |
| `fast-pathtracer.html` | Flagship interactive renderer for live demos and article embeds |
| `pathtracer.html` | Spectral reference renderer, detailed UI, and turntable export |
| `scroll.html` | Runtime-friendly atlas viewer |
| `src/opal-volume-baker.js` | Grain-field bake and orientation codebook |
| `src/opal-volume-worker.js` | Worker wrapper for rebakes |
| `scripts/native_opal_renderer.py` | Native Taichi atlas renderer |
| `scripts/render-turntable.mjs` | Puppeteer export script |
| `modal_native_render.py` | Modal GPU wrapper for the native renderer |
| `modal_render.py` | Optional Modal frame renderer |

## References:

- Soma Yokota and Issei Fujishiro, "Visual simulation of opal using bond percolation through the weighted Voronoi diagram and the Ewald construction," *The Visual Computer* 40, 5005-5016, 2024. <https://doi.org/10.1007/s00371-024-03504-1>
- Soma Yokota, "Visual Simulation of Opal Using Voronoi Tessellation and Ewald Construction," PhD dissertation, Keio University, 2025.

## Citation

If you cite or reuse this renderer, please use the repository citation file or this BibTeX entry:

```bibtex
@software{sumo_opal_path_tracer_2026,
  author = {Sumo, Armand},
  title = {Opal Path Tracer},
  year = {2026},
  url = {https://github.com/a-sumo/opal-pathtracer},
  license = {MIT}
}
```

## License

MIT
