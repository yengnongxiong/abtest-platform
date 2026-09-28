import { cookies } from "next/headers";
import { redirect } from "next/navigation";

import {
  createSessionToken,
  passwordMatches,
  SESSION_COOKIE,
  SESSION_SECONDS,
  sessionSecret,
  verifySessionToken,
} from "./session";

/** For server actions: they can be POSTed to directly, so the proxy's check isn't enough. */
export async function requireSignedIn(): Promise<void> {
  const token = (await cookies()).get(SESSION_COOKIE)?.value;
  if (token === undefined || !(await verifySessionToken(token, sessionSecret()))) {
    redirect("/login");
  }
}

/** Checks the password and, if it's right, sets the session cookie. */
export async function signIn(password: string): Promise<boolean> {
  const expected = process.env.ADMIN_PASSWORD;
  if (!expected) {
    throw new Error("Set ADMIN_PASSWORD in the dashboard's environment.");
  }
  if (!(await passwordMatches(password, expected))) {
    return false;
  }
  (await cookies()).set(SESSION_COOKIE, await createSessionToken(sessionSecret()), {
    httpOnly: true, // no script on the page can read it
    sameSite: "lax", // not sent on cross-site POSTs
    secure: process.env.NODE_ENV === "production", // local development runs on plain http
    maxAge: SESSION_SECONDS,
    path: "/",
  });
  return true;
}

export async function signOut(): Promise<void> {
  (await cookies()).delete(SESSION_COOKIE);
}
