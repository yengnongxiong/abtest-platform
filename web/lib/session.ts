/**
 * The admin session (PRD §17): one password, then an httpOnly cookie signed with an HMAC
 * keyed by SESSION_SECRET, using Web Crypto (no dependency needed).
 *
 * The cookie holds only an expiry time and its signature: there is one admin, so there is
 * nothing else to say. A forged or edited cookie fails the signature check.
 */

export const SESSION_COOKIE = "abtest_session";
export const SESSION_SECONDS = 7 * 24 * 60 * 60;

const encoder = new TextEncoder();

async function hmacKey(secret: string): Promise<CryptoKey> {
  return crypto.subtle.importKey(
    "raw",
    encoder.encode(secret),
    { name: "HMAC", hash: "SHA-256" },
    false,
    ["sign", "verify"],
  );
}

export async function createSessionToken(secret: string, now = Date.now()): Promise<string> {
  const expires = String(now + SESSION_SECONDS * 1000);
  const signature = await crypto.subtle.sign("HMAC", await hmacKey(secret), encoder.encode(expires));
  return `${expires}.${Buffer.from(signature).toString("base64url")}`;
}

/** True for an unexpired token signed with this secret. crypto.subtle.verify compares
 * signatures in constant time, so the check leaks nothing about the right signature. */
export async function verifySessionToken(
  token: string,
  secret: string,
  now = Date.now(),
): Promise<boolean> {
  const [expires, signature, ...rest] = token.split(".");
  if (!expires || !signature || rest.length > 0 || !/^\d+$/.test(expires)) {
    return false;
  }
  if (Number(expires) <= now) {
    return false;
  }
  return crypto.subtle.verify(
    "HMAC",
    await hmacKey(secret),
    Buffer.from(signature, "base64url"),
    encoder.encode(expires),
  );
}

/** Compares a typed password with the configured one in constant time: both are hashed
 * first, so even their lengths aren't compared directly. */
export async function passwordMatches(typed: string, expected: string): Promise<boolean> {
  const [a, b] = await Promise.all(
    [typed, expected].map((text) => crypto.subtle.digest("SHA-256", encoder.encode(text))),
  );
  if (!a || !b) {
    return false;
  }
  const left = new Uint8Array(a);
  const right = new Uint8Array(b);
  let difference = 0;
  for (let i = 0; i < left.length; i++) {
    difference |= (left[i] ?? 0) ^ (right[i] ?? 0);
  }
  return difference === 0;
}

export function sessionSecret(): string {
  const secret = process.env.SESSION_SECRET;
  if (!secret || secret.length < 32) {
    throw new Error("Set SESSION_SECRET (at least 32 characters) in the dashboard's environment.");
  }
  return secret;
}
