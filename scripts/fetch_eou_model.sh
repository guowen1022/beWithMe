#!/usr/bin/env bash
# Download the LiveKit text turn-detector model (ONNX export).
#
# The transcribe sidecar loads this on-demand; if EOU_MODEL_PATH is unset
# the /api/eou endpoint returns 503 and the client gates fail open (today's
# behavior). Run this once, point .env at the result, and disfluency
# handling lights up.
#
# Usage:
#   ./scripts/fetch_eou_model.sh                  # download to ./models/eou
#   ./scripts/fetch_eou_model.sh /custom/dir       # download to /custom/dir
#
# Then in .env:
#   EOU_MODEL_PATH=./models/eou/model_q8.onnx
#   EOU_TOKENIZER_PATH=./models/eou
#
# Requires `huggingface-cli` (pip install -U huggingface_hub).

set -euo pipefail

REPO="${EOU_HF_REPO:-livekit/turn-detector}"
DEST="${1:-$(pwd)/models/eou}"

if ! command -v huggingface-cli >/dev/null 2>&1; then
  cat <<'EOF' >&2
huggingface-cli not found. Install with:
  pip install -U huggingface_hub

Or download manually from:
  https://huggingface.co/livekit/turn-detector
and set EOU_MODEL_PATH / EOU_TOKENIZER_PATH in .env.
EOF
  exit 1
fi

mkdir -p "$DEST"
echo "→ downloading $REPO into $DEST"
huggingface-cli download "$REPO" \
  --local-dir "$DEST" \
  --local-dir-use-symlinks False

cat <<EOF

Done. Add these to .env:
  EOU_MODEL_PATH=$DEST/model_q8.onnx
  EOU_TOKENIZER_PATH=$DEST

(If model_q8.onnx is named differently in the repo, point to whichever
.onnx file you want — q8 is the quantized export.)
EOF
