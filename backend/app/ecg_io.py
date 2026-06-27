from __future__ import annotations

import json
import zipfile
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory

import numpy as np
import pandas as pd
from scipy.signal import resample

from .config import HORIZONTAL_LEADS, LEAD_ORDER, TARGET_SAMPLING_RATE, TARGET_SAMPLES
from .schemas import ECGRecord, LeadQuality


LEAD_ALIASES = {
    "1": "I",
    "lead1": "I",
    "leadi": "I",
    "i": "I",
    "2": "II",
    "lead2": "II",
    "leadii": "II",
    "ii": "II",
    "3": "III",
    "lead3": "III",
    "leadiii": "III",
    "iii": "III",
    "avr": "aVR",
    "avl": "aVL",
    "avf": "aVF",
    "v1": "V1",
    "v2": "V2",
    "v3": "V3",
    "v4": "V4",
    "v5": "V5",
    "v6": "V6",
}


@dataclass
class NormalizedECG:
    study_id: str
    filename: str
    array: np.ndarray
    sampling_rate_hz: float
    present_leads: list[str]
    missing_leads: list[str]
    derived_leads: list[str]
    caveats: list[str]
    lead_quality: list[LeadQuality]

    def to_record(self) -> ECGRecord:
        return ECGRecord(
            study_id=self.study_id,
            filename=self.filename,
            sampling_rate_hz=self.sampling_rate_hz,
            samples=int(self.array.shape[1]),
            seconds=round(float(self.array.shape[1] / TARGET_SAMPLING_RATE), 3),
            present_leads=self.present_leads,
            missing_leads=self.missing_leads,
            derived_leads=self.derived_leads,
            caveats=self.caveats,
            lead_quality=self.lead_quality,
        )


def normalize_lead_name(value: str) -> str | None:
    key = "".join(ch for ch in value.strip().lower() if ch.isalnum())
    return LEAD_ALIASES.get(key)


def load_ecg_file(
    source_path: Path,
    study_id: str,
    sampling_rate_hint: float = TARGET_SAMPLING_RATE,
    lead_order_hint: list[str] | None = None,
) -> NormalizedECG:
    suffix = source_path.suffix.lower()
    if suffix == ".npy":
        raw, leads, sample_rate = _load_npy(source_path, lead_order_hint, sampling_rate_hint)
    elif suffix == ".npz":
        raw, leads, sample_rate = _load_npz(source_path, lead_order_hint, sampling_rate_hint)
    elif suffix == ".csv":
        raw, leads, sample_rate = _load_csv(source_path, lead_order_hint, sampling_rate_hint)
    elif suffix == ".zip":
        raw, leads, sample_rate = _load_zip(source_path, lead_order_hint, sampling_rate_hint)
    else:
        raise ValueError(f"Unsupported ECG file type: {suffix}. Use .npy, .npz, .csv, or a zipped WFDB record.")

    array_12, present, missing, derived, caveats = coerce_to_twelve_leads(raw, leads)
    array_12, sample_rate, resample_caveats = standardize_sampling(array_12, sample_rate)
    caveats.extend(resample_caveats)
    caveats.extend(build_interpretation_caveats(missing, derived))

    return NormalizedECG(
        study_id=study_id,
        filename=source_path.name,
        array=array_12.astype(np.float32),
        sampling_rate_hz=sample_rate,
        present_leads=present,
        missing_leads=missing,
        derived_leads=derived,
        caveats=caveats,
        lead_quality=lead_quality(array_12, present, derived),
    )


def coerce_to_twelve_leads(raw: np.ndarray, leads: list[str]) -> tuple[np.ndarray, list[str], list[str], list[str], list[str]]:
    if raw.ndim != 2:
        raise ValueError(f"ECG array must be 2D; received shape {raw.shape}.")

    raw = orient_leads_first(raw, len(leads))
    if len(leads) != raw.shape[0]:
        raise ValueError(f"Lead count {len(leads)} does not match array shape {raw.shape}.")

    caveats: list[str] = []
    derived: list[str] = []
    lead_map = {lead: raw[idx].astype(float) for idx, lead in enumerate(leads) if lead in LEAD_ORDER}

    if "I" in lead_map and "II" in lead_map:
        if "III" not in lead_map:
            lead_map["III"] = lead_map["II"] - lead_map["I"]
            derived.append("III")
        if "aVR" not in lead_map:
            lead_map["aVR"] = -(lead_map["I"] + lead_map["II"]) / 2.0
            derived.append("aVR")
        if "aVL" not in lead_map:
            lead_map["aVL"] = lead_map["I"] - (lead_map["II"] / 2.0)
            derived.append("aVL")
        if "aVF" not in lead_map:
            lead_map["aVF"] = lead_map["II"] - (lead_map["I"] / 2.0)
            derived.append("aVF")

    samples = raw.shape[1]
    out = np.zeros((len(LEAD_ORDER), samples), dtype=float)
    present = []
    missing = []
    for idx, lead in enumerate(LEAD_ORDER):
        if lead in lead_map:
            out[idx] = lead_map[lead]
            present.append(lead)
        else:
            missing.append(lead)

    if missing:
        caveats.append(f"Missing leads were zero-filled for storage only: {', '.join(missing)}.")
    if derived:
        caveats.append(f"Derived limb leads from leads I and II: {', '.join(derived)}.")

    return out, present, missing, derived, caveats


