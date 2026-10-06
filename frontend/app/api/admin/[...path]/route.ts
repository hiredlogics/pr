import { NextRequest } from "next/server";
import { cookies } from "next/headers";
import { backendUrl } from "@/lib/backend";

/**
 * Admin-only proxy. Injects ADMIN_TRACE_TOKEN server-side. Never reachable
 * through the customer catch-all at /api/[...path].
 *
 * Allowed paths (relative to /admin/... on FastAPI, or /cases/{id}/trace):
 *   cases/{id}/console|compare|report|audit|execution-trace|claim-plans|trace
 *   api/db/*  (PostgreSQL + pgvector live explorer — read-only)
 */
const BACKEND = backendUrl();
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
  // Live data explorer (read-only APIs)
  /^api\/db\/status$/,
  /^api\/db\/tables$/,
  /^api\/db\/tables\/[^/]+\/[^/]+$/,
  /^api\/db\/tables\/[^/]+\/[^/]+\/rows$/,
  /^api\/db\/cases$/,
  /^api\/db\/cases\/[^/]+$/,
  /^api\/db\/knowledge$/,
  /^api\/db\/vector\/status$/,
  /^api\/db\/vector\/columns$/,
  /^api\/db\/vector\/indexes$/,
  /^api\/db\/vector\/embeddings$/,
  /^api\/db\/vector\/health$/,
  /^api\/db\/vector\/search$/,
  /^api\/db\/diagnostics$/,
  /^api\/db\/diagnostics\/[^/]+$/,
  /^api\/db\/diagnostics\/select$/,
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
  if (!BACKEND) {
    console.error("[admin proxy] PCN_API_URL is missing or empty in production");
    return Response.json(
      { detail: { message: "The appeal service is temporarily unavailable." } },
      { status: 503 },
    );
  }

  const target = `${BACKEND}/${upstreamPath(path).split("/").map(encodeURIComponent).join("/")}${req.nextUrl.search}`;
  const headers = new Headers();
  headers.set("accept", req.headers.get("accept") || "application/json");
  const ct = req.headers.get("content-type");
  if (ct) headers.set("content-type", ct);
  const token = configuredToken();
  if (token) {
    headers.set("authorization", `Bearer ${token}`);
    headers.set("x-admin-token", token);
  }

  try {
    const hasBody = req.method !== "GET" && req.method !== "HEAD";
    const upstream = await fetch(target, {
      method: req.method,
      headers,
      body: hasBody ? await req.arrayBuffer() : undefined,
      redirect: "manual",
      cache: "no-store",
    });
    const payload = await upstream.arrayBuffer();
    const out = new Headers();
    const outCt = upstream.headers.get("content-type");
    if (outCt) out.set("content-type", outCt);
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

export async function POST(req: NextRequest, ctx: { params: Promise<{ path: string[] }> }) {
  const { path } = await ctx.params;
  return forward(req, path);
}
