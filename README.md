# Opal Path Tracer

![opal path tracer](media/opal-turntable.gif)

This is a small browser renderer for exploring opal play-of-color. It treats the stone as a volume of many tiny crystal domains, then traces wavelength-sampled rays through that structure so the color comes from optical geometry rather than a painted texture.

The project is inspired by Soma Yokota and Issei Fujishiro's work on opal rendering. Their useful abstraction is that a visible gem is too large to model sphere by sphere, but it can be represented as a polycrystalline material where each grain has its own local lattice orientation. This repo turns that idea into a compact WebGL experiment that can export turntable frames for AR.

## Run

```bash
npm install
npm run dev
```

Open the Vite URL and use:

- `pathtracer.html` for the live renderer and turntable export.
- `scroll.html` for a lightweight atlas viewer.
- `index.html` for the project landing page.

## Export Frames

The browser page can export an atlas directly. For scripted rendering, use:

```bash
node scripts/render-turntable.mjs 100 --output renders/opal-100spp.webp
```

For parallel frame rendering on Modal:

```bash
modal run modal_render.py --samples 100 --angles 72 --frame-size 512 --output-dir renders/opal-100spp-frames --concurrency 24
```

The Modal path renders individual frames and writes them locally, so stitching can happen outside Modal.

## What It Does

- Builds a compact internal grain field for the opal body.
- Gives each grain a local lattice orientation.
- Samples wavelengths during path tracing instead of choosing RGB colors up front.
- Uses the grain orientation and ray direction to produce Bragg-like flashes.
- Accumulates spectral samples and converts the result to display color at the end.
- Exports turntable imagery that can be used as an AR texture sequence.

## Current Features

- Live WebGL renderer with controls for sphere diameter, body tone, domain scale, percolation, scattering, and sample count.
- Worker-based volume bake so changes do not freeze the UI.
- Named starting presets for black, white, crystal, and fire-like opal looks.
- Turntable atlas export from the browser.
- Scripted single-frame and atlas rendering through Puppeteer.
- Optional Modal renderer for parallel frame jobs.
- Lightweight scroll viewer for exported atlases.

## Still Rough

- Opal type presets are still artistic starting points. Black opal, white opal, crystal opal, fire opal, common opal, pinfire, broadflash, and harlequin should become calibrated presets with notes about body tone, sphere diameter range, domain scale, and viewing behavior.
- Percolation needs a more physical control. The current slider changes connectivity, but it should eventually map to target domain size and cluster statistics.
- Neighboring grain orientations are too independent, which can make rotation feel jumpier than real opal footage.
- Cabochons, thin slabs, and cutaways need better path length handling than the current sphere-first turntable path.
- Lighting is intentionally simple and still needs richer reference presets for documentation renders.
- Exported frames need companion metadata for AR use: camera angle, physical scale, preset name, sphere diameter, domain scale, percolation, sample count, and playback speed.

## Files

| File | Role |
| --- | --- |
| `pathtracer.html` | Main renderer, UI, and turntable export |
| `scroll.html` | Runtime-friendly atlas viewer |
| `src/opal-volume-baker.js` | Grain-field bake and orientation codebook |
| `src/opal-volume-worker.js` | Worker wrapper for rebakes |
| `scripts/render-turntable.mjs` | Puppeteer export script |
| `modal_render.py` | Optional Modal frame renderer |

## References

- Soma Yokota and Issei Fujishiro, "Visual simulation of opal using bond percolation through the weighted Voronoi diagram and the Ewald construction," *The Visual Computer* 40, 5005-5016, 2024. <https://doi.org/10.1007/s00371-024-03504-1>
- Soma Yokota, "Visual Simulation of Opal Using Voronoi Tessellation and Ewald Construction," PhD dissertation, Keio University, 2025.

## License

MIT
