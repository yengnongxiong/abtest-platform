"""Charts and summary.md for a validation run. Every number written comes from the run.

Colors follow the analysis method on every chart (fixed-horizon blue, mSPRT orange, naive
peeking aqua), in the categorical order of a palette checked for color-blind separation.
Reference lines are gray.
"""

import textwrap
from dataclasses import dataclass
from pathlib import Path

from matplotlib.axes import Axes
from matplotlib.figure import Figure
from matplotlib.layout_engine import ConstrainedLayoutEngine
from matplotlib.ticker import PercentFormatter
from scipy.stats import binom

from abtest.simulator.validation import (
    AAResult,
    PowerResult,
    Rate,
    SkewedResult,
    SRMScenarioResult,
)

SURFACE = "#fcfcfb"
INK = "#0b0b0b"
INK_SECONDARY = "#52514e"
GRID = "#e4e3df"
FIXED = "#2a78d6"
SEQUENTIAL = "#eb6834"
PEEKING = "#1baf7a"


@dataclass(frozen=True, slots=True)
class RunInfo:
    """How the numbers were produced, recorded next to them."""

    command: str
    seed: int
    runtime_seconds: float
    machine: str


@dataclass(frozen=True, slots=True)
class ValidationRun:
    aa: AAResult
    power: PowerResult
    srm: SRMScenarioResult
    skewed: SkewedResult
    info: RunInfo


def calibrated_range(trials: int, rate: float) -> tuple[float, float]:
    """The central 95% range of the observed rate for a test whose true rate is `rate`:
    the 2.5% and 97.5% quantiles of Binomial(trials, rate), divided by trials."""
    low = float(binom.ppf(0.025, trials, rate))
    high = float(binom.ppf(0.975, trials, rate))
    return low / trials, high / trials


