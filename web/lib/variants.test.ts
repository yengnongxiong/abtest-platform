import { expect, test } from "vitest";

import { controlAfterRemoving } from "./variants";

test("the control stays with its variant when another variant is removed", () => {
  // Regression: removing any variant made the first variant the control, so with variant 2
  // as the control, removing variant 3 silently moved the control to variant 1.
  expect(controlAfterRemoving(1, 2)).toBe(1); // a variant after the control
  expect(controlAfterRemoving(2, 0)).toBe(1); // a variant before it: the control moves up
  expect(controlAfterRemoving(1, 1)).toBe(0); // the control itself: the first one takes over
});
