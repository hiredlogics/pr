/** Resolve the server-side API origin; production must have an explicit URL. */
export function backendUrl(
  configured = process.env.PCN_API_URL,
  production = process.env.NODE_ENV === "production",
): string | null {
  const url = configured?.trim().replace(/\/+$/, "");
  return url || (production ? null : "http://127.0.0.1:8077");
}
