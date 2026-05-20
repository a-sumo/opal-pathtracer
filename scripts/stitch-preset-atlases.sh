#!/usr/bin/env bash
set -euo pipefail

SAMPLES="${1:-32}"
FRAME="${OPAL_FRAME_SIZE:-512}"
COLS="${OPAL_COLS:-12}"
ROWS="${OPAL_ROWS:-6}"
QUALITY="${OPAL_QUALITY:-90}"
ROOT="${OPAL_FRAME_ROOT:-renders/preset-turntable-frames-${SAMPLES}spp}"
OUT_DIR="${OPAL_ATLAS_DIR:-renders/preset-turntable-atlases-${SAMPLES}spp}"

mkdir -p "$OUT_DIR"

for preset in black white crystal fire; do
  in_dir="$ROOT/$preset"
  out="$OUT_DIR/opal-${preset}-preset-turntable-${COLS}x${ROWS}-${FRAME}-${SAMPLES}spp-q${QUALITY}.webp"
  count="$(find "$in_dir" -maxdepth 1 -type f -name '*.webp' | wc -l | tr -d ' ')"
  if [[ "$count" != "$((COLS * ROWS))" ]]; then
    echo "expected $((COLS * ROWS)) frames for $preset, found $count in $in_dir" >&2
    exit 1
  fi
  echo "stitching $preset -> $out"
  ffmpeg -nostdin -hide_banner -loglevel error -y \
    -framerate 1 \
    -pattern_type glob \
    -i "$in_dir/*.webp" \
    -vf "tile=${COLS}x${ROWS}" \
    -frames:v 1 \
    -quality "$QUALITY" \
    "$out"
done
