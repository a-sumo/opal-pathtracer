// Render a turntable atlas from pathtracer.html with the in-page Atlas Export.
//
// Usage:
//   node scripts/render-turntable.mjs probe
//   node scripts/render-turntable.mjs 100 --output renders/opal-100spp.webp
//   node scripts/render-turntable.mjs frame --angle-index 7 --output frames/007.webp
//
// The Vite dev server must be running. Override the URL with --url or OPAL_URL.

import { parseArgs } from 'node:util';
import fs from 'node:fs';
import path from 'node:path';
import puppeteer from 'puppeteer';

const { values, positionals } = parseArgs({
  args: process.argv.slice(2),
  allowPositionals: true,
  options: {
    url: { type: 'string' },
    output: { type: 'string', short: 'o' },
    'output-dir': { type: 'string' },
    angles: { type: 'string' },
    cols: { type: 'string' },
    frame: { type: 'string' },
    format: { type: 'string' },
    quality: { type: 'string' },
    preset: { type: 'string' },
    probeSeconds: { type: 'string' },
    'angle-index': { type: 'string' },
    samples: { type: 'string' },
  },
});

const arg = positionals[0] || 'probe';
const PROBE = arg === 'probe';
const FRAME_MODE = arg === 'frame' || values['angle-index'] !== undefined;
const SAMPLES = PROBE ? 0 : parseInt(FRAME_MODE ? (positionals[1] || values.samples || '100') : arg, 10);
if (!PROBE && (!Number.isFinite(SAMPLES) || SAMPLES <= 0)) {
  throw new Error(`Expected a positive sample count, got ${arg}`);
}

const URL = values.url || process.env.OPAL_URL || 'http://127.0.0.1:4200/pathtracer.html';
const OUTPUT_DIR = values['output-dir'] || process.env.OPAL_OUTPUT_DIR || '/tmp';
const ANGLES = parseInt(values.angles || process.env.OPAL_ANGLES || '72', 10);
const COLS = parseInt(values.cols || process.env.OPAL_COLS || '12', 10);
const FRAME = parseInt(values.frame || process.env.OPAL_FRAME_SIZE || '512', 10);
const FORMAT = values.format || process.env.OPAL_FORMAT || 'webp';
const QUALITY = parseInt(values.quality || process.env.OPAL_QUALITY || '90', 10);
const PRESET = values.preset || process.env.OPAL_PRESET || 'black';
const PROBE_SECONDS = parseFloat(values.probeSeconds || process.env.OPAL_PROBE_SECONDS || '25');
const ANGLE_INDEX = parseInt(values['angle-index'] || process.env.OPAL_ANGLE_INDEX || '0', 10);

// Stored hero parameter set. Values are raw slider values, not display values.
const SETTINGS = {
  preset: PRESET,
  diam: '345',
  grain: '12',
  perc: '37',
  tone: '0',
  bounces: '80',
  laue: '2',
  envInt: '5',
  volScat: '0',
};

const chromeArgs = [
  '--no-sandbox',
  '--disable-setuid-sandbox',
  '--disable-dev-shm-usage',
  '--enable-webgl',
  '--ignore-gpu-blocklist',
  '--enable-unsafe-swiftshader',
];

if (process.platform === 'darwin') {
  chromeArgs.push('--use-gl=angle', '--use-angle=metal');
} else {
  chromeArgs.push('--use-gl=angle', '--use-angle=swiftshader');
}

const launchOptions = {
  headless: 'new',
  args: chromeArgs,
  protocolTimeout: 60 * 60 * 1000,
};

if (process.env.PUPPETEER_EXECUTABLE_PATH) {
  launchOptions.executablePath = process.env.PUPPETEER_EXECUTABLE_PATH;
}

const browser = await puppeteer.launch(launchOptions);
const page = await browser.newPage();
await page.setViewport({ width: 1280, height: 900, deviceScaleFactor: 1 });

const errors = [];
page.on('console', m => {
  if (m.type() === 'error') errors.push('ERR: ' + m.text());
});
page.on('pageerror', e => errors.push('PAGE_ERR: ' + e.message));

await page.goto(URL, { waitUntil: 'domcontentloaded' });
console.log(`loaded ${URL}, waiting for initial bake...`);
await page.waitForFunction(() => {
  const el = document.getElementById('gcount');
  return el && el.textContent && el.textContent !== '-';
}, { timeout: 60000 });

await page.evaluate((s) => {
  const presetEl = document.getElementById('preset');
  presetEl.value = s.preset;
  presetEl.dispatchEvent(new Event('change', { bubbles: true }));
  for (const [id, val] of Object.entries(s)) {
    if (id === 'preset') continue;
    const el = document.getElementById(id);
    el.value = val;
    el.dispatchEvent(new Event('input', { bubbles: true }));
  }
}, SETTINGS);

await new Promise(r => setTimeout(r, 1200));
await page.waitForFunction(() => {
  const s = document.getElementById('bakeStatus');
  const g = document.getElementById('gcount');
  return (!s || !s.textContent) && g && g.textContent !== '-';
}, { timeout: 60000 });
const grains = await page.evaluate(() => document.getElementById('gcount').textContent);
console.log(`bake done: ${grains} grains`);

