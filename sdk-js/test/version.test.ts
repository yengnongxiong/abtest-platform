import { expect, test } from "vitest";

import pkg from "../package.json" with { type: "json" };
import { SDK_NAME, SDK_VERSION } from "../src/index";

// The constants are hard-coded (no build-time injection), so this guards against drift
// when package.json's version is bumped.
test("SDK name and version match package.json", () => {
  expect(SDK_NAME).toBe(pkg.name);
  expect(SDK_VERSION).toBe(pkg.version);
});
