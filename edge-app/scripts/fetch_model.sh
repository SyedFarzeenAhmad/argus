#!/usr/bin/env bash
# Downloads the MVP pothole model and exports it to ONNX for the edge app.
#
# Model: tahaUgan/pothole-yolo11n on Hugging Face (YOLO11n, classes 0=Pothole,
# 1=Sewage-Manhole; weights CC-BY-4.0). Trained with Ultralytics, which is AGPL-3.0,
# so this is a PROTOTYPE model only — see docs/03 "Model licensing". It is replaced
# by CV-Perception's own model once trained; the app only needs pothole.onnx.
set -euo pipefail

here="$(cd "$(dirname "$0")/.." && pwd)"
out="$here/app/src/main/assets/models/pothole.onnx"
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT

curl -fsSL -o "$work/pothole.pt" \
  https://huggingface.co/tahaUgan/pothole-yolo11n/resolve/main/pothole2v.pt

uv venv -q --python 3.12 "$work/.venv"
uv pip install -q --python "$work/.venv/bin/python" ultralytics onnx onnxslim
(cd "$work" && .venv/bin/python -c "
from ultralytics import YOLO
YOLO('pothole.pt').export(format='onnx', imgsz=640, opset=17, simplify=True, dynamic=False)
")

mkdir -p "$(dirname "$out")"
cp "$work/pothole.onnx" "$out"
echo "wrote $out"
