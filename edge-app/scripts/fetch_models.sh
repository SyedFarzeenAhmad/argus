#!/usr/bin/env bash
# Downloads the MVP's three prototype models and exports them to ONNX for the edge app.
#
#   pothole.onnx      tahaUgan/pothole-yolo11n (HF)       YOLO11n  0 Pothole, 1 Sewage-Manhole  CC-BY-4.0 weights
#   road_damage.onnx  dronefreak/rdd2022-yolov8n (HF)     YOLOv8n  RDD2022 cracks + pothole      AGPL-3.0
#   traffic.onnx      Ultralytics yolo11n.pt (COCO)       YOLO11n  80 COCO classes                AGPL-3.0
#
# All are trained with Ultralytics (AGPL-3.0), so they are PROTOTYPES only — see docs/03
# "Model licensing". CV-Perception replaces them file by file; the app only needs the names.
set -euo pipefail

here="$(cd "$(dirname "$0")/.." && pwd)"
out="$here/app/src/main/assets/models"
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT
mkdir -p "$out"

curl -fsSL -o "$work/pothole.pt"     https://huggingface.co/tahaUgan/pothole-yolo11n/resolve/main/pothole2v.pt
curl -fsSL -o "$work/road_damage.pt" https://huggingface.co/dronefreak/rdd2022-yolov8n/resolve/main/best.pt

uv venv -q --python 3.12 "$work/.venv"
uv pip install -q --python "$work/.venv/bin/python" ultralytics onnx onnxslim
(cd "$work" && .venv/bin/python -c "
from ultralytics import YOLO
for src in ['pothole.pt', 'road_damage.pt', 'yolo11n.pt']:   # yolo11n.pt is fetched by Ultralytics
    YOLO(src).export(format='onnx', imgsz=640, opset=17, simplify=True, dynamic=False)
")

cp "$work/pothole.onnx"     "$out/pothole.onnx"
cp "$work/road_damage.onnx" "$out/road_damage.onnx"
cp "$work/yolo11n.onnx"     "$out/traffic.onnx"
echo "wrote $out/{pothole,road_damage,traffic}.onnx"
