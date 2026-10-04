"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { getSession, unlockSession } from "@/lib/admin_db";

const NAV = [
  { href: "/admin/database", label: "Overview" },
  { href: "/admin/database/cases", label: "Cases" },
  { href: "/admin/database/knowledge", label: "Knowledge" },
  { href: "/admin/database/vectors", label: "Vectors" },
  { href: "/admin/database/tables", label: "Tables" },
  { href: "/admin/database/health", label: "Health" },
];

export default function AdminLayout({ children }: { children: React.ReactNode }) {
  const [auth, setAuth] = useState<"loading" | "ok" | "need">("loading");
  const [token, setToken] = useState("");
  const [err, setErr] = useState("");

  useEffect(() => {
    getSession()
      .then((s) => setAuth(s.authorised ? "ok" : "need"))
      .catch(() => setAuth("need"));
  }, []);

  async function unlock(e: React.FormEvent) {
    e.preventDefault();
    setErr("");
    const r = await unlockSession(token);
    if (r.authorised) setAuth("ok");
    else setErr(typeof r.detail === "string" ? r.detail : "Unauthorized");
  }

  if (auth === "loading") {
    return <div className="admin-db-shell"><p className="admin-muted">Checking admin session…</p></div>;
  }

  if (auth !== "ok") {
    return (
      <div className="admin-db-shell">
        <h1 className="admin-title">Admin Data Explorer</h1>
        <p className="admin-muted">Admin authentication required. Customers cannot access these routes.</p>
        <form onSubmit={unlock} className="admin-card admin-stack">
          <label>
            Admin token
            <input
              type="password"
              value={token}
              onChange={(e) => setToken(e.target.value)}
              autoComplete="off"
              className="admin-input"
            />
          </label>
          {err && <p className="admin-error">{err}</p>}
          <button type="submit" className="btn btn-primary">Unlock</button>
        </form>
      </div>
    );
  }

  return (
    <div className="admin-db-shell">
      <header className="admin-top">
        <div>
          <p className="admin-kicker">Admin only · read-only</p>
          <h1 className="admin-title">Live Data Explorer</h1>
        </div>
        <nav className="admin-nav">
          {NAV.map((item) => (
            <Link key={item.href} href={item.href} className="admin-nav-link">
              {item.label}
            </Link>
          ))}
        </nav>
      </header>
      <main>{children}</main>
    </div>
  );
}
