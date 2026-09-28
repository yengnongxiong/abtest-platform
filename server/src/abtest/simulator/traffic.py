"""End-to-end traffic generator (PRD §16B): simulated users flow through the real API.

It creates and starts an experiment through the admin API, then plays users as the SDK
would: assign each one locally with the real assignment code, send an exposure, and sometimes
a purchase, in batches to POST /v1/events. It finishes by asking for a fresh snapshot and
printing it next to the true effect. The random draws are seeded, so which users convert is
reproducible; the timestamps are real.
"""

import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Self
from uuid import uuid4

import httpx2
import numpy as np
import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from abtest.assignment import WeightedVariant, assign

BATCH_SIZE = 500  # events per request, the API's maximum


class ScenarioVariant(BaseModel):
    model_config = ConfigDict(extra="forbid")
    key: str
    name: str
    weight_bp: int
    is_control: bool = False
    conversion_rate: float = Field(ge=0, le=1)  # the true rate the simulation draws from


class ScenarioExperiment(BaseModel):
    model_config = ConfigDict(extra="forbid")
    key: str
    name: str
    hypothesis: str
    traffic_bp: int
    analysis_type: str = "sequential"
    mde_relative: float
    expected_baseline: float
    variants: list[ScenarioVariant]


class ScenarioMetric(BaseModel):
    model_config = ConfigDict(extra="forbid")
    key: str
    name: str
    event_name: str


class SrmBug(BaseModel):
    """A logging bug: this share of one variant's exposures never reaches the server."""

    model_config = ConfigDict(extra="forbid")
    variant: str
    drop_rate: float = Field(gt=0, lt=1)


class Scenario(BaseModel):
    model_config = ConfigDict(extra="forbid")
    seed: int
    users: int = Field(gt=0)
    arrival_rate: float = Field(gt=0)  # users per second
    metric: ScenarioMetric
    experiment: ScenarioExperiment
    srm_bug: SrmBug | None = None

    @model_validator(mode="after")
    def check_bug_variant(self) -> Self:
        keys = {v.key for v in self.experiment.variants}
        if self.srm_bug is not None and self.srm_bug.variant not in keys:
            raise ValueError(f"srm_bug.variant must be one of {sorted(keys)}")
        return self

    @classmethod
    def load(cls, path: Path) -> Self:
        return cls.model_validate(yaml.safe_load(path.read_text()))


def run(scenario: Scenario, admin: httpx2.Client, public: httpx2.Client) -> dict[str, Any]:
    """Run the scenario; return the results the API computed.

    `admin` sends the server key and `public` the client key, both to the same API.
    """
    experiment = scenario.experiment
    _create(admin, scenario)
    started = admin.post(f"/admin/experiments/{experiment.key}/start")
    _check(started)
    # Assign from the served config, exactly as the SDK does.
    config = next(
        e for e in _check(public.get("/v1/config")).json()["experiments"]
        if e["key"] == experiment.key
    )  # fmt: skip
    variants = [
        WeightedVariant(v["key"], v["weight_bp"], v["position"]) for v in config["variants"]
    ]
    rates = {v.key: v.conversion_rate for v in experiment.variants}

    rng = np.random.default_rng(scenario.seed)
    # Send events stamped at least 1 s after the start by the server's clock: this machine's
    # clock may trail the database's, and events that predate the start aren't attributed.
    not_before = datetime.fromisoformat(started.json()["started_at"]) + timedelta(seconds=1)
    while datetime.now(UTC) < not_before:
        time.sleep(0.05)

    began = time.monotonic()
    batch: list[dict[str, Any]] = []
    for i in range(scenario.users):
        user = f"{experiment.key}-user-{i}"
        variant = assign(experiment.key, user, config["traffic_bp"], variants)
        if variant is None:
            continue  # outside the experiment's traffic: the SDK logs nothing
        now = datetime.now(UTC)
        bug = scenario.srm_bug
        lost = bug is not None and bug.variant == variant and rng.random() < bug.drop_rate
        if not lost:
            properties = {"experiment_key": experiment.key, "variant_key": variant}
            batch.append(_event("$exposure", user, now, properties))
        if rng.random() < rates[variant]:
            batch.append(_event(scenario.metric.event_name, user, now + timedelta(milliseconds=1)))
        if len(batch) >= BATCH_SIZE - 1:
            _send(public, batch)
            batch = []
            # Pace arrivals: user i arrives at i / arrival_rate seconds.
            time.sleep(max(0.0, began + i / scenario.arrival_rate - time.monotonic()))
    _send(public, batch)

    _check(admin.post(f"/admin/experiments/{experiment.key}/recompute"))
    results: dict[str, Any] = _check(
        admin.get(f"/admin/experiments/{experiment.key}/results")
    ).json()
    return results


