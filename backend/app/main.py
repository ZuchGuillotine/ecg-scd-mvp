from __future__ import annotations

import json
import re
import uuid
from datetime import UTC, datetime
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .biomarker import compute_avl_biomarker
from .config import FRONTEND_DIR, LEAD_ORDER, STORAGE_DIR, TARGET_SAMPLING_RATE
from .ecg_io import load_ecg_file, preview_payload, write_metadata
from .inference import load_model_registry, run_prediction, save_record_array
from .schemas import JobStatus, JobSummary, ModelInfo


app = FastAPI(
    title="ECG-SCD Explorer MVP",
    description="Exploratory non-clinical wrapper around the ECG-SCD research pipeline.",
    version="0.1.0",
)


@app.on_event("startup")
def startup() -> None:
    STORAGE_DIR.mkdir(parents=True, exist_ok=True)
    (STORAGE_DIR / "jobs").mkdir(parents=True, exist_ok=True)


@app.get("/api/health")
def health() -> dict:
    return {"ok": True}


@app.get("/api/models", response_model=list[ModelInfo])
def models() -> list[ModelInfo]:
    registry = load_model_registry()
    return [config.to_info() for config in registry.values()]


@app.get("/api/jobs", response_model=list[JobSummary])
def list_jobs() -> list[JobSummary]:
    jobs_dir = STORAGE_DIR / "jobs"
    if not jobs_dir.exists():
        return []

    summaries = []
    for job_dir in sorted(jobs_dir.iterdir(), key=lambda path: path.stat().st_mtime, reverse=True):
        if not job_dir.is_dir() or not re.fullmatch(r"[a-f0-9]{12}", job_dir.name):
            continue
        records_path = job_dir / "records.json"
        if not records_path.exists():
            continue
        summaries.append(read_job_summary(job_dir))
    return summaries


@app.post("/api/jobs", response_model=JobStatus)
async def create_job(
    files: list[UploadFile] = File(...),
    sampling_rate_hz: float = Form(TARGET_SAMPLING_RATE),
    lead_order: str | None = Form(None),
) -> JobStatus:
    if not files:
        raise HTTPException(status_code=400, detail="Upload at least one ECG file.")

    job_id = uuid.uuid4().hex[:12]
    job_dir = STORAGE_DIR / "jobs" / job_id
    upload_dir = job_dir / "uploads"
    upload_dir.mkdir(parents=True, exist_ok=True)

    lead_hint = parse_lead_order(lead_order)
    records = []
    for index, upload in enumerate(files, start=1):
        safe_name = safe_filename(upload.filename or f"ecg_{index}.npy")
        source_path = upload_dir / safe_name
        source_path.write_bytes(await upload.read())

        study_id = Path(safe_name).stem or f"ecg_{index}"
        try:
            normalized = load_ecg_file(source_path, study_id, sampling_rate_hz, lead_hint)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=f"{safe_name}: {exc}") from exc

        save_record_array(job_dir, normalized.study_id, normalized.array)
        records.append(normalized.to_record())

    now = timestamp_now()
    write_json(
        job_dir / "metadata.json",
        {
            "job_id": job_id,
            "created_at": now,
            "updated_at": now,
            "source_filenames": [record.filename for record in records],
        },
    )
    write_metadata(job_dir / "records.json", records)
    write_json(job_dir / "biomarkers.json", {"biomarkers": [], "biomarker_caveats": []})
    write_json(job_dir / "predictions.json", {"predictions": [], "prediction_caveats": []})
    return read_job(job_id)


@app.get("/api/jobs/{job_id}", response_model=JobStatus)
def read_job(job_id: str) -> JobStatus:
    job_dir = get_job_dir(job_id)
    records_path = job_dir / "records.json"
    if not records_path.exists():
        raise HTTPException(status_code=404, detail="Job not found.")
    metadata = read_job_metadata(job_dir)
    biomarker_payload = read_json(job_dir / "biomarkers.json", {"biomarkers": [], "biomarker_caveats": []})
    prediction_payload = read_json(job_dir / "predictions.json", {"predictions": [], "prediction_caveats": []})
    return JobStatus(
        job_id=job_id,
        created_at=metadata.get("created_at"),
        updated_at=metadata.get("updated_at"),
        records=json.loads(records_path.read_text(encoding="utf-8")),
        biomarkers=biomarker_payload.get("biomarkers", []),
        biomarker_caveats=biomarker_payload.get("biomarker_caveats", []),
        predictions=prediction_payload.get("predictions", []),
        prediction_caveats=prediction_payload.get("prediction_caveats", []),
    )


@app.post("/api/jobs/{job_id}/predict", response_model=JobStatus)
def predict_job(job_id: str, model_id: str = Form(...), allow_missing_leads: bool = Form(False)) -> JobStatus:
    job_dir = get_job_dir(job_id)
    current = read_job(job_id)
    registry = load_model_registry()
    model_config = registry.get(model_id)
    if model_config is None:
        raise HTTPException(status_code=404, detail=f"Unknown model_id: {model_id}")

    record_payloads = [record.model_dump() for record in current.records]
    predictions, caveats = run_prediction(job_dir, record_payloads, model_config, allow_missing_leads)
    write_json(job_dir / "predictions.json", {"predictions": predictions, "prediction_caveats": caveats})
    touch_job_metadata(job_dir)
    return read_job(job_id)


