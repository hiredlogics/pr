/** Client for /api/admin/api/db/* — admin session cookie required. */

async function adminFetch(path: string, init?: RequestInit): Promise<Response> {
  return fetch(`/api/admin/${path}`, {
    ...init,
    cache: "no-store",
    headers: {
      accept: "application/json",
      ...(init?.headers || {}),
    },
  });
}

export async function getSession(): Promise<{ authorised: boolean; token_configured?: boolean }> {
  const res = await fetch("/api/admin/session", { cache: "no-store" });
  return res.json();
}

export async function unlockSession(token: string): Promise<{ authorised: boolean; detail?: string }> {
  const res = await fetch("/api/admin/session", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ token }),
  });
  const body = await res.json().catch(() => ({}));
  if (!res.ok) return { authorised: false, detail: body.detail || "unauthorized" };
  return body;
}

export async function dbStatus() {
  const res = await adminFetch("api/db/status");
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

export async function dbTables(counts = true) {
  const res = await adminFetch(`api/db/tables?counts=${counts ? "true" : "false"}`);
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

export async function dbTableRows(
  schema: string,
  table: string,
  params: Record<string, string | number | boolean | undefined>,
) {
  const q = new URLSearchParams();
  Object.entries(params).forEach(([k, v]) => {
    if (v !== undefined && v !== "") q.set(k, String(v));
  });
  const res = await adminFetch(
    `api/db/tables/${encodeURIComponent(schema)}/${encodeURIComponent(table)}/rows?${q}`,
  );
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

export async function dbCases(showSensitive = false) {
  const res = await adminFetch(`api/db/cases?show_sensitive=${showSensitive}`);
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

export async function dbCase(caseId: string, showSensitive = false) {
  const res = await adminFetch(
    `api/db/cases/${encodeURIComponent(caseId)}?show_sensitive=${showSensitive}`,
  );
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

export async function dbKnowledge(params: { q?: string; role?: string; page?: number }) {
  const q = new URLSearchParams();
  if (params.q) q.set("q", params.q);
  if (params.role) q.set("role", params.role);
  if (params.page) q.set("page", String(params.page));
  const res = await adminFetch(`api/db/knowledge?${q}`);
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

export async function vectorStatus() {
  const res = await adminFetch("api/db/vector/status");
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

export async function vectorHealth() {
  const res = await adminFetch("api/db/vector/health");
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

export async function vectorEmbeddings(params: {
  page?: number;
  show_raw_vector?: boolean;
  q?: string;
}) {
  const q = new URLSearchParams();
  if (params.page) q.set("page", String(params.page));
  if (params.show_raw_vector) q.set("show_raw_vector", "true");
  if (params.q) q.set("q", params.q);
  const res = await adminFetch(`api/db/vector/embeddings?${q}`);
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

export async function vectorSearch(query: string, limit = 10) {
  const res = await adminFetch("api/db/vector/search", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ query, limit }),
  });
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

export async function diagnostics(queryId: string) {
  const res = await adminFetch(`api/db/diagnostics/${encodeURIComponent(queryId)}`);
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

export async function diagnosticsList() {
  const res = await adminFetch("api/db/diagnostics");
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}
