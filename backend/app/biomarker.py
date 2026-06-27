from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
from scipy.signal import find_peaks
from scipy.stats import mode

from .config import LEAD_ORDER, TARGET_SAMPLING_RATE


AVL_INDEX = LEAD_ORDER.index("aVL")
LEAD_I_INDEX = LEAD_ORDER.index("I")
AVF_INDEX = LEAD_ORDER.index("aVF")
QRS_WINDOW_SAMPLES = 40
MEDIAN_BEAT_SAMPLES = 500


@dataclass
class BiomarkerResult:
    study_id: str
    aVL_rs_diff: float | None
    aVL_rs_diff_2: float | None
    qrs_axis_estimate: float | None
    r_peaks_used: int
    biomarker_available: bool
    caveats: list[str]
    debug: dict | None = None

    def to_dict(self) -> dict:
        return {
            "studyId": self.study_id,
            "aVL_rs_diff": self.aVL_rs_diff,
            "aVL_rs_diff_2": self.aVL_rs_diff_2,
            "qrs_axis_estimate": self.qrs_axis_estimate,
            "r_peaks_used": self.r_peaks_used,
            "biomarker_available": self.biomarker_available,
            "caveats": self.caveats,
            "debug": self.debug,
        }


def compute_avl_biomarker(
    study_id: str,
    ecg: np.ndarray,
    present_leads: list[str],
    derived_leads: list[str],
) -> BiomarkerResult:
    caveats = [
        "aVL biomarker values are research features from the ECG-SCD paper workflow, not clinical recommendations."
    ]
    present = set(present_leads)
    derived = set(derived_leads)

    if "aVL" not in present:
        return BiomarkerResult(
            study_id=study_id,
            aVL_rs_diff=None,
            aVL_rs_diff_2=None,
            qrs_axis_estimate=None,
            r_peaks_used=0,
            biomarker_available=False,
            caveats=[*caveats, "aVL biomarker unavailable because lead aVL is missing and could not be derived."],
        )

    if "aVL" in derived:
        caveats.append("aVL was reconstructed from leads I and II; treat the biomarker as lower fidelity than native aVL.")

    processed = zero_mode(ecg.astype(float, copy=True))
    axis = compute_qrs_axis(processed, present)
    rpeaks = detect_rpeaks(processed[AVL_INDEX])
    if len(rpeaks) == 0:
        return BiomarkerResult(
            study_id=study_id,
            aVL_rs_diff=None,
            aVL_rs_diff_2=None,
            qrs_axis_estimate=axis,
            r_peaks_used=0,
            biomarker_available=False,
            caveats=[*caveats, "aVL biomarker unavailable because no aVL R-peaks were detected."],
        )

    median_beat = median_beat_from_rpeaks(processed, rpeaks)
    if median_beat is None:
        return BiomarkerResult(
            study_id=study_id,
            aVL_rs_diff=None,
            aVL_rs_diff_2=None,
            qrs_axis_estimate=axis,
            r_peaks_used=int(len(rpeaks)),
            biomarker_available=False,
            caveats=[*caveats, "aVL biomarker unavailable because too few complete beats were available."],
        )

    rs_diff, rs_diff_2, debug = compute_v15style_rs_pair(median_beat[AVL_INDEX])
    rs_diff = finite_or_none(rs_diff)
    rs_diff_2 = finite_or_none(rs_diff_2)
    axis = finite_or_none(axis)
    available = rs_diff is not None and rs_diff_2 is not None
    if not available:
        caveats.append("aVL biomarker unavailable because Q/R/S morphology could not be parameterized.")

    return BiomarkerResult(
        study_id=study_id,
        aVL_rs_diff=rs_diff,
        aVL_rs_diff_2=rs_diff_2,
        qrs_axis_estimate=axis,
        r_peaks_used=int(len(rpeaks)),
        biomarker_available=available,
        caveats=caveats,
        debug=debug,
    )