def standardize_sampling(array: np.ndarray, sampling_rate_hz: float) -> tuple[np.ndarray, float, list[str]]:
    caveats: list[str] = []
    if sampling_rate_hz <= 0:
        sampling_rate_hz = TARGET_SAMPLING_RATE
        caveats.append("Invalid sampling rate supplied; assumed 500 Hz.")

    if abs(sampling_rate_hz - TARGET_SAMPLING_RATE) > 0.01:
        target_len = max(1, int(round(array.shape[1] * TARGET_SAMPLING_RATE / sampling_rate_hz)))
        array = resample(array, target_len, axis=1)
        caveats.append(f"Resampled from {sampling_rate_hz:g} Hz to 500 Hz.")
        sampling_rate_hz = TARGET_SAMPLING_RATE
    else:
        sampling_rate_hz = TARGET_SAMPLING_RATE

    if array.shape[1] > TARGET_SAMPLES:
        array = array[:, :TARGET_SAMPLES]
        caveats.append("Trimmed signal to the first 10 seconds expected by the upstream model.")
    elif array.shape[1] < TARGET_SAMPLES:
        pad = np.zeros((array.shape[0], TARGET_SAMPLES - array.shape[1]), dtype=array.dtype)
        array = np.concatenate([array, pad], axis=1)
        caveats.append("Padded signal with zeros to reach the 10-second model window.")

    return array, sampling_rate_hz, caveats


def build_interpretation_caveats(missing: list[str], derived: list[str]) -> list[str]:
    caveats = [
        "This exploratory tool is not cleared or validated for clinical decision-making."
    ]
    if missing:
        caveats.append(
            "A 12-lead model prediction with missing leads is out-of-distribution unless a compatible limited-lead model artifact is selected."
        )
    missing_horizontal = sorted(set(missing).intersection(HORIZONTAL_LEADS))
    if missing_horizontal:
        caveats.append(
            f"Horizontal-plane morphology review is diminished because {', '.join(missing_horizontal)} are unavailable."
        )
    if "aVL" in missing:
        caveats.append("The aVL RS-slope biomarker display is unavailable without lead aVL or derivable limb leads.")
    elif "aVL" in derived:
        caveats.append("aVL-derived displays should be treated as lower fidelity because aVL was reconstructed from I and II.")
    return caveats


def lead_quality(array: np.ndarray, present: list[str], derived: list[str]) -> list[LeadQuality]:
    present_set = set(present)
    derived_set = set(derived)
    quality = []
    for idx, lead in enumerate(LEAD_ORDER):
        values = array[idx]
        is_present = lead in present_set
        quality.append(
            LeadQuality(
                lead=lead,
                present=is_present,
                derived=lead in derived_set,
                min_mv=float(np.min(values)) if is_present else None,
                max_mv=float(np.max(values)) if is_present else None,
                peak_to_peak_mv=float(np.max(values) - np.min(values)) if is_present else None,
            )
        )
    return quality


def preview_payload(array: np.ndarray, max_points: int = 700) -> dict:
    stride = max(1, int(np.ceil(array.shape[1] / max_points)))
    x = (np.arange(0, array.shape[1], stride) / TARGET_SAMPLING_RATE).round(4).tolist()
    leads = {
        lead: array[idx, ::stride].round(4).tolist()
        for idx, lead in enumerate(LEAD_ORDER)
    }
    return {"seconds": x, "leads": leads}


