import type { NextConfig } from "next";

// The browser only talks to this origin. Proxying to FastAPI is handled by the
// route handler at app/api/[...path]/route.ts rather than a rewrite here,
// because it forwards the raw request body byte-for-byte - a rewrite risks
// mangling multipart boundaries on file uploads.
const nextConfig: NextConfig = {};

export default nextConfig;