def zero_mode(ecg: np.ndarray) -> np.ndarray:
    centered = ecg.copy()
    for channel in range(centered.shape[0]):
        signal = centered[channel]
        try:
            baseline = float(mode(signal, axis=None, keepdims=False).mode)
        except Exception:
            baseline = float(np.median(signal))
        centered[channel] = signal - baseline
    return centered


def detect_rpeaks(signal: np.ndarray) -> np.ndarray:
    try:
        from biosppy.signals import ecg as bioecg

        rpeaks, = bioecg.hamilton_segmenter(signal, sampling_rate=TARGET_SAMPLING_RATE)
        return np.asarray(rpeaks, dtype=int)
    except Exception:
        distance = int(0.3 * TARGET_SAMPLING_RATE)
        prominence = max(float(np.std(signal) * 0.5), 1e-6)
        peaks, _ = find_peaks(signal, distance=distance, prominence=prominence)
        return peaks.astype(int)


def median_beat_from_rpeaks(ecg: np.ndarray, rpeaks: np.ndarray) -> np.ndarray | None:
    half_window = MEDIAN_BEAT_SAMPLES // 2
    beats = []
    for rpeak in rpeaks:
        start = max(0, int(rpeak) - half_window)
        end = min(ecg.shape[1], int(rpeak) + half_window)
        beat = ecg[:, start:end]
        if beat.shape[1] < MEDIAN_BEAT_SAMPLES:
            beat = np.pad(beat, ((0, 0), (0, MEDIAN_BEAT_SAMPLES - beat.shape[1])), "constant", constant_values=0)
        beats.append(beat)

    beats = beats[1:-1]
    if not beats:
        return None
    return np.median(np.asarray(beats), axis=0)


