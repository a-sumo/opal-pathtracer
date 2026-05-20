#!/usr/bin/env bash
set -euo pipefail

SAMPLES="${1:-32}"
ANGLES="${OPAL_ANGLES:-72}"
FRAME="${OPAL_FRAME_SIZE:-512}"
COLS="${OPAL_COLS:-12}"
FORMAT="${OPAL_FORMAT:-webp}"
QUALITY="${OPAL_QUALITY:-90}"
URL="${OPAL_URL:-http://127.0.0.1:4326/pathtracer.html}"
OUT_DIR="${OPAL_OUTPUT_DIR:-renders/preset-turntables-${SAMPLES}spp}"

mkdir -p "$OUT_DIR"

for preset in black white crystal fire; do
  out="$OUT_DIR/opal-${preset}-preset-turntable-${COLS}x$(( (ANGLES + COLS - 1) / COLS ))-${FRAME}-${SAMPLES}spp-q${QUALITY}.${FORMAT}"
  echo "rendering $preset -> $out"
  node scripts/render-turntable.mjs "$SAMPLES" \
    --url "$URL" \
    --output "$out" \
    --preset "$preset" \
    --preset-defaults \
    --angles "$ANGLES" \
    --cols "$COLS" \
    --frame "$FRAME" \
    --format "$FORMAT" \
    --quality "$QUALITY"
done
