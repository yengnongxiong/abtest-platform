import { NextResponse, type NextRequest } from "next/server";

import { SESSION_COOKIE, sessionSecret, verifySessionToken } from "@/lib/session";

/**
 * Every page but /login needs a valid session cookie; without one, pages redirect to /login
 * and the one API route answers 401. Server actions check again (lib/auth.ts), because they
 * can be called directly.
 */
export async function proxy(request: NextRequest) {
  const token = request.cookies.get(SESSION_COOKIE)?.value;
  if (token !== undefined && (await verifySessionToken(token, sessionSecret()))) {
    return NextResponse.next();
  }
  if (request.nextUrl.pathname.startsWith("/api/")) {
    return NextResponse.json({ error: { code: "unauthorized", message: "Sign in first" } }, { status: 401 });
  }
  return NextResponse.redirect(new URL("/login", request.url));
}

export const config = {
  // Everything except the login page itself and Next.js's own static files.
  matcher: ["/((?!login$|_next/static/|_next/image|favicon\\.ico$).*)"],
};