def write_report(run: ValidationRun, out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    _aa_chart(run.aa).savefig(out_dir / "aa_false_positives.png", dpi=150)
    _power_chart(run.power).savefig(out_dir / "power.png", dpi=150)
    _srm_chart(run.srm).savefig(out_dir / "srm.png", dpi=150)
    _skewed_chart(run.skewed).savefig(out_dir / "welch_skewed.png", dpi=150)
    (out_dir / "summary.md").write_text(_summary(run))


# --- Charts ----------------------------------------------------------------------------------


def _figure(
    width: float, height: float, title: str, subtitle: str, panels: int = 1
) -> tuple[Figure, list[Axes]]:
    """A figure with a left-aligned title, a wrapped subtitle, and recessive axes.

    The plots are laid out below the text block, which is sized to the subtitle's length.
    """
    lines = textwrap.wrap(subtitle, width=int(width * 13))  # about 13 characters per inch
    text_inches = 0.5 + 0.19 * len(lines)
    fig = Figure(
        figsize=(width, height),
        facecolor=SURFACE,
        layout=ConstrainedLayoutEngine(rect=(0, 0, 1, 1 - text_inches / height)),
    )
    fig.text(0.01, 1 - 0.12 / height, title, va="top", fontsize=12, color=INK, fontweight="bold")
    fig.text(0.01, 1 - 0.42 / height, "\n".join(lines), va="top", fontsize=9,
             color=INK_SECONDARY, linespacing=1.3)  # fmt: skip
    axes = fig.subplots(1, panels, sharey=True, squeeze=False)[0]
    for ax in axes:
        ax.set_facecolor(SURFACE)
        ax.grid(axis="y", color=GRID, linewidth=0.8)
        ax.set_axisbelow(True)
        for side in ("top", "right"):
            ax.spines[side].set_visible(False)
        for side in ("left", "bottom"):
            ax.spines[side].set_color(INK_SECONDARY)
        ax.tick_params(colors=INK_SECONDARY, labelsize=9)
        ax.yaxis.set_major_formatter(PercentFormatter(xmax=1, decimals=0))
    return fig, list(axes)


def _plot_rates(
    ax: Axes, x: list[float], rates: list[Rate], color: str, label: str, marker: str = "o"
) -> None:
    """A series with its 95% CI as a light band."""
    lows, highs = zip(*(rate.ci for rate in rates), strict=True)
    ax.fill_between(x, lows, highs, color=color, alpha=0.15, linewidth=0)
    ax.plot(x, [r.value for r in rates], color=color, linewidth=2, marker=marker, markersize=4,
            label=label)  # fmt: skip


def _label_end(ax: Axes, x: float, y: float, text: str) -> None:
    """A value label just right of a series' last point, in text ink (not the series color)."""
    ax.annotate(text, (x, y), xytext=(8, 0), textcoords="offset points", va="center",
                fontsize=9, color=INK)  # fmt: skip


def _aa_chart(aa: AAResult) -> Figure:
    fig, (ax,) = _figure(
        8,
        4.8,
        "A/A tests: how often a change that does nothing is declared significant",
        f"{aa.experiments:,} simulated A/A experiments, {aa.baseline:.0%} conversion, a look "
        f"every {aa.users_per_look:,} users. Shaded bands are 95% Monte Carlo CIs.",
    )
    x = [aa.users_per_look * (k + 1) / 1000 for k in range(aa.looks)]
    ax.axhspan(*calibrated_range(aa.experiments, aa.alpha), color=GRID, linewidth=0,
               label=f"Where a calibrated {aa.alpha:.0%} test lands 95% of the time")  # fmt: skip
    ax.axhline(aa.alpha, color=INK_SECONDARY, linewidth=1)
    _plot_rates(ax, x, aa.peeking_by_look, PEEKING, "Naive peeking: z-test at every look")
    _plot_rates(ax, x, aa.sequential_by_look, SEQUENTIAL, "mSPRT at every look (always-valid)")
    low, high = aa.fixed.ci
    ax.errorbar([x[-1]], [aa.fixed.value], yerr=[[aa.fixed.value - low], [high - aa.fixed.value]],
                fmt="D", color=FIXED, markersize=6, capsize=3,
                label="Fixed horizon: one z-test at the end")  # fmt: skip
    for rate in (aa.peeking_by_look[-1], aa.sequential_by_look[-1], aa.fixed):
        _label_end(ax, x[-1], rate.value, f"{rate.value:.1%}")
    ax.set_xticks(range(0, int(x[-1]) + 1, 5))
    ax.set_xlim(0, x[-1] * 1.08)
    ax.set_ylim(0, None)
    ax.set_xlabel("Users so far, both variants (thousands)", color=INK_SECONDARY)
    ax.set_ylabel("Experiments declared significant", color=INK_SECONDARY)
    ax.legend(loc="upper left", fontsize=9, frameon=False, labelcolor=INK)
    return fig


def _power_chart(power: PowerResult) -> Figure:
    fig, axes = _figure(
        11,
        4.3,
        "Power: how often a real lift is detected, by sample size",
        f"{power.experiments:,} simulated experiments per lift, {power.baseline:.0%} baseline. "
        "The vertical gray line marks the planned sample size for 80% power.",
        panels=len(power.curves),
    )
    for ax, curve in zip(axes, power.curves, strict=True):
        x = [n / 1000 for n in curve.users_per_variant]
        ax.plot(x, curve.analytic, color=INK_SECONDARY, linewidth=1.2, label="Theory (z-test)")
        _plot_rates(ax, x, curve.fixed, FIXED, "Fixed horizon: z-test at this n")
        _plot_rates(ax, x, curve.sequential, SEQUENTIAL, "mSPRT: significant by this n", "s")
        ax.axvline(curve.n_for_80_percent / 1000, color=GRID, linewidth=1.5, zorder=0)
        ax.set_title(f"True lift +{curve.lift:.0%}  (80% power at {curve.n_for_80_percent:,})",
                     fontsize=10, color=INK, loc="left")  # fmt: skip
        ax.set_xlabel("Users per variant (thousands)", color=INK_SECONDARY)
        ax.set_ylim(0, 1.02)
    axes[0].set_ylabel("Experiments detecting the lift", color=INK_SECONDARY)
    axes[0].legend(loc="lower right", fontsize=8, frameon=False, labelcolor=INK)
    return fig


def _srm_chart(srm: SRMScenarioResult) -> Figure:
    fig, (ax,) = _figure(
        8,
        4.6,
        f"SRM check: catching a bug that loses {srm.drop_rate:.0%} of treatment exposures",
        f"{srm.experiments:,} simulated buggy experiments on a 50/50 split, flagged when "
        f"p < 0.001. For comparison, healthy experiments were flagged "
        f"{srm.false_alarm_single_look.value:.2%} of the time at one look, and "
        f"{srm.false_alarm_any_look.value:.2%} of the time at any of {srm.looks} looks.",
    )
    x = [n / 1000 for n in srm.total_users]
    _plot_rates(ax, x, srm.detection, FIXED, "SRM flagged")
    ax.set_ylim(0, 1.02)
    ax.set_xlim(0, None)
    ax.set_xlabel("Users so far, both variants (thousands)", color=INK_SECONDARY)
    ax.set_ylabel("Buggy experiments flagged", color=INK_SECONDARY)
    return fig


def _skewed_chart(skewed: SkewedResult) -> Figure:
    fig, (ax,) = _figure(
        8,
        4.6,
        "Welch t-test on a skewed revenue metric: false-positive rate by sample size",
        f"{skewed.experiments:,} A/A experiments per sample size, per-user values "
        f"lognormal(0, {skewed.sigma}). Gray band: where a calibrated {skewed.alpha:.0%} test "
        "lands 95% of the time. Shaded blue: 95% Monte Carlo CIs.",
    )
    x = [float(n) for n in skewed.users_per_variant]
    ax.axhspan(*calibrated_range(skewed.experiments, skewed.alpha), color=GRID, linewidth=0)
    ax.axhline(skewed.alpha, color=INK_SECONDARY, linewidth=1)
    _plot_rates(ax, x, skewed.false_positive, FIXED, "Welch t-test")
    ax.set_xscale("log")
    ax.set_xticks(x, labels=[f"{n:,}" for n in skewed.users_per_variant])
    ax.minorticks_off()
    ax.set_ylim(0, None)
    ax.set_xlabel("Users per variant (log scale)", color=INK_SECONDARY)
    ax.set_ylabel("A/A experiments declared significant", color=INK_SECONDARY)
    return fig


# --- summary.md ------------------------------------------------------------------------------


def _pct(rate: Rate) -> str:
    low, high = rate.ci
    return f"{rate.value:.2%} ({low:.2%} to {high:.2%})"


def _summary(run: ValidationRun) -> str:
    aa, power, srm, skewed, info = run.aa, run.power, run.srm, run.skewed, run.info
    band_low, band_high = calibrated_range(aa.experiments, aa.alpha)
    fixed_ok = band_low <= aa.fixed.value <= band_high
    sequential = aa.sequential_by_look[-1]
    sequential_ok = sequential.value <= band_high  # not significantly above alpha
    peeking = aa.peeking_by_look[-1]

    lines = [
        "# Monte Carlo validation results",
        "",
        "Generated by the simulator; do not edit by hand. Rerunning the same command "
        "reproduces every number exactly (same seed, library versions, and platform); only the "
        "runtime changes.",
        "",
        f"- Command: `{info.command}`",
        f"- Seed: {info.seed}",
        f"- Runtime: {info.runtime_seconds:.0f} s on {info.machine}",
        "",
        "## Acceptance checks (PRD §22, M2)",
        "",
        "| Check | Result | |",
        "|---|---|---|",
        f"| A/A fixed-horizon false-positive rate is inside the 95% range of a calibrated "
        f"{aa.alpha:.0%} test ({band_low:.2%} to {band_high:.2%}) "
        f"| {aa.fixed.value:.2%} | {'PASS' if fixed_ok else 'FAIL'} |",
        f"| A/A sequential (mSPRT) false-positive rate is at most {aa.alpha:.0%}, within Monte "
        f"Carlo error (at most {band_high:.2%}) | {sequential.value:.2%} "
        f"| {'PASS' if sequential_ok else 'FAIL'} |",
        "",
        "## 1-3. A/A tests: fixed horizon, naive peeking, sequential",
        "",
        f"{aa.experiments:,} A/A experiments (both variants convert at {aa.baseline:.0%}), "
        f"with a look every {aa.users_per_look:,} users (both variants together) for "
        f"{aa.looks} looks, {aa.users_per_look * aa.looks:,} users in total. alpha = {aa.alpha}. "
        f"The mSPRT uses tau = baseline x MDE = {aa.baseline} x {aa.mde_relative} = "
        f"{aa.tau:g}. Rates are shown with exact 95% binomial CIs.",
        "",
        "| Analysis | False-positive rate (95% CI) |",
        "|---|---|",
        f"| Fixed horizon: one z-test after {aa.users_per_look * aa.looks:,} users "
        f"| {_pct(aa.fixed)} |",
        f"| Naive peeking: z-test at each of {aa.looks} looks, stop at the first p < "
        f"{aa.alpha} | {_pct(peeking)} |",
        f"| mSPRT at each of {aa.looks} looks | {_pct(sequential)} |",
        "",
        f"Naive peeking produces {peeking.value / aa.alpha:.1f}x the nominal rate of false "
        "positives.",
        "",
        "![A/A false-positive rates](aa_false_positives.png)",
        "",
        "## 4. Power: fixed horizon vs sequential",
        "",
        f"{power.experiments:,} experiments per true lift, {power.baseline:.0%} baseline, "
        f"alpha = {power.alpha}, {power.looks} looks. A detection is a significant win. "
        "tau = baseline x the true lift. Theory is the z-test's analytic power curve.",
        "",
        "| True lift | Users per variant for 80% power | Fixed horizon at that n | Theory "
        "| mSPRT by that n | mSPRT first reaches 80% at |",
        "|---|---|---|---|---|---|",
    ]
    for curve in power.curves:
        k = min(range(len(curve.users_per_variant)),
                key=lambda i: abs(curve.users_per_variant[i] - curve.n_for_80_percent))  # fmt: skip
        reached = next(
            (n for n, r in zip(curve.users_per_variant, curve.sequential, strict=True)
             if r.value >= 0.8),
            None,
        )  # fmt: skip
        reached_text = (
            f"{reached:,} ({reached / curve.n_for_80_percent:.2f}x)"
            if reached
            else "not within the simulated range"
        )
        lines.append(
            f"| +{curve.lift:.0%} | {curve.n_for_80_percent:,} "
            f"| {_pct(curve.fixed[k])} at {curve.users_per_variant[k]:,} "
            f"| {curve.analytic[k]:.2%} | {_pct(curve.sequential[k])} | {reached_text} |"
        )
    lines += [
        "",
        "![Power curves](power.png)",
        "",
        "## 5. Sample ratio mismatch",
        "",
        f"A bug loses {srm.drop_rate:.0%} of treatment exposures on a 50/50 split. "
        f"{srm.experiments:,} buggy experiments; flagged when the chi-square p < 0.001.",
        "",
        "| Users so far (both variants) | Buggy experiments flagged (95% CI) |",
        "|---|---|",
    ]
    for n, rate in list(zip(srm.total_users, srm.detection, strict=True))[1::2]:
        lines.append(f"| {n:,} | {_pct(rate)} |")
    lines += [
        "",
        f"False alarms on {srm.false_alarm_single_look.trials:,} healthy 50/50 experiments, "
        f"checked every 1,000 users up to {srm.looks * 1000:,}:",
        "",
        f"- flagged at the final look: {_pct(srm.false_alarm_single_look)}",
        f"- flagged at any of the {srm.looks} looks: {_pct(srm.false_alarm_any_look)}",
        "",
        "A per-look threshold of 0.001 is not a per-experiment false-alarm rate: an SRM check "
        "repeated on every snapshot has its own peeking problem.",
        "",
        "![SRM detection](srm.png)",
        "",
        "## 6. Welch t-test on a skewed metric",
        "",
        f"{skewed.experiments:,} A/A experiments per sample size; per-user values are "
        f"lognormal(0, {skewed.sigma}) in both variants. alpha = {skewed.alpha}. A calibrated "
        f"test lands in {calibrated_range(skewed.experiments, skewed.alpha)[0]:.2%} to "
        f"{calibrated_range(skewed.experiments, skewed.alpha)[1]:.2%} 95% of the time.",
        "",
        "| Users per variant | False-positive rate (95% CI) |",
        "|---|---|",
    ]
    for n, rate in zip(skewed.users_per_variant, skewed.false_positive, strict=True):
        lines.append(f"| {n:,} | {_pct(rate)} |")
    lines += ["", "![Welch on skewed data](welch_skewed.png)", ""]
    return "\n".join(lines)
