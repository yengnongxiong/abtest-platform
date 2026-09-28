"""Simulator entry point.

    python -m abtest.simulator validate --seed 42    # Monte Carlo validation -> docs/results

Run it from the repository root (`make simulate` does), so the default output directory is
the repo's docs/results.
"""

import argparse
import os
import platform
import sys
import time
from pathlib import Path

import numpy as np

from abtest.simulator.report import RunInfo, ValidationRun, write_report
from abtest.simulator.validation import run_aa, run_power, run_skewed_means, run_srm

# Simulated experiments per scenario. The A/A count sets how tightly the false-positive
# rate is pinned down (a 95% Monte Carlo band of about +/- 0.3 points around 5%); SRM false
# alarms are rare events, so they get many experiments too.
AA_EXPERIMENTS = 20_000
POWER_EXPERIMENTS = 2_000
SRM_BUGGY_EXPERIMENTS = 2_000
SRM_HEALTHY_EXPERIMENTS = 20_000
SKEWED_EXPERIMENTS = 10_000


def validate(seed: int, out_dir: Path) -> None:
    started = time.perf_counter()
    # One independent random stream per scenario: changing one scenario's size never
    # changes another scenario's numbers.
    aa_seed, power_seed, srm_seed, skewed_seed = np.random.SeedSequence(seed).spawn(4)

    def progress(scenario: str) -> None:
        print(f"{scenario} done ({time.perf_counter() - started:.0f} s)", file=sys.stderr)

    aa = run_aa(np.random.default_rng(aa_seed), AA_EXPERIMENTS)
    progress("A/A")
    power = run_power(np.random.default_rng(power_seed), POWER_EXPERIMENTS)
    progress("power")
    srm = run_srm(np.random.default_rng(srm_seed), SRM_BUGGY_EXPERIMENTS, SRM_HEALTHY_EXPERIMENTS)
    progress("SRM")
    skewed = run_skewed_means(np.random.default_rng(skewed_seed), SKEWED_EXPERIMENTS)
    progress("skewed means")
    info = RunInfo(
        command=f"python -m abtest.simulator validate --seed {seed}",
        seed=seed,
        runtime_seconds=time.perf_counter() - started,
        machine=f"{platform.system()} {platform.machine()}, {os.cpu_count()} CPUs, "
        f"Python {platform.python_version()}",
    )
    write_report(ValidationRun(aa, power, srm, skewed, info), out_dir)
    print(f"wrote {out_dir}/summary.md and charts in {info.runtime_seconds:.0f} s", file=sys.stderr)


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m abtest.simulator")
    commands = parser.add_subparsers(dest="command", required=True)
    validate_parser = commands.add_parser("validate", help="Monte Carlo validation of the stats")
    validate_parser.add_argument("--seed", type=int, default=42)
    validate_parser.add_argument("--out", type=Path, default=Path("docs/results"))
    args = parser.parse_args()
    validate(args.seed, args.out)


if __name__ == "__main__":
    main()
