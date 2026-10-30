#!/usr/bin/env bash
# Bake the tracks, build the scene, run the per-frame checks, render, encode.
#
#   ./render.sh                    # full 1920x1080 @ 60 fps, 1020 frames
#   ./render.sh --res 50 --samples 16 --frames 1-240
#
# Uses $BLENDER if set (blender -b -P ...), otherwise a Python with `pip install bpy`.
set -euo pipefail
cd "$(dirname "$0")"

node tracks.js

if [[ -n "${BLENDER:-}" ]]; then
  run() { "$BLENDER" -b -P build.py -- "$@"; }
elif command -v blender >/dev/null; then
  run() { blender -b -P build.py -- "$@"; }
else
  run() { "${PYTHON:-python3}" build.py "$@"; }
fi

# --check exits non-zero (and nothing renders) if any frame fails
run --check
run --save "" --render build/frames "$@"

ffmpeg -y -loglevel error -framerate 60 -i build/frames/frame_%04d.png \
  -c:v libx264 -pix_fmt yuv420p -crf 16 -movflags +faststart build/joynuts_make_it_crunch.mp4
echo "wrote build/joynuts_make_it_crunch.mp4"
