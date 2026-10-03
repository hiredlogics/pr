import { NextRequest } from "next/server";
import { cookies } from "next/headers";

export const dynamic = "force-dynamic";

const COOKIE = "pcn_admin_trace";
const MAX_AGE = 60 * 60 * 12; // 12h

function configuredToken(): string {
  return (process.env.ADMIN_TRACE_TOKEN || process.env.ADMIN_TOKEN || "").trim();
}

/**
 * POST { token } — validates against server ADMIN_TRACE_TOKEN and sets an
 * httpOnly cookie. Query `?trace=1` alone is never enough.
 *
 * GET — returns { authorised, trace_enabled } without exposing the token.
 * DELETE — clears the admin cookie.
 */
export async function POST(req: NextRequest) {
  const expected = configuredToken();
  let body: { token?: string } = {};
  try {
    body = await req.json();
  } catch {
    body = {};
  }
  const offered = String(body.token || "").trim();
  const isProd =
    process.env.APP_ENV === "production" ||
    process.env.VERCEL_ENV === "production" ||
    process.env.NODE_ENV === "production";

  // Dev with no token configured: allow unlock so local testing works.
  if (!expected) {
    if (isProd) {
      return Response.json(
        { detail: "admin endpoints are disabled: no admin token configured" },
        { status: 403 },
      );
    }
    const jar = await cookies();
    jar.set(COOKIE, "dev-open", {
      httpOnly: true,
      sameSite: "lax",
      secure: isProd,
      path: "/",
      maxAge: MAX_AGE,
    });
    return Response.json({ authorised: true, mode: "dev-open" });
  }

  if (!offered || offered.length !== expected.length) {
    return Response.json({ detail: "admin authorization required" }, { status: 401 });
  }
  // Constant-time compare
  let mismatch = 0;
  for (let i = 0; i < expected.length; i++) {
    mismatch |= offered.charCodeAt(i) ^ expected.charCodeAt(i);
  }
  if (mismatch !== 0) {
    return Response.json({ detail: "admin authorization required" }, { status: 401 });
  }

  const jar = await cookies();
  jar.set(COOKIE, "ok", {
    httpOnly: true,
    sameSite: "lax",
    secure: isProd,
    path: "/",
    maxAge: MAX_AGE,
  });
  return Response.json({ authorised: true, mode: "token" });
}

export async function GET() {
  const jar = await cookies();
  const cookie = jar.get(COOKIE)?.value;
  const expected = configuredToken();
  const authorised = Boolean(
    cookie === "ok" || (cookie === "dev-open" && !expected),
  );
  return Response.json({
    authorised,
    token_configured: Boolean(expected),
  });
}

export async function DELETE() {
  const jar = await cookies();
  jar.delete(COOKIE);
  return Response.json({ authorised: false });
}
