from __future__ import annotations

import numpy as np
import pandas as pd

from backend.app.config import LEAD_ORDER, TARGET_SAMPLES
from backend.app.biomarker import compute_avl_biomarker
from backend.app.ecg_io import coerce_to_twelve_leads, load_ecg_file, standardize_sampling


def test_coerce_derives_limb_leads_from_i_and_ii() -> None:
    lead_i = np.ones(100)
    lead_ii = np.ones(100) * 3
    array, present, missing, derived, caveats = coerce_to_twelve_leads(np.vstack([lead_i, lead_ii]), ["I", "II"])

    assert array.shape == (12, 100)
    assert "III" in present
    assert "aVR" in present
    assert "aVL" in present
    assert "aVF" in present
    assert set(derived) == {"III", "aVR", "aVL", "aVF"}
    assert np.allclose(array[LEAD_ORDER.index("III")], 2)
    assert np.allclose(array[LEAD_ORDER.index("aVR")], -2)
    assert np.allclose(array[LEAD_ORDER.index("aVL")], -0.5)
    assert np.allclose(array[LEAD_ORDER.index("aVF")], 2.5)
    assert "V1" in missing
    assert any("Derived limb leads" in caveat for caveat in caveats)


def test_standardize_sampling_resamples_and_pads() -> None:
    raw = np.ones((12, 1000))
    standardized, sample_rate, caveats = standardize_sampling(raw, 250)

    assert standardized.shape == (12, TARGET_SAMPLES)
    assert sample_rate == 500
    assert any("Resampled" in caveat for caveat in caveats)
    assert any("Padded" in caveat for caveat in caveats)


def test_load_csv_detects_named_leads_and_missing_horizontal_caveat(tmp_path) -> None:
    path = tmp_path / "two_lead.csv"
    df = pd.DataFrame(
        {
            "time": np.arange(500) / 500,
            "I": np.sin(np.linspace(0, 10, 500)),
            "II": np.cos(np.linspace(0, 10, 500)),
        }
    )
    df.to_csv(path, index=False)

    ecg = load_ecg_file(path, "example", 500)

    assert ecg.array.shape == (12, TARGET_SAMPLES)
    assert ecg.sampling_rate_hz == 500
    assert "aVL" in ecg.present_leads
    assert "V6" in ecg.missing_leads
    assert any("Horizontal-plane morphology review is diminished" in caveat for caveat in ecg.caveats)


def test_compute_avl_biomarker_returns_features_for_clean_rhythm() -> None:
    samples = TARGET_SAMPLES
    x = np.arange(samples)
    ecg = np.zeros((12, samples), dtype=float)
    for peak in range(500, 4500, 500):
        pulse = np.exp(-0.5 * ((x - peak) / 12) ** 2)
        ecg[LEAD_ORDER.index("aVL")] += pulse
        ecg[LEAD_ORDER.index("I")] += pulse * 0.8
        ecg[LEAD_ORDER.index("aVF")] += pulse * 0.4

    result = compute_avl_biomarker(
        study_id="synthetic",
        ecg=ecg,
        present_leads=LEAD_ORDER,
        derived_leads=[],
    )

    assert result.biomarker_available is True
    assert result.aVL_rs_diff is not None
    assert result.aVL_rs_diff_2 is not None
    assert result.r_peaks_used >= 3


def test_compute_avl_biomarker_reports_missing_avl() -> None:
    result = compute_avl_biomarker(
        study_id="missing",
        ecg=np.zeros((12, TARGET_SAMPLES), dtype=float),
        present_leads=["I", "II"],
        derived_leads=[],
    )

    assert result.biomarker_available is False
    assert result.aVL_rs_diff is None
    assert any("aVL biomarker unavailable" in caveat for caveat in result.caveats)
