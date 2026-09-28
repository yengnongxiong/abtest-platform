"""Request and response models for the admin and public APIs (PRD §11).

Request models forbid unknown fields, so a typo like "weight" for "weight_bp" is an error
instead of being silently ignored. In PATCH models, a missing or null field means "leave it
unchanged".
"""

import json
from collections.abc import Iterator
from datetime import datetime
from typing import Annotated, Any, Literal, Self
from uuid import UUID

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

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


class LatestResults(BaseModel):
    """The newest snapshot of the primary metric, in brief."""

    computed_at: datetime
    users: int
    srm_flagged: bool


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
    latest_results: LatestResults | None


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


# --- Public events (POST /v1/events) ----------------------------------------------------------

EXPOSURE_EVENT = "$exposure"
MAX_PROPERTIES_BYTES = 4096


class SdkInfo(BaseModel):
    name: str = Field(max_length=100)
    version: str = Field(max_length=50)


class EventBatch(BaseModel):
    """The envelope. Each event is validated on its own (EventIn), so one bad event is
    rejected with a reason instead of failing the whole batch."""

    sdk: SdkInfo
    events: list[Any] = Field(max_length=500)


class EventIn(RequestModel):
    event_id: UUID
    user_id: str
    name: EventName
    occurred_at: AwareDatetime  # must carry a time zone: a naive time is ambiguous
    value: Annotated[float, Field(strict=True, allow_inf_nan=False)] | None = None
    properties: dict[str, Any] = Field(default_factory=dict)

    @field_validator("user_id")
    @classmethod
    def check_user_id(cls, user_id: str) -> str:
        """1-200 characters of valid Unicode. A lone surrogate can't be encoded as UTF-8 (the
        SDK would hash something else), and Postgres text can't hold NUL."""
        if not 1 <= len(user_id) <= 200:
            raise ValueError("must be 1-200 characters")
        _check_storable(user_id)
        return user_id

    @field_validator("properties")
    @classmethod
    def check_properties(cls, properties: dict[str, Any]) -> dict[str, Any]:
        for text in _strings(properties):
            _check_storable(text)
        encoded = json.dumps(properties, ensure_ascii=False, separators=(",", ":"))
        if len(encoded.encode()) > MAX_PROPERTIES_BYTES:
            raise ValueError(f"must be at most {MAX_PROPERTIES_BYTES} bytes as JSON")
        return properties

    @model_validator(mode="after")
    def check_exposure(self) -> Self:
        if self.name == EXPOSURE_EVENT:
            for field in ("experiment_key", "variant_key"):
                if not isinstance(self.properties.get(field), str):
                    raise ValueError(f"an exposure needs properties.{field}")
        return self


def _check_storable(text: str) -> None:
    """Postgres rejects NUL in text and JSON; UTF-8 can't encode a lone surrogate."""
    if "\x00" in text:
        raise ValueError("must not contain NUL characters")
    try:
        text.encode()
    except UnicodeEncodeError:
        raise ValueError("must be valid Unicode (no lone surrogates)") from None


def _strings(value: Any) -> Iterator[str]:
    """Every string (keys too) in a JSON value. Iterative, so deep nesting can't overflow
    the stack."""
    stack = [value]
    while stack:
        item = stack.pop()
        if isinstance(item, str):
            yield item
        elif isinstance(item, dict):
            yield from item.keys()
            stack.extend(item.values())
        elif isinstance(item, list):
            stack.extend(item)


class Rejection(BaseModel):
    index: int
    reason: str


class EventsResult(BaseModel):
    """accepted + duplicates + len(rejected) = the number of events sent."""

    accepted: int
    duplicates: int
    rejected: list[Rejection]


# --- Results snapshots (the worker writes them; GET .../results reads them) -------------------


class MSPRTStateOut(BaseModel):
    """The mSPRT's running minimum p-value and intersected CI after a look (see stats)."""

    p_value: float
    ci_low: float
    ci_high: float


class VariantSummary(BaseModel):
    key: str
    is_control: bool
    weight_bp: int
    users: int
    # Conversion metrics: converters. Mean metrics: the per-user sum and sum of squares.
    conversions: int | None
    total: float
    total_sq: float


class Comparison(BaseModel):
    """One treatment variant against the control."""

    variant_key: str
    abs_diff: float | None
    rel_lift: float | None
    ci_low: float | None
    ci_high: float | None
    rel_ci_low: float | None
    rel_ci_high: float | None
    p_value: float | None
    significant: bool
    insufficient_data: str | None
    verdict: str
    # Sequential analysis only: what the next look continues from.
    msprt_state: MSPRTStateOut | None = None


class SrmOut(BaseModel):
    p_value: float | None
    flagged: bool
    insufficient_data: str | None


class SnapshotData(BaseModel):
    """Everything one look at an experiment's metric produced (results_snapshots.data)."""

    cutoff: datetime
    analysis_type: AnalysisType
    alpha: float
    tau: float | None  # the mSPRT mixing standard deviation; None for fixed-horizon
    metric_key: str
    metric_kind: MetricKind
    direction: Direction
    window_hours: int
    users: int
    conflicted_users: int
    mismatched_users: int
    srm: SrmOut
    variants: list[VariantSummary]
    comparisons: list[Comparison]


class Snapshot(BaseModel):
    computed_at: datetime
    data: SnapshotData


class SeriesComparison(BaseModel):
    variant_key: str
    rel_lift: float | None
    rel_ci_low: float | None
    rel_ci_high: float | None
    p_value: float | None


class SeriesPoint(BaseModel):
    """A compact view of one snapshot, for the lift-over-time chart."""

    computed_at: datetime
    users: int
    srm_flagged: bool
    comparisons: list[SeriesComparison]


class Results(BaseModel):
    experiment_key: str
    metric_key: str
    latest: Snapshot | None
    series: list[SeriesPoint]


class Recomputed(BaseModel):
    metric_keys: list[str]
