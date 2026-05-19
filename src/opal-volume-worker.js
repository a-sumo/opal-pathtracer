// Web Worker wrapper for opal cell texture baking.
// Runs the percolation off the main thread so the page stays responsive.

import { bakeOpalCellTextures } from './opal-volume-baker.js';

self.onmessage = (e) => {
  const { scale, perc, noiseFreq, noiseAmp } = e.data;
  const result = bakeOpalCellTextures(scale, perc, noiseFreq || 0.35, noiseAmp || 0.25);
  self.postMessage(result, [result.siteData.buffer, result.grainData.buffer, result.codebook.buffer]);
};
