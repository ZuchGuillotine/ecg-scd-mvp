from __future__ import annotations

from pydantic import BaseModel, Field


class LeadQuality(BaseModel):
    lead: str
    present: bool
    derived: bool = False
    min_mv: float | None = None
    max_mv: float | None = None
    peak_to_peak_mv: float | None = None


class ECGRecord(BaseModel):
    study_id: str
    filename: str
    sampling_rate_hz: float
    samples: int
    seconds: float
    present_leads: list[str]
    missing_leads: list[str]
    derived_leads: list[str] = Field(default_factory=list)
    lead_quality: list[LeadQuality]
    caveats: list[str]


class JobStatus(BaseModel):
    job_id: str
    created_at: str | None = None
    updated_at: str | None = None
    records: list[ECGRecord]
    biomarkers: list[dict] = Field(default_factory=list)
    biomarker_caveats: list[str] = Field(default_factory=list)
    predictions: list[dict] = Field(default_factory=list)
    prediction_caveats: list[str] = Field(default_factory=list)


class JobSummary(BaseModel):
    job_id: str
    created_at: str
    updated_at: str
    record_count: int
    filenames: list[str] = Field(default_factory=list)
    has_biomarkers: bool = False
    has_predictions: bool = False


class ModelInfo(BaseModel):
    id: str
    label: str
    kind: str
    lead_mode: str
    ready: bool
    notes: str | None = None


class PredictionRequest(BaseModel):
    model_id: str
    allow_missing_leads: bool = False