def orient_leads_first(raw: np.ndarray, lead_count: int) -> np.ndarray:
    if raw.shape[0] == lead_count:
        return raw
    if raw.shape[1] == lead_count:
        return raw.T
    if raw.shape[0] <= 12 and raw.shape[1] > raw.shape[0]:
        return raw
    if raw.shape[1] <= 12 and raw.shape[0] > raw.shape[1]:
        return raw.T
    raise ValueError(f"Could not infer ECG orientation from shape {raw.shape}.")


def _load_npy(path: Path, lead_order_hint: list[str] | None, sampling_rate_hint: float) -> tuple[np.ndarray, list[str], float]:
    arr = np.load(path, allow_pickle=False)
    leads = lead_order_hint or LEAD_ORDER[: min(arr.shape)]
    return np.asarray(arr), leads, sampling_rate_hint


def _load_npz(path: Path, lead_order_hint: list[str] | None, sampling_rate_hint: float) -> tuple[np.ndarray, list[str], float]:
    archive = np.load(path, allow_pickle=False)
    keys = list(archive.keys())
    data_key = next((key for key in ["ecg", "signal", "signals", "waveform", "arr_0"] if key in archive), keys[0])
    arr = np.asarray(archive[data_key])
    if "lead_names" in archive:
        leads = [str(x) for x in archive["lead_names"].tolist()]
    else:
        leads = lead_order_hint or LEAD_ORDER[: min(arr.shape)]
    sample_rate = float(archive["sampling_rate"].item()) if "sampling_rate" in archive else sampling_rate_hint
    return arr, leads, sample_rate


def _load_csv(path: Path, lead_order_hint: list[str] | None, sampling_rate_hint: float) -> tuple[np.ndarray, list[str], float]:
    df = pd.read_csv(path)
    lower_map = {col: normalize_lead_name(col) for col in df.columns}
    lead_cols = [(lead, col) for col, lead in lower_map.items() if lead is not None]
    if not lead_cols:
        numeric = df.select_dtypes(include=["number"])
        time_cols = [col for col in numeric.columns if col.lower() in {"time", "seconds", "sec", "t"}]
        signal_cols = [col for col in numeric.columns if col not in time_cols]
        leads = lead_order_hint or LEAD_ORDER[: len(signal_cols)]
        arr = numeric[signal_cols].to_numpy(dtype=float).T
    else:
        lead_cols = sorted(lead_cols, key=lambda item: LEAD_ORDER.index(item[0]))
        leads = [lead for lead, _ in lead_cols]
        arr = df[[col for _, col in lead_cols]].to_numpy(dtype=float).T

    sample_rate = _sampling_rate_from_time_column(df) or sampling_rate_hint
    return arr, leads, sample_rate


def _load_zip(path: Path, lead_order_hint: list[str] | None, sampling_rate_hint: float) -> tuple[np.ndarray, list[str], float]:
    with TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        with zipfile.ZipFile(path) as archive:
            archive.extractall(tmp_path)
        npy_files = sorted(tmp_path.rglob("*.npy"))
        npz_files = sorted(tmp_path.rglob("*.npz"))
        csv_files = sorted(tmp_path.rglob("*.csv"))
        hea_files = sorted(tmp_path.rglob("*.hea"))
        if npy_files:
            return _load_npy(npy_files[0], lead_order_hint, sampling_rate_hint)
        if npz_files:
            return _load_npz(npz_files[0], lead_order_hint, sampling_rate_hint)
        if csv_files:
            return _load_csv(csv_files[0], lead_order_hint, sampling_rate_hint)
        if hea_files:
            return _load_wfdb_record(hea_files[0])
    raise ValueError("Zip archive did not contain a supported ECG file.")


def _load_wfdb_record(header_path: Path) -> tuple[np.ndarray, list[str], float]:
    import wfdb

    record_base = str(header_path.with_suffix(""))
    signals, fields = wfdb.rdsamp(record_base)
    leads = [normalize_lead_name(str(name)) or str(name) for name in fields.get("sig_name", [])]
    sample_rate = float(fields.get("fs", TARGET_SAMPLING_RATE))
    return signals.T, leads, sample_rate


def _sampling_rate_from_time_column(df: pd.DataFrame) -> float | None:
    for col in df.columns:
        if col.lower() in {"time", "seconds", "sec", "t"}:
            values = pd.to_numeric(df[col], errors="coerce").dropna().to_numpy()
            if len(values) > 2:
                diffs = np.diff(values)
                median = float(np.median(diffs[diffs > 0])) if np.any(diffs > 0) else 0
                if median > 0:
                    return 1.0 / median
    return None


def write_metadata(path: Path, records: list[ECGRecord]) -> None:
    payload = [record.model_dump() for record in records]
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