def compute_v15style_rs_pair(avl: np.ndarray) -> tuple[float | None, float | None, dict | None]:
    eps = 1e-12
    avl_norm = (avl - np.min(avl)) / ((np.max(avl) - np.min(avl)) + eps)
    rpeaks = detect_rpeaks(avl_norm)
    if len(rpeaks) == 0:
        return None, None, {
            "methodVersion": "ecg-scd-avl-rs-v1",
            "detectionStatus": "no_median_beat_r_peak",
            "sampleRateHz": TARGET_SAMPLING_RATE,
            "normalizedAvl": rounded_list(avl_norm),
        }

    first_diff = np.diff(avl_norm)
    second_diff = np.diff(first_diff)
    feature_rows = []

    for rpeak_value in rpeaks:
        rpeak = int(rpeak_value)
        q_window = avl_norm[max(0, rpeak - QRS_WINDOW_SAMPLES):rpeak]
        q_diff = np.diff(q_window)
        q_changes = np.where(np.diff(np.sign(q_diff)))[0]
        qpeak = rpeak - len(q_window) + q_changes[-1] if len(q_changes) > 0 else rpeak - 1

        s_window = avl_norm[rpeak:min(len(avl_norm), rpeak + QRS_WINDOW_SAMPLES + 1)]
        s_diff = np.diff(s_window)
        s_changes = np.where(np.diff(np.sign(s_diff)))[0]
        speak = rpeak + s_changes[0] + 1 if len(s_changes) > 0 else rpeak + 1

        start = max(0, min(rpeak, len(first_diff) - 1))
        end = min(max(int(speak), start + 1), len(first_diff))
        first_segment = np.abs(first_diff[start:end])
        second_segment = np.abs(second_diff[start:end])
        if end <= start or len(first_segment) == 0 or len(second_segment) == 0:
            continue
        feature_rows.append(
            {
                "qPeakIndex": int(qpeak),
                "rPeakIndex": int(rpeak),
                "sEndIndex": int(speak),
                "intervalStartIndex": int(start),
                "intervalEndIndex": int(end),
                "firstDiffValue": float(np.mean(first_segment)),
                "secondDiffValue": float(np.mean(second_segment)),
                "cumulativeFirstDiff": cumulative_mean_trace(first_segment),
                "cumulativeSecondDiff": cumulative_mean_trace(second_segment),
            }
        )

    if not feature_rows:
        return None, None, {
            "methodVersion": "ecg-scd-avl-rs-v1",
            "detectionStatus": "no_valid_r_to_s_interval",
            "sampleRateHz": TARGET_SAMPLING_RATE,
            "medianBeatSamples": int(len(avl_norm)),
            "medianBeatDurationMs": round(float(len(avl_norm) / TARGET_SAMPLING_RATE * 1000), 3),
            "normalizedAvl": rounded_list(avl_norm),
            "medianBeatRPeaks": [int(peak) for peak in rpeaks],
        }

    selected = feature_rows[0]
    debug = {
        "methodVersion": "ecg-scd-avl-rs-v1",
        "detectionStatus": "ok",
        "sampleRateHz": TARGET_SAMPLING_RATE,
        "medianBeatSamples": int(len(avl_norm)),
        "medianBeatDurationMs": round(float(len(avl_norm) / TARGET_SAMPLING_RATE * 1000), 3),
        "medianBeatRPeaks": [int(peak) for peak in rpeaks],
        "normalizedAvl": rounded_list(avl_norm),
        "qPeakIndex": selected["qPeakIndex"],
        "rPeakIndex": selected["rPeakIndex"],
        "sEndIndex": selected["sEndIndex"],
        "intervalStartIndex": selected["intervalStartIndex"],
        "intervalEndIndex": selected["intervalEndIndex"],
        "intervalDurationMs": round(
            float((selected["intervalEndIndex"] - selected["intervalStartIndex"]) / TARGET_SAMPLING_RATE * 1000),
            3,
        ),
        "cumulativeFirstDiff": selected["cumulativeFirstDiff"],
        "cumulativeSecondDiff": selected["cumulativeSecondDiff"],
        "allDetectedIntervals": [
            {
                key: value
                for key, value in row.items()
                if key
                in {
                    "qPeakIndex",
                    "rPeakIndex",
                    "sEndIndex",
                    "intervalStartIndex",
                    "intervalEndIndex",
                    "firstDiffValue",
                    "secondDiffValue",
                }
            }
            for row in feature_rows
        ],
    }
    return float(selected["firstDiffValue"]), float(selected["secondDiffValue"]), debug


def compute_qrs_axis(ecg: np.ndarray, present_leads: set[str]) -> float | None:
    if "I" not in present_leads or "aVF" not in present_leads:
        return None

    rpeaks = detect_rpeaks(ecg[LEAD_I_INDEX])
    if len(rpeaks) == 0:
        return None

    mask = np.zeros(ecg.shape[1])
    for peak in rpeaks:
        start = max(0, int(peak) - QRS_WINDOW_SAMPLES)
        end = min(ecg.shape[1], int(peak) + QRS_WINDOW_SAMPLES)
        mask[start:end] = 1

    i_sum = float(np.sum(ecg[LEAD_I_INDEX] * mask))
    avf_sum = float(np.sum(ecg[AVF_INDEX] * mask))
    if math.isclose(i_sum, 0.0) and math.isclose(avf_sum, 0.0):
        return None
    return float(np.degrees(np.arctan2(avf_sum, i_sum)))


def finite_or_none(value: float | None) -> float | None:
    if value is None:
        return None
    if not np.isfinite(value):
        return None
    return float(value)


def rounded_list(values: np.ndarray, digits: int = 6) -> list[float]:
    return np.round(values.astype(float), digits).tolist()


def cumulative_mean_trace(values: np.ndarray, digits: int = 6) -> list[float]:
    if len(values) == 0:
        return []
    trace = np.cumsum(values.astype(float)) / len(values)
    return rounded_list(trace, digits)
