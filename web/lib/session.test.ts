import { expect, test } from "vitest";

import {
  createSessionToken,
  passwordMatches,
  SESSION_SECONDS,
  verifySessionToken,
} from "./session";

const SECRET = "a-test-secret-that-is-at-least-32-characters";

test("a fresh token verifies", async () => {
  const token = await createSessionToken(SECRET);

  expect(await verifySessionToken(token, SECRET)).toBe(true);
});

test("an expired token is refused", async () => {
  const token = await createSessionToken(SECRET, 0);

  expect(await verifySessionToken(token, SECRET, SESSION_SECONDS * 1000 + 1)).toBe(false);
});

test("an edited expiry breaks the signature", async () => {
  const [, signature] = (await createSessionToken(SECRET)).split(".");
  const forged = `${String(Date.now() + 10 ** 12)}.${signature ?? ""}`;

  expect(await verifySessionToken(forged, SECRET)).toBe(false);
});

test("another secret's token is refused", async () => {
  const token = await createSessionToken("another-secret-that-is-also-32-chars-long");

  expect(await verifySessionToken(token, SECRET)).toBe(false);
});

test.each(["", "garbage", "123", "123.", ".abc", "1.2.3", "abc.def"])(
  "malformed token %j is refused",
  async (token) => {
    expect(await verifySessionToken(token, SECRET)).toBe(false);
  },
);

test("password check", async () => {
  expect(await passwordMatches("correct horse", "correct horse")).toBe(true);
  expect(await passwordMatches("correct hors", "correct horse")).toBe(false);
  expect(await passwordMatches("", "correct horse")).toBe(false);
});
