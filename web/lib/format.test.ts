import { describe, expect, test } from "vitest";

import {
  daysNeeded,
  daysRunning,
  liftTicks,
  pValue,
  resultSentence,
  signedPercent,
  type SentenceInput,
} from "./format";
import type { Comparison } from "./types";

function comparison(overrides: Partial<Comparison> = {}): Comparison {
  return {
    variant_key: "b",
    abs_diff: 0.0041,
    rel_lift: 0.041,
    ci_low: 0.0012,
    ci_high: 0.007,
    rel_ci_low: 0.012,
    rel_ci_high: 0.07,
    p_value: 0.004,
    significant: true,
    insufficient_data: null,
    verdict: "significant_win",
    ...overrides,
  };
}

function input(overrides: Partial<SentenceInput> = {}): SentenceInput {
  return {
    variantName: "Variant B",
    metricName: "checkout conversion",
    metricKind: "conversion",
    direction: "increase",
    analysisType: "fixed_horizon",
    alpha: 0.05,
    comparison: comparison(),
    ...overrides,
  };
}

describe("resultSentence", () => {
  test("reads like the PRD's example", () => {
    expect(resultSentence(input())).toBe(
      "Variant B increased checkout conversion by 4.1% (95% CI +1.2% to +7.0%). " +
        "This is statistically significant.",
    );
  });

  test("names the always-valid interval for sequential analyses, and says 'yet'", () => {
    const sentence = resultSentence(
      input({
        analysisType: "sequential",
        comparison: comparison({
          rel_lift: -0.02,
          rel_ci_low: -0.05,
          rel_ci_high: 0.01,
          significant: false,
          verdict: "not_significant",
        }),
      }),
    );

    expect(sentence).toBe(
      "Variant B decreased checkout conversion by 2.0% (95% always-valid CI −5.0% to +1.0%). " +
        "This is not statistically significant yet.",
    );
  });

  test("an SRM verdict replaces the result", () => {
    const sentence = resultSentence(input({ comparison: comparison({ verdict: "srm_untrustworthy" }) }));

    expect(sentence).toMatch(/^These results can't be trusted/);
    expect(sentence).not.toMatch(/4\.1%/);
  });

  test("insufficient data and a zero baseline say so", () => {
    const none = { rel_lift: null, rel_ci_low: null, rel_ci_high: null };
    expect(
      resultSentence(
        input({ comparison: comparison({ ...none, insufficient_data: "too few", verdict: "insufficient_data" }) }),
      ),
    ).toBe("Not enough data to compare Variant B yet.");
    expect(resultSentence(input({ comparison: comparison(none) }))).toMatch(/control's checkout conversion is zero/);
  });

  test("an empty always-valid CI still reports the lift and its significance", () => {
    // Regression: when the mSPRT's intersected CI is empty (PRD §14), only the CI is left
    // out, but the sentence claimed the control's rate was zero.
    const noCi = { ci_low: null, ci_high: null, rel_ci_low: null, rel_ci_high: null };
    const sentence = resultSentence(input({ analysisType: "sequential", comparison: comparison(noCi) }));

    expect(sentence).toBe(
      "Variant B increased checkout conversion by 4.1% (no 95% always-valid CI: the checks so far " +
        "disagree about the effect's size). This is statistically significant.",
    );
  });

  test("uses the experiment's confidence level", () => {
    expect(resultSentence(input({ alpha: 0.1 }))).toContain("(90% CI");
  });
});

test("formatting helpers", () => {
  expect(signedPercent(0.073)).toBe("+7.3%");
  expect(signedPercent(-0.02)).toBe("−2.0%");
  expect(signedPercent(0.00001)).toBe("0.0%");
  expect(pValue(0.0004)).toBe("< 0.001");
  expect(pValue(0.2291)).toBe("0.229");
  expect(pValue(null)).toBe("n/a");
});

test("daysNeeded", () => {
  // 14,751 per variant, 2 variants, half the traffic, 2,000 visitors a day.
  expect(daysNeeded(14_751, 2, 0.5, 2_000)).toBe(30);
  expect(daysNeeded(14_751, 2, 0.5, 0)).toBeNull();
});

test("daysRunning", () => {
  const started = "2026-09-01T00:00:00Z";
  const now = Date.parse("2026-09-03T12:00:00Z");
  expect(daysRunning({ started_at: null, stopped_at: null }, now)).toBeNull();
  expect(daysRunning({ started_at: started, stopped_at: null }, now)).toBe(3);
  expect(daysRunning({ started_at: started, stopped_at: "2026-09-02T00:00:00Z" }, now)).toBe(1);
});

test("liftTicks", () => {
  // A CI of -2.1% to +16.5% gets 5% steps, from the step below the low end to the one above.
  expect(liftTicks(-0.021, 0.165).map((t) => signedPercent(t, 0))).toEqual([
    "−5%", "0%", "+5%", "+10%", "+15%", "+20%",
  ]);
  // Zero is always on the axis, even when the whole CI is above it.
  expect(liftTicks(0.012, 0.03).map((t) => signedPercent(t, 0))).toEqual(["0%", "+1%", "+2%", "+3%"]);
});
