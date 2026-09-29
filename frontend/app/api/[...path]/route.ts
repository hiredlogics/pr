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
  // curl and some HTTP libraries add `Expect: 100-continue` once a body passes
  // 1MB. Forwarding it makes undici's fetch throw NotSupportedError before the
  // request leaves, which surfaced as "the appeal service is not responding" on
  // a backend that was healthy. Browsers never send it, so this only ever broke
  // non-browser clients.
  "expect",
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
      // duplex is required when streaming a request body in some runtimes;
      // we already buffer, so it is unused - kept out deliberately.
      redirect: "manual",
      cache: "no-store",
    });

    // Buffer the body. Forwarding `upstream.body` as a stream drops the
    // payload on Vercel for gzipped Railway responses (health, uploads),
    // which left the browser with HTTP 200 and an empty body - the UI then
    // failed JSON parse and showed "Something went wrong."
    const payload = await upstream.arrayBuffer();

    const out = new Headers(upstream.headers);
    for (const h of HOP_BY_HOP) out.delete(h);
    // undici has already decoded; re-advertising these makes clients misread
    // a plain buffer as still compressed, or disagree on length.
    out.delete("content-encoding");
    out.delete("content-length");
    out.set("Cache-Control", "no-store");

    return new Response(payload, { status: upstream.status, headers: out });
  } catch (err) {
    // A bare `catch {}` here reported "not responding" for every failure,
    // including ones the backend never saw, which sent debugging at the wrong
    // component. The cause always goes to the server log; the client still gets
    // a message it can show a customer.
    console.error(`[proxy] ${req.method} ${target} failed:`, err);
    return Response.json(
      {
        detail: {
          message: "The appeal service is not responding.",
          rejected: [],
          // undici wraps everything as "fetch failed"; the cause is the useful part.
          reason:
            process.env.NODE_ENV === "production"
              ? undefined
              : `${String(err)} | cause: ${String((err as { cause?: unknown })?.cause)}`,
        },
      },
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
