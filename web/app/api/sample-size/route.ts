import { NextResponse, type NextRequest } from "next/server";

import { api, ApiError } from "@/lib/api";

/** The new-experiment form's live estimate, fetched by the browser. The proxy requires a
 * session; the server key stays on the server. */
export async function GET(request: NextRequest) {
  const allowed = ["baseline", "mde_relative", "alpha", "power"];
  const params = new URLSearchParams();
  for (const name of allowed) {
    const value = request.nextUrl.searchParams.get(name);
    if (value !== null) {
      params.set(name, value);
    }
  }
  try {
    return NextResponse.json(await api.sampleSize(params));
  } catch (error) {
    if (error instanceof ApiError) {
      return NextResponse.json({ error: { code: error.code, message: error.message } }, { status: error.status });
    }
    throw error;
  }
}
