"""Request and response models for the admin and public APIs (PRD §11).

Request models forbid unknown fields, so a typo like "weight" for "weight_bp" is an error
instead of being silently ignored. In PATCH models, a missing or null field means "leave it
unchanged".
"""

from datetime import datetime
from typing import Annotated, Any, Literal, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from abtest.keys import KeyKind

# Metric, flag, experiment, and variant keys. Never ":" (hash inputs are joined with it).
Key = Annotated[str, StringConstraints(pattern=r"^[a-z0-9][a-z0-9_-]{0,63}$")]
EventName = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9_$.:-]{1,100}$")]
Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
BasisPoints = Annotated[int, Field(ge=0, le=10_000)]
MetricKind = Literal["conversion", "mean"]
Direction = Literal["increase", "decrease"]
Status = Literal["draft", "running", "stopped"]
AnalysisType = Literal["fixed_horizon", "sequential"]
Role = Literal["primary", "secondary", "guardrail"]


class RequestModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


# --- Metrics ----------------------------------------------------------------------------------


class MetricCreate(RequestModel):
    key: Key
    name: Name
    kind: MetricKind
    event_name: EventName
    direction: Direction
    window_hours: int = Field(default=168, gt=0, le=24 * 365)


class MetricUpdate(RequestModel):
    name: Name | None = None
    # Definition fields: editable only while no started experiment uses the metric.
    kind: MetricKind | None = None
    event_name: EventName | None = None
    direction: Direction | None = None
    window_hours: int | None = Field(default=None, gt=0, le=24 * 365)


class Metric(BaseModel):
    key: str
    name: str
    kind: MetricKind
    event_name: str
    direction: Direction
    window_hours: int
    created_at: datetime


# --- Flags ------------------------------------------------------------------------------------


class FlagCreate(RequestModel):
    key: Key
    description: str = Field(default="", max_length=2000)
    enabled: bool = False
    rollout_bp: BasisPoints = 0


class FlagUpdate(RequestModel):
    description: str | None = Field(default=None, max_length=2000)
    enabled: bool | None = None
    rollout_bp: BasisPoints | None = None


class Flag(BaseModel):
    key: str
    description: str
    enabled: bool
    rollout_bp: int
    created_at: datetime
    updated_at: datetime


# --- Experiments ------------------------------------------------------------------------------


class VariantIn(RequestModel):
    key: Key
    name: Name
    weight_bp: int = Field(ge=1, le=10_000)
    is_control: bool = False


class ExperimentMetricIn(RequestModel):
    metric_key: Key
    role: Role
    # The PM's pre-registered baseline: a rate for a conversion metric, a per-user mean for
    # a mean metric. It sets the metric's mSPRT tau. Required to start.
    expected_baseline: float | None = Field(default=None, gt=0)


def _check_variants_and_metrics(
    variants: list[VariantIn] | None, metrics: list[ExperimentMetricIn] | None
) -> None:
    """Catch clashes early, with a clear message, instead of as a database error."""
    if variants is not None:
        keys = [v.key for v in variants]
        if len(set(keys)) != len(keys):
            raise ValueError("variant keys must be unique")
        if sum(v.is_control for v in variants) > 1:
            raise ValueError("at most one variant can be the control")
    if metrics is not None:
        keys = [m.metric_key for m in metrics]
        if len(set(keys)) != len(keys):
            raise ValueError("a metric can be attached only once")
        if sum(m.role == "primary" for m in metrics) > 1:
            raise ValueError("at most one metric can be primary")


class ExperimentCreate(RequestModel):
    key: Key
    name: Name
    hypothesis: str = Field(default="", max_length=5000)
    traffic_bp: BasisPoints
    analysis_type: AnalysisType = "sequential"
    alpha: float = Field(default=0.05, gt=0, lt=1)
    mde_relative: float | None = Field(default=None, gt=0)
    # Variants in list order become positions 0, 1, 2, ...
    variants: list[VariantIn] = Field(default_factory=list, max_length=20)
    metrics: list[ExperimentMetricIn] = Field(default_factory=list, max_length=50)

    @model_validator(mode="after")
    def check_lists(self) -> Self:
        _check_variants_and_metrics(self.variants, self.metrics)
        return self


class ExperimentUpdate(RequestModel):
    """Drafts can change anything. A running experiment can only be renamed or get more
    traffic; a stopped one can only be renamed."""

    name: Name | None = None
    hypothesis: str | None = Field(default=None, max_length=5000)
    traffic_bp: BasisPoints | None = None
    analysis_type: AnalysisType | None = None
    alpha: float | None = Field(default=None, gt=0, lt=1)
    mde_relative: float | None = Field(default=None, gt=0)
    # When given, these replace the whole list.
    variants: list[VariantIn] | None = Field(default=None, max_length=20)
    metrics: list[ExperimentMetricIn] | None = Field(default=None, max_length=50)

    @model_validator(mode="after")
    def check_lists(self) -> Self:
        _check_variants_and_metrics(self.variants, self.metrics)
        return self


class StopRequest(RequestModel):
    reason: Name


class CloneRequest(RequestModel):
    new_key: Key


class Variant(BaseModel):
    key: str
    name: str
    weight_bp: int
    is_control: bool
    position: int


class ExperimentMetric(BaseModel):
    metric_key: str
    kind: MetricKind
    role: Role
    expected_baseline: float | None


class Change(BaseModel):
    action: str
    details: dict[str, Any]
    created_at: datetime


class Experiment(BaseModel):
    key: str
    name: str
    hypothesis: str
    status: Status
    traffic_bp: int
    analysis_type: AnalysisType
    alpha: float
    mde_relative: float | None
    started_at: datetime | None
    stopped_at: datetime | None
    stop_reason: str | None
    created_at: datetime
    updated_at: datetime
    variants: list[Variant]
    metrics: list[ExperimentMetric]


class ExperimentDetail(Experiment):
    changes: list[Change]


# --- API keys and sample size -----------------------------------------------------------------


class ApiKeyCreate(RequestModel):
    kind: KeyKind


class ApiKey(BaseModel):
    id: UUID
    kind: KeyKind
    key_prefix: str
    created_at: datetime
    revoked_at: datetime | None


class ApiKeyCreated(ApiKey):
    key: str = Field(description="The full key. It is shown only this once.")


class SampleSize(BaseModel):
    users_per_variant: int


# --- Public config (GET /v1/config) -----------------------------------------------------------


class ConfigFlag(BaseModel):
    key: str
    enabled: bool
    rollout_bp: int


class ConfigVariant(BaseModel):
    key: str
    weight_bp: int
    position: int


class ConfigExperiment(BaseModel):
    key: str
    traffic_bp: int
    variants: list[ConfigVariant]


class Config(BaseModel):
    config_version: int
    flags: list[ConfigFlag]
    experiments: list[ConfigExperiment]
