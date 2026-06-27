from __future__ import annotations

import os
from pathlib import Path


APP_ROOT = Path(__file__).resolve().parents[2]
FRONTEND_DIR = APP_ROOT / "frontend"
STORAGE_DIR = Path(os.getenv("ECG_SCD_STORAGE", APP_ROOT / "storage")).resolve()
MODEL_REGISTRY_PATH = Path(
    os.getenv("ECG_SCD_MODEL_REGISTRY", APP_ROOT / "models" / "model_registry.json")
).resolve()

TARGET_SAMPLING_RATE = 500
TARGET_SECONDS = 10
TARGET_SAMPLES = TARGET_SAMPLING_RATE * TARGET_SECONDS

LEAD_ORDER = ["I", "II", "III", "aVR", "aVL", "aVF", "V1", "V2", "V3", "V4", "V5", "V6"]
HORIZONTAL_LEADS = {"V1", "V2", "V3", "V4", "V5", "V6"}