@app.post("/api/jobs/{job_id}/biomarkers", response_model=JobStatus)
def compute_biomarkers(job_id: str) -> JobStatus:
    import numpy as np

    job_dir = get_job_dir(job_id)
    current = read_job(job_id)
    biomarkers = []
    caveats = [
        "Computed aVL RS-slope features following the public ECG-SCD preprocessing script's biomarker parameterization."
    ]

    for record in current.records:
        ecg_path = job_dir / "ecgs" / f"{safe_filename(record.study_id)}.npy"
        if not ecg_path.exists():
            biomarkers.append(
                {
                    "studyId": record.study_id,
                    "aVL_rs_diff": None,
                    "aVL_rs_diff_2": None,
                    "qrs_axis_estimate": None,
                    "r_peaks_used": 0,
                    "biomarker_available": False,
                    "caveats": ["Normalized ECG array was not found for this record."],
                }
            )
            continue

        result = compute_avl_biomarker(
            study_id=record.study_id,
            ecg=np.load(ecg_path),
            present_leads=record.present_leads,
            derived_leads=record.derived_leads,
        )
        biomarkers.append(result.to_dict())

    if any(not row["biomarker_available"] for row in biomarkers):
        caveats.append("One or more records could not be parameterized; inspect per-record caveats.")
    if any("aVL" in record.derived_leads for record in current.records):
        caveats.append("One or more aVL traces were reconstructed from I and II, so native-aVL validation is preferable.")

    write_json(job_dir / "biomarkers.json", {"biomarkers": biomarkers, "biomarker_caveats": caveats})
    touch_job_metadata(job_dir)
    return read_job(job_id)


@app.get("/api/jobs/{job_id}/records/{study_id}/preview")
def record_preview(job_id: str, study_id: str) -> dict:
    import numpy as np

    job_dir = get_job_dir(job_id)
    ecg_path = job_dir / "ecgs" / f"{safe_filename(study_id)}.npy"
    if not ecg_path.exists():
        raise HTTPException(status_code=404, detail="Record not found.")
    return preview_payload(np.load(ecg_path))


@app.get("/")
def index() -> FileResponse:
    return FileResponse(FRONTEND_DIR / "index.html")


app.mount("/assets", StaticFiles(directory=FRONTEND_DIR), name="assets")


def parse_lead_order(raw: str | None) -> list[str] | None:
    if not raw:
        return None
    leads = [part.strip() for part in raw.split(",") if part.strip()]
    unknown = [lead for lead in leads if lead not in LEAD_ORDER]
    if unknown:
        raise HTTPException(status_code=400, detail=f"Unknown lead names: {', '.join(unknown)}")
    return leads


def get_job_dir(job_id: str) -> Path:
    if not re.fullmatch(r"[a-f0-9]{12}", job_id):
        raise HTTPException(status_code=404, detail="Job not found.")
    return STORAGE_DIR / "jobs" / job_id


def safe_filename(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "_", value).strip("._")
    return cleaned or "ecg"


def write_json(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def read_json(path: Path, default: dict) -> dict:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def timestamp_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def timestamp_from_mtime(path: Path) -> str:
    return datetime.fromtimestamp(path.stat().st_mtime, UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def read_job_metadata(job_dir: Path) -> dict:
    metadata_path = job_dir / "metadata.json"
    if metadata_path.exists():
        metadata = read_json(metadata_path, {})
    else:
        metadata = {}
    fallback = timestamp_from_mtime(job_dir)
    metadata.setdefault("job_id", job_dir.name)
    metadata.setdefault("created_at", fallback)
    metadata.setdefault("updated_at", fallback)
    return metadata


def touch_job_metadata(job_dir: Path) -> None:
    metadata = read_job_metadata(job_dir)
    metadata["updated_at"] = timestamp_now()
    write_json(job_dir / "metadata.json", metadata)


def read_job_summary(job_dir: Path) -> JobSummary:
    metadata = read_job_metadata(job_dir)
    records = json.loads((job_dir / "records.json").read_text(encoding="utf-8"))
    biomarker_payload = read_json(job_dir / "biomarkers.json", {"biomarkers": []})
    prediction_payload = read_json(job_dir / "predictions.json", {"predictions": []})
    return JobSummary(
        job_id=job_dir.name,
        created_at=metadata["created_at"],
        updated_at=metadata["updated_at"],
        record_count=len(records),
        filenames=[record.get("filename", record.get("study_id", "ECG")) for record in records[:4]],
        has_biomarkers=bool(biomarker_payload.get("biomarkers")),
        has_predictions=bool(prediction_payload.get("predictions")),
    )
