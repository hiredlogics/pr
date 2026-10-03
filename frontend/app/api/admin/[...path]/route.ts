import { NextRequest } from "next/server";
import { cookies } from "next/headers";

/**
 * Admin-only proxy. Injects ADMIN_TRACE_TOKEN server-side. Never reachable
 * through the customer catch-all at /api/[...path].
 *
 * Allowed paths (relative to /admin/... on FastAPI, or /cases/{id}/trace):
 *   cases/{id}/console
 *   cases/{id}/console/compare
 *   cases/{id}/console/report.txt
 *   cases/{id}/audit
 *   cases/{id}/execution-trace
 *   cases/{id}/claim-plans
 *   cases/{id}/trace          (legacy rich trace; mapped to /cases/...)
 */
const BACKEND = process.env.PCN_API_URL ?? "http://127.0.0.1:8077";
export const dynamic = "force-dynamic";

const COOKIE = "pcn_admin_trace";

const ALLOWED: ReadonlyArray<RegExp> = [
  /^cases\/[^/]+\/console$/,
  /^cases\/[^/]+\/console\/compare$/,
  /^cases\/[^/]+\/console\/report\.txt$/,
  /^cases\/[^/]+\/audit$/,
  /^cases\/[^/]+\/execution-trace$/,
  /^cases\/[^/]+\/claim-plans$/,
  /^cases\/[^/]+\/trace$/,
];

function configuredToken(): string {
  return (process.env.ADMIN_TRACE_TOKEN || process.env.ADMIN_TOKEN || "").trim();
}

async function authorised(): Promise<boolean> {
  const jar = await cookies();
  const cookie = jar.get(COOKIE)?.value;
  const expected = configuredToken();
  if (cookie === "ok" && expected) return true;
  if (cookie === "dev-open" && !expected) return true;
  // Dev with no token and no cookie: still allow (mirrors backend _require_admin)
  if (!expected && process.env.NODE_ENV !== "production" && process.env.APP_ENV !== "production") {
    return true;
  }
  return false;
}

function allowed(path: string[]): boolean {
  if (path.some((seg) => seg === "" || seg.includes("/") || seg === "." || seg === "..")) {
    return false;
  }
  return ALLOWED.some((re) => re.test(path.join("/")));
}

/** Map proxy path → FastAPI path. Most live under /admin/; /trace is under /cases/. */
function upstreamPath(path: string[]): string {
  const joined = path.join("/");
  if (/^cases\/[^/]+\/trace$/.test(joined)) {
    return joined;
  }
  return `admin/${joined}`;
}

async function forward(req: NextRequest, path: string[]): Promise<Response> {
  if (!(await authorised())) {
    return Response.json({ detail: "admin authorization required" }, { status: 401 });
  }
  if (!allowed(path)) {
    return Response.json({ detail: { message: "Not found." } }, { status: 404 });
  }

  const target = `${BACKEND}/${upstreamPath(path).split("/").map(encodeURIComponent).join("/")}${req.nextUrl.search}`;
  const headers = new Headers();
  headers.set("accept", req.headers.get("accept") || "application/json");
  const token = configuredToken();
  if (token) {
    headers.set("authorization", `Bearer ${token}`);
    headers.set("x-admin-token", token);
  }

  try {
    const upstream = await fetch(target, {
      method: req.method,
      headers,
      redirect: "manual",
      cache: "no-store",
    });
    const payload = await upstream.arrayBuffer();
    const out = new Headers();
    const ct = upstream.headers.get("content-type");
    if (ct) out.set("content-type", ct);
    out.set("cache-control", "no-store");
    return new Response(payload, { status: upstream.status, headers: out });
  } catch {
    return Response.json(
      { detail: "The appeal service is not responding." },
      { status: 502 },
    );
  }
}

export async function GET(req: NextRequest, ctx: { params: Promise<{ path: string[] }> }) {
  const { path } = await ctx.params;
  return forward(req, path);
}
