import { expect, test, vi } from "vitest";

import { revokeKey } from "./actions";

vi.mock("next/cache", () => ({ revalidatePath: vi.fn() }));
vi.mock("@/lib/auth", () => ({ requireSignedIn: vi.fn() }));
// The API refuses to revoke the project's last active server key (PRD §11), with a 409.
vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  const refusal = new actual.ApiError(409, "last_server_key", "can't revoke the project's last active server key", null);
  return { ...actual, api: { revokeApiKey: vi.fn(() => Promise.reject(refusal)) } };
});

test("a refused revoke reports the API's reason instead of failing the page", async () => {
  // Regression: the action let the error escape, so the dashboard showed its generic
  // "Something went wrong" page, which blames the API or the server key.
  const result = await revokeKey("a-key-id");

  expect(result).toEqual({ message: "can't revoke the project's last active server key" });
});