if (PROBE) {
  await page.evaluate((cfg) => window.__opalTurntableAPI.prepareFrame(cfg), {
    angles: ANGLES,
    samples: 100,
    frameSize: FRAME,
    distance: 2.7,
    angleIndex: 0,
  });
  const t0 = Date.now();
  await new Promise(r => setTimeout(r, PROBE_SECONDS * 1000));
  const n = await page.evaluate(() => window.__opalTurntableAPI.sampleCount());
  const dt = (Date.now() - t0) / 1000;
  console.log(`probe: ${n} samples in ${dt.toFixed(1)}s -> ${(n / dt).toFixed(2)} samples/s`);
  console.log(`estimate for ${ANGLES} angles: ${(ANGLES * 100 / (n / dt) / 60).toFixed(1)} min @ 100 spp`);
  if (errors.length) console.log('errors:\n  ' + errors.join('\n  '));
  await browser.close();
  process.exit(0);
}

if (FRAME_MODE) {
  console.log(`rendering frame ${ANGLE_INDEX + 1}/${ANGLES} @ ${SAMPLES} spp...`);
  const t0 = Date.now();
  const cfg = {
    angleIndex: ANGLE_INDEX,
    angles: ANGLES,
    samples: SAMPLES,
    frameSize: FRAME,
    distance: 2.7,
    format: FORMAT,
    quality: QUALITY / 100,
  };
  await page.evaluate((c) => window.__opalTurntableAPI.prepareFrame(c), cfg);
  let lastSamples = -1;
  while (true) {
    const n = await page.evaluate(() => window.__opalTurntableAPI.sampleCount());
    if (n !== lastSamples) {
      console.log(`  samples ${Math.min(n, SAMPLES)}/${SAMPLES}`);
      lastSamples = n;
    }
    if (n >= SAMPLES) break;
    await new Promise(r => setTimeout(r, 2000));
  }
  const dataUrl = await page.evaluate((c) => window.__opalTurntableAPI.captureFrame(c), cfg);
  const ext = FORMAT === 'jpeg' ? 'jpg' : 'webp';
  const out = values.output || path.join(
    OUTPUT_DIR,
    `opal-${PRESET}-frame-${String(ANGLE_INDEX).padStart(4, '0')}-${FRAME}-${SAMPLES}spp.${ext}`
  );
  const buf = Buffer.from(dataUrl.split(',')[1], 'base64');
  fs.mkdirSync(path.dirname(out), { recursive: true });
  fs.writeFileSync(out, buf);
  console.log(`saved: ${out} (${(buf.length / 1048576).toFixed(2)} MB)`);
  console.log(`total: ${((Date.now() - t0) / 1000).toFixed(0)}s`);
  if (errors.length) console.log('errors:\n  ' + errors.join('\n  '));
  await browser.close();
  process.exit(0);
}

await page.evaluate(() => {
  const orig = HTMLAnchorElement.prototype.click;
  HTMLAnchorElement.prototype.click = function () {
    if (this.href && this.href.startsWith('blob:')) {
      const url = this.href;
      const filename = this.download;
      fetch(url).then(r => r.blob()).then(blob => {
        const reader = new FileReader();
        reader.onloadend = () => {
          window.__atlas = reader.result;
          window.__atlasName = filename;
        };
        reader.readAsDataURL(blob);
      });
      return;
    }
    return orig.apply(this, arguments);
  };
});

await page.evaluate((cfg) => {
  const set = (id, v) => {
    const el = document.getElementById(id);
    el.step = '1';
    el.value = String(v);
    el.dispatchEvent(new Event('input', { bubbles: true }));
  };
  set('ttAngles', cfg.angles);
  set('ttSpa', cfg.samples);
  set('ttCols', cfg.cols);
  set('ttFrameSz', cfg.frame);
  set('ttJpegQ', cfg.quality);
  const fmt = document.getElementById('ttFormat');
  fmt.value = cfg.format;
  fmt.dispatchEvent(new Event('change', { bubbles: true }));
}, { angles: ANGLES, samples: SAMPLES, cols: COLS, frame: FRAME, format: FORMAT, quality: QUALITY });

await page.click('#ttOpen');
await new Promise(r => setTimeout(r, 600));
console.log(`exporting: ${ANGLES} angles x ${SAMPLES} spp...`);
const t0 = Date.now();
await page.click('#ttExport');

let last = '';
let atlas = null;
for (let i = 0; i < 28800; i++) {
  const poll = await page.evaluate(() => ({
    status: document.getElementById('ttStatus').textContent,
    has: typeof window.__atlas === 'string',
  }));
  if (poll.status !== last) {
    console.log(`  [${((Date.now() - t0) / 1000).toFixed(0).padStart(5)}s] ${poll.status}`);
    last = poll.status;
  }
  if (poll.has) {
    atlas = await page.evaluate(() => ({ name: window.__atlasName, data: window.__atlas }));
    break;
  }
  if (/error|cancelled/i.test(poll.status)) break;
  await new Promise(r => setTimeout(r, 250));
}

if (atlas) {
  const buf = Buffer.from(atlas.data.split(',')[1], 'base64');
  const out = values.output || path.join(OUTPUT_DIR, atlas.name);
  fs.mkdirSync(path.dirname(out), { recursive: true });
  fs.writeFileSync(out, buf);
  console.log(`\nsaved: ${out}  (${(buf.length / 1048576).toFixed(2)} MB)`);
} else {
  console.log('\nno atlas captured');
}
console.log(`total: ${((Date.now() - t0) / 1000).toFixed(0)}s`);
if (errors.length) console.log('errors:\n  ' + errors.join('\n  '));
await browser.close();
