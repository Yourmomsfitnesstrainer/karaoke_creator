#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
mkdir -p "$ROOT_DIR/demo"
if [[ -n "${KARAOKE_FFMPEG:-}" ]]; then
  FFMPEG_BIN="$KARAOKE_FFMPEG"
elif [[ -x /opt/homebrew/opt/ffmpeg-full/bin/ffmpeg ]]; then
  FFMPEG_BIN=/opt/homebrew/opt/ffmpeg-full/bin/ffmpeg
else
  FFMPEG_BIN=ffmpeg
fi
if [[ -x "$ROOT_DIR/.venv/bin/karaoke-gen" ]]; then
  KARAOKE_BIN="$ROOT_DIR/.venv/bin/karaoke-gen"
else
  KARAOKE_BIN=karaoke-gen
fi

"$FFMPEG_BIN" -y -hide_banner -loglevel error \
  -f lavfi -i "sine=frequency=220:duration=8" \
  -af "volume=0.12" "$ROOT_DIR/demo/tone.wav"

"$KARAOKE_BIN" generate \
  --audio "$ROOT_DIR/demo/tone.wav" \
  --lyrics "$ROOT_DIR/demo/lyrics.txt" \
  --output "$ROOT_DIR/demo/result" \
  --backend uniform \
  --skip-separation \
  --audio-mode original

echo "Demo created at $ROOT_DIR/demo/result/karaoke.mp4"
