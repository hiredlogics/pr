import { NextRequest } from "next/server";

/**
 * Catch-all proxy to the FastAPI app. The browser only ever talks to Next:
 * FastAPI has no CORS configuration, and this keeps its address server-side.
 *
 * The raw body is forwarded byte-for-byte so multipart boundaries survive
 * untouched - re-encoding a FormData here would risk mangling uploads.
 */
const BACKEND = process.env.PCN_API_URL ?? "http://127.0.0.1:8077";

// Uploads are user photos; don't let Next cache any of this.
export const dynamic = "force-dynamic";

const HOP_BY_HOP = new Set([
  "connection",
  "keep-alive",
  "transfer-encoding",
  "upgrade",
  "host",
  "content-length",
]);

async function forward(req: NextRequest, path: string[]): Promise<Response> {
  const target = `${BACKEND}/${path.map(encodeURIComponent).join("/")}${req.nextUrl.search}`;

  const headers = new Headers();
  req.headers.forEach((value, key) => {
    if (!HOP_BY_HOP.has(key.toLowerCase())) headers.set(key, value);
  });

  const hasBody = req.method !== "GET" && req.method !== "HEAD";

  try {
    const upstream = await fetch(target, {
      method: req.method,
      headers,
      body: hasBody ? await req.arrayBuffer() : undefined,
      redirect: "manual",
      cache: "no-store",
    });

    const out = new Headers(upstream.headers);
    for (const h of HOP_BY_HOP) out.delete(h);
    out.set("Cache-Control", "no-store");

    return new Response(upstream.body, { status: upstream.status, headers: out });
  } catch {
    return Response.json(
      { detail: { message: "The appeal service is not responding.", rejected: [] } },
      { status: 502 },
    );
  }
}

type Ctx = { params: Promise<{ path: string[] }> };

export async function GET(req: NextRequest, ctx: Ctx) {
  return forward(req, (await ctx.params).path);
}
export async function POST(req: NextRequest, ctx: Ctx) {
  return forward(req, (await ctx.params).path);
}
export async function PUT(req: NextRequest, ctx: Ctx) {
  return forward(req, (await ctx.params).path);
}
export async function PATCH(req: NextRequest, ctx: Ctx) {
  return forward(req, (await ctx.params).path);
}
export async function DELETE(req: NextRequest, ctx: Ctx) {
  return forward(req, (await ctx.params).path);
}
