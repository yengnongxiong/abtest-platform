import { describe, expect, test } from "vitest";

import vectors from "../../shared/hash_test_vectors.json" with { type: "json" };
import {
  assign,
  bucket,
  chooseVariant,
  flagEnabled,
  hash32,
  hasLoneSurrogate,
  type WeightedVariant,
} from "../src/assignment";

// The same vectors the Python suite checks: agreement here means the SDK and the server
// assign every user identically (PRD §9).
describe("shared hash vectors", () => {
  test.each(vectors.hashes)("hash of $input", ({ input, hash, bucket: expected }) => {
    expect(hash32(input)).toBe(hash);
    expect(bucket(input)).toBe(expected);
  });

  test.each(vectors.assignments)(
    "$experiment at $traffic_bp bp for $user",
    ({ experiment, user, traffic_bp, variants, variant }) => {
      expect(assign(experiment, user, traffic_bp, variants)).toBe(variant);
    },
  );

  test.each(vectors.flags)(
    "$flag (enabled $enabled, $rollout_bp bp) for $user",
    ({ flag, user, enabled, rollout_bp, on }) => {
      expect(flagEnabled(flag, user, enabled, rollout_bp)).toBe(on);
    },
  );
});

describe("assignment", () => {
  const threeWay: WeightedVariant[] = [
    { key: "a", weight_bp: 3300, position: 0 },
    { key: "b", weight_bp: 3300, position: 1 },
    { key: "c", weight_bp: 3400, position: 2 },
  ];

  test("walks variants by position, not list order", () => {
    const backwards = [...threeWay].reverse();
    for (let i = 0; i < 1000; i++) {
      expect(chooseVariant("order", `user-${String(i)}`, backwards)).toBe(
        chooseVariant("order", `user-${String(i)}`, threeWay),
      );
    }
  });

  test("weights that don't cover every bucket are an error", () => {
    const short: WeightedVariant[] = [{ key: "only", weight_bp: 10, position: 0 }];
    let user = "";
    for (let i = 0; bucket(`x:variant:${user}`) < 10 || user === ""; i++) {
      user = `user-${String(i)}`;
    }
    expect(() => chooseVariant("x", user, short)).toThrow(/sum to 10/);
  });

  test("detects lone surrogates", () => {
    expect(hasLoneSurrogate("user-\ud800")).toBe(true);
    expect(hasLoneSurrogate("user-\udc00-x")).toBe(true);
    expect(hasLoneSurrogate("user-😀")).toBe(false); // a proper surrogate pair
    expect(hasLoneSurrogate("user-1")).toBe(false);
  });
});
