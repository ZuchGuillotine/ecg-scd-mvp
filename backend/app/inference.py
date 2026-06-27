from __future__ import annotations

import json
import pickle
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

from .config import LEAD_ORDER, MODEL_REGISTRY_PATH
from .schemas import ModelInfo


@dataclass
class ModelConfig:
    id: str
    label: str
    kind: str
    model_name: str
    params_path: Path
    model_path: Path
    calibration_path: Path | None
    lead_mode: str
    notes: str | None

    @property
    def ready(self) -> bool:
        checkpoint = self.model_path / "model.best.pth.tar"
        return self.params_path.exists() and checkpoint.exists()

    def to_info(self) -> ModelInfo:
        return ModelInfo(
            id=self.id,
            label=self.label,
            kind=self.kind,
            lead_mode=self.lead_mode,
            ready=self.ready,
            notes=self.notes,
        )


def load_model_registry(path: Path = MODEL_REGISTRY_PATH) -> dict[str, ModelConfig]:
    if not path.exists():
        example = path.with_name("model_registry.example.json")
        if not example.exists():
            return {}
        path = example

    payload = json.loads(path.read_text(encoding="utf-8"))
    models = {}
    for item in payload.get("models", []):
        calibration = item.get("calibration_path")
        config = ModelConfig(
            id=item["id"],
            label=item.get("label", item["id"]),
            kind=item.get("kind", "ecg_10s"),
            model_name=item["model_name"],
            params_path=Path(item["params_path"]).expanduser(),
            model_path=Path(item["model_path"]).expanduser(),
            calibration_path=Path(calibration).expanduser() if calibration else None,
            lead_mode=item.get("lead_mode", "12-lead"),
            notes=item.get("notes"),
        )
        models[config.id] = config
    return models


def write_prediction_inputs(job_dir: Path, records: list[dict], outputs: list[str]) -> Path:
    rows = []
    for record in records:
        row = {
            "studyId": record["study_id"],
            "ptId": record["study_id"],
            "include_modelling": True,
            "scaling_factor": 1.0,
            "age": np.nan,
            "female": np.nan,
        }
        for output in outputs:
            row[output] = 0.0
        rows.append(row)

    covariate_path = job_dir / "covariate_df.feather"
    pd.DataFrame(rows).to_feather(covariate_path)
    return covariate_path


def run_prediction(job_dir: Path, record_payloads: list[dict], model_config: ModelConfig, allow_missing_leads: bool) -> tuple[list[dict], list[str]]:
    if not model_config.ready:
        return [], [
            "Prediction was not run because the selected model artifacts are not present. Add the upstream JSON and model.best.pth.tar files under apps/ecg-scd-mvp/models."
        ]

    incomplete = [record for record in record_payloads if record.get("missing_leads")]
    if incomplete and not allow_missing_leads:
        return [], [
            "Prediction blocked: one or more ECGs are missing leads. Re-run with missing-lead predictions explicitly enabled, or use a compatible limited-lead model artifact."
        ]

    try:
        from ekg_scd import predict as upstream_predict
    except Exception as exc:
        return [], [f"Prediction was not run because the upstream ekg_scd package could not be imported: {exc}"]

    params = json.loads(model_config.params_path.read_text(encoding="utf-8"))
    outputs = params["outputs"]
    covariate_path = write_prediction_inputs(job_dir, record_payloads, outputs)
    val_ids = [record["study_id"] for record in record_payloads]

    predictions = upstream_predict.predict(
        val_ids=val_ids,
        outputs=outputs,
        preprocessing=params.get("preprocessing"),
        regress=params.get("regress", [False] * len(outputs)),
        model=params["model"],
        model_path=str(model_config.model_path),
        sig_length=5000,
        cumulative_predictor=params.get("cumulative_predictor", False),
        train_ids=val_ids,
        in_sample=False,
        dropout=params.get("dropout", 0.5),
        covariate_conditioning=params.get("covariate_conditioning"),
        covariate_df_path=str(covariate_path),
        x_dir=str(job_dir / "ecgs"),
        conv_channels=params.get("conv_channels", 128),
        attention=params.get("attention", "max"),
        rep_mp=params.get("rep_mp", 4),
        num_rep_blocks=params.get("num_rep_blocks", 12),
    )

    pred_cols = [f"{output}_hat" for output in outputs]
    calibration_path = model_config.calibration_path
    if calibration_path and calibration_path.exists() and "scd1_hat" in pred_cols:
        with calibration_path.open("rb") as handle:
            calibration_model = pickle.load(handle)
        predictions["scd1_hat"] = calibration_model.predict_proba(predictions[["scd1_hat"]].values)[:, 1]

    out_path = job_dir / "predictions.feather"
    predictions.to_feather(out_path)
    rows = predictions[["studyId", *pred_cols]].to_dict("records")
    caveats = [
        "Scores are research outputs from supplied model artifacts, not clinical recommendations.",
        "Compare cohorts using the same file format, lead set, and preprocessing assumptions whenever possible.",
    ]
    if incomplete:
        caveats.append("At least one prediction used zero-filled missing leads and should be treated as lower fidelity.")
    return rows, caveats


def save_record_array(job_dir: Path, study_id: str, array: np.ndarray) -> None:
    ecg_dir = job_dir / "ecgs"
    ecg_dir.mkdir(parents=True, exist_ok=True)
    np.save(ecg_dir / f"{study_id}.npy", array.astype(np.float32))
