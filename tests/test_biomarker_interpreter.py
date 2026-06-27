from __future__ import annotations

import numpy as np
import pytest

from backend.app import biomarker
from backend.app.config import LEAD_ORDER, TARGET_SAMPLES


def test_rs_pair_uses_paper_r_to_qrs_end_interval(monkeypatch) -> None:
    monkeypatch.setattr(biomarker, "detect_rpeaks", lambda signal: np.asarray([4]))
    avl = np.asarray([0.0, 0.1, 0.2, 0.4, 1.0, 0.7, 0.5, 0.4, 0.45, 0.5])

    rs_diff, rs_diff_2, debug = biomarker.compute_v15style_rs_pair(avl)

    first_diff = np.diff(avl)
    second_diff = np.diff(first_diff)
    assert debug is not None
    assert debug["qPeakIndex"] == 3
    assert debug["intervalStartIndex"] == 4
    assert debug["intervalEndIndex"] == 7
    assert rs_diff == pytest.approx(np.mean(np.abs(first_diff[4:7])))
    assert rs_diff_2 == pytest.approx(np.mean(np.abs(second_diff[4:7])))


def test_slurred_downstroke_has_lower_derivative_features(monkeypatch) -> None:
    monkeypatch.setattr(biomarker, "detect_rpeaks", lambda signal: np.asarray([4]))
    sharp = np.asarray([0.0, 0.1, 0.2, 0.4, 1.0, 0.2, 0.1, 0.0, 0.1, 0.2])
    slurred = np.asarray([0.0, 0.1, 0.2, 0.4, 1.0, 0.8, 0.6, 0.4, 0.5, 0.6])

    sharp_first, sharp_second, _ = biomarker.compute_v15style_rs_pair(sharp)
    slurred_first, slurred_second, _ = biomarker.compute_v15style_rs_pair(slurred)

    assert slurred_first < sharp_first
    assert slurred_second < sharp_second


def test_clean_rhythm_returns_debug_traces() -> None:
    samples = TARGET_SAMPLES
    x = np.arange(samples)
    ecg = np.zeros((12, samples), dtype=float)
    for peak in range(500, 4500, 500):
        pulse = np.exp(-0.5 * ((x - peak) / 12) ** 2)
        ecg[LEAD_ORDER.index("aVL")] += pulse
        ecg[LEAD_ORDER.index("I")] += pulse * 0.8
        ecg[LEAD_ORDER.index("aVF")] += pulse * 0.4

    result = biomarker.compute_avl_biomarker(
        study_id="synthetic",
        ecg=ecg,
        present_leads=LEAD_ORDER,
        derived_leads=[],
    )

    assert result.biomarker_available is True
    assert result.debug is not None
    assert result.debug["detectionStatus"] == "ok"
    assert len(result.debug["normalizedAvl"]) == 500
    assert result.debug["cumulativeFirstDiff"][-1] == pytest.approx(result.aVL_rs_diff, abs=1e-6)
    assert result.debug["cumulativeSecondDiff"][-1] == pytest.approx(result.aVL_rs_diff_2, abs=1e-6)
