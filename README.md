# ECG-SCD Explorer MVP

This is a small, isolated wrapper around the public `alexmschubert/ECG-SCD` research code for retrospective exploration. It is designed for physicians and collaborators who want to upload historical ECG files, inspect lead completeness, review whether the aVL biomarker workflow is behaving plausibly, and run supplied model artifacts without manually assembling a Python/torch environment.

It is not a clinical calculator and is not for patient care.

## What It Does

- Accepts `.npy`, `.npz`, `.csv`, or zipped WFDB-style ECG records.
- Normalizes ECG waveforms into the upstream convention: 12 leads, 500 Hz, 10 seconds, one `<studyId>.npy` per ECG.
- Derives standard limb leads from I and II when possible: III, aVR, aVL, aVF.
- Flags missing leads and explains interpretation limitations, especially horizontal-plane leads and aVL fidelity.
- Computes the paper workflow's lightweight aVL RS-slope biomarker features without requiring deep-model weights.
- Reviews the biomarker workflow with a two-feature reference map, marginal uploaded-cohort bands, method-readiness checks, and a waveform validation panel.
- Runs the upstream ECG-SCD 10-second prediction function when trained model artifacts are supplied.
- Serves a simple browser UI for upload, waveform preview, caveats, and prediction tables.
- Lists previous local runs with job IDs, timestamps, source filenames, and biomarker/prediction status.

## Important Constraint

The public ECG-SCD repository currently ships code and training procedures, but not pretrained model artifacts. To run prediction, put artifacts created by the upstream training scripts into this app's `models/` directory:

```text
models/
  model_registry.json
  modelfits_ecg/
    ntuh_scd_model_demo.json
    ntuh_scd_model_demo/
      model.best.pth.tar
      calibration_model.pkl        # optional
```

Use `models/model_registry.example.json` as the template.

The aVL biomarker path does not require those artifacts. After uploading ECGs, click **Compute aVL biomarker** to calculate:

- `aVL_rs_diff`
- `aVL_rs_diff_2`
- `qrs_axis_estimate`
- R-peaks used and per-record caveats

This mirrors the intent of upstream `00_Data_Preprocessing/x06_create_aVL_feature.py`: a lower-compute way to inspect the discovered biomarker in outside retrospective cohorts. The current implementation aligns the feature interval to the public script's R-peak-to-QRS-end calculation and emits a debug trace for each successful record:

- normalized median aVL beat
- detected R peak and QRS-end/S endpoint
- shaded extraction interval
- cumulative absolute first-difference trace
- cumulative absolute second-difference trace
- method version and detection status

The **Review workflow** button opens a biomarker workflow review. It is organized for reproduction-method QA rather than clinical interpretation:

1. **Workflow checks** summarize input normalization, native-vs-derived aVL, feature availability, and method-trace status.
2. **Two-feature reference map** plots `aVL_rs_diff` against `aVL_rs_diff_2` for the uploaded ECGs, with marginal uploaded-cohort bands for one-feature sanity checks.
3. **Waveform validation** shows the normalized median aVL beat and the cumulative derivative traces used to produce the two feature values.
4. **Paper reference baselines** show published cohort/model context and clearly mark that raw paper-cohort `aVL_rs_diff` distributions are not available in the publication.

The paper reference rows are approximate context only. The app does not draw paper cohort densities or percentile cutoffs without raw or summarized feature distributions.

Each upload creates a local job under `storage/jobs/<jobId>/`. The UI's **Previous Runs** list reads those folders so users can return to prior runs. Direct links also work:

```text
http://localhost:8080/?job=<jobId>
```

## Run Locally With Docker

```bash
git clone https://github.com/ZuchGuillotine/ecg-scd-mvp.git
cd ecg-scd-mvp
cp models/model_registry.example.json models/model_registry.json
docker compose up --build
```

Open [http://localhost:8080](http://localhost:8080).

Docker installs the unmodified upstream package from [alexmschubert/ECG-SCD](https://github.com/alexmschubert/ECG-SCD). The upstream code is licensed CC BY-NC-ND 4.0, so this MVP wraps and imports it rather than vendoring modified copies.

## Run Without Docker

```bash
git clone https://github.com/ZuchGuillotine/ecg-scd-mvp.git
cd ecg-scd-mvp
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements.inference.txt
uvicorn backend.app.main:app --host 0.0.0.0 --port 8080
```

## Test Locally

```bash
git clone https://github.com/ZuchGuillotine/ecg-scd-mvp.git
cd ecg-scd-mvp
uv venv .venv
uv pip install --python .venv/bin/python -e '.[dev]'
.venv/bin/python -m pytest tests/test_biomarker_interpreter.py -q
.venv/bin/python -m pytest -q
node --check frontend/app.js
```

The focused biomarker interpreter tests cover three cases:

- exact use of the public workflow's R-peak-to-QRS-end interval
- lower derivative features for a smoother/slurred synthetic downstroke than a sharp downstroke
- full clean-rhythm biomarker extraction with debug traces returned for the UI

## Input Formats

For `.npy` and `.npz`, the signal may be shaped as `(leads, samples)` or `(samples, leads)`. If lead names are not stored in the file, the app assumes the standard 12-lead order:

```text
I, II, III, aVR, aVL, aVF, V1, V2, V3, V4, V5, V6
```

For `.csv`, named columns like `I`, `II`, `aVL`, and `V1` are detected automatically. A `time`, `seconds`, `sec`, or `t` column is used to infer sampling rate when present.

For WFDB records, zip the full record files together, including the `.hea` file.

## Limited-Lead Behavior

The backend never treats incomplete ECGs as equivalent to complete 12-lead ECGs.

- If only I and II are present, standard derived limb leads are reconstructed.
- Missing precordial leads are zero-filled only so the normalized array can be stored in the 12-channel shape expected by the original code.
- Prediction is blocked by default when leads are missing.
- Users can explicitly allow missing-lead prediction, but the result is marked lower fidelity and out-of-distribution unless the selected artifact was trained for limited-lead input.
- aVL displays are unavailable without aVL or derivable limb leads.
- Horizontal-plane morphology review is diminished when V1-V6 are missing.

## MVP Gaps To Decide Next

- Whether to host this as a cloud app or distribute it as a Docker package.
- Which pretrained or locally trained model artifacts should be distributed, and under what license/data-use terms.
- Which historical ECG file formats are most common in the target review cohorts.
- Whether subgroup metadata should be uploaded as a separate CSV and joined by `studyId`.
- Whether public or user-created reference cohorts should be imported as versioned feature files for bivariate comparison.
- Whether to train explicit limited-lead models rather than permitting zero-filled missing leads through a 12-lead model.

## Sources

- Upstream code: [alexmschubert/ECG-SCD](https://github.com/alexmschubert/ECG-SCD)
- Paper: [An ECG biomarker for sudden cardiac death discovered with deep learning](https://www.nature.com/articles/s41586-026-10674-6), published June 24, 2026.