def summary(scenario: Scenario, results: dict[str, Any]) -> str:
    """A plain-text report: what happened, next to what was true."""
    data = results["latest"]["data"]
    control = next(v for v in scenario.experiment.variants if v.is_control)
    lines = [
        f"experiment {results['experiment_key']}: {data['users']} users, "
        f"{data['analysis_type']} analysis",
        f"SRM check: p = {_fmt(data['srm']['p_value'])}, "
        f"{'FLAGGED' if data['srm']['flagged'] else 'not flagged'}",
    ]
    for variant in data["variants"]:
        lines.append(
            f"  {variant['key']}: {variant['users']} users, {variant['conversions']} converted"
        )
    for comparison in data["comparisons"]:
        truth = next(v for v in scenario.experiment.variants if v.key == comparison["variant_key"])
        true_lift = truth.conversion_rate / control.conversion_rate - 1
        low, high = comparison["rel_ci_low"], comparison["rel_ci_high"]
        contains = low is not None and high is not None and low <= true_lift <= high
        lines += [
            f"{comparison['variant_key']} vs {control.key}: lift {_pct(comparison['rel_lift'])}, "
            f"95% CI {_pct(low)} to {_pct(high)}, p = {_fmt(comparison['p_value'])}, "
            f"verdict {comparison['verdict']}",
            f"  true lift {_pct(true_lift)}: "
            f"{'inside' if contains else 'OUTSIDE'} the confidence interval",
        ]
    return "\n".join(lines)


def _create(admin: httpx2.Client, scenario: Scenario) -> None:
    metric = scenario.metric
    response = admin.post(
        "/admin/metrics",
        json={"key": metric.key, "name": metric.name, "kind": "conversion",
              "event_name": metric.event_name, "direction": "increase"},
    )  # fmt: skip
    if response.status_code != 409:  # an existing metric is reused
        _check(response)
    experiment = scenario.experiment
    body = {
        "key": experiment.key,
        "name": experiment.name,
        "hypothesis": experiment.hypothesis,
        "traffic_bp": experiment.traffic_bp,
        "analysis_type": experiment.analysis_type,
        "mde_relative": experiment.mde_relative,
        "variants": [
            {"key": v.key, "name": v.name, "weight_bp": v.weight_bp, "is_control": v.is_control}
            for v in experiment.variants
        ],
        "metrics": [
            {"metric_key": metric.key, "role": "primary",
             "expected_baseline": experiment.expected_baseline}
        ],
    }  # fmt: skip
    response = admin.post("/admin/experiments", json=body)
    if response.status_code == 409:
        raise SystemExit(
            f"experiment {experiment.key!r} already exists: rerun with --experiment-key <new key>"
        )
    _check(response)


def _event(
    name: str, user: str, at: datetime, properties: dict[str, str] | None = None
) -> dict[str, Any]:
    event: dict[str, Any] = {
        "event_id": str(uuid4()),
        "user_id": user,
        "name": name,
        "occurred_at": at.isoformat(),
    }
    if properties is not None:
        event["properties"] = properties
    return event


def _send(public: httpx2.Client, events: list[dict[str, Any]]) -> None:
    if not events:
        return
    body = {"sdk": {"name": "abtest-traffic-generator", "version": "1"}, "events": events}
    for _ in range(5):  # retry on 429 and 5xx, as the SDK does
        response = public.post("/v1/events", json=body)
        if response.status_code == 429 or response.status_code >= 500:
            time.sleep(float(response.headers.get("Retry-After", "1")))
            continue
        _check(response)
        rejected = response.json()["rejected"]
        if rejected:
            raise SystemExit(f"the API rejected generated events: {rejected[:3]}")
        return
    raise SystemExit("the API kept refusing a batch")


def _check(response: httpx2.Response) -> httpx2.Response:
    if response.is_error:
        raise SystemExit(f"{response.request.method} {response.request.url.path} failed: "
                         f"{response.status_code} {response.text}")  # fmt: skip
    return response


def _pct(value: float | None) -> str:
    return "n/a" if value is None else f"{value:+.1%}"


def _fmt(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.3g}"
