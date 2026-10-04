"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import {
  fetchCaseConsole,
  fetchCopyReport,
  fetchRunCompare,
  getTraceSession,
  lockTrace,
  unlockTrace,
  type CaseConsole,
  type RunCompare,
  type TraceStatus,
} from "@/lib/trace";
import { Accordion, CopyId, Empty, JsonBlock, Kv, StatusChip } from "./TraceBits";

type Filter =
  | "ALL"
  | "ERRORS"
  | "FACTS"
  | "FINDINGS"
  | "GROUNDS"
  | "QUESTIONS"
  | "RELATIONSHIPS"
  | "VALIDATION"
  | "LOGS";

const FILTERS: Filter[] = [
  "ALL", "ERRORS", "FACTS", "FINDINGS", "GROUNDS",
  "QUESTIONS", "RELATIONSHIPS", "VALIDATION", "LOGS",
];

function fmtMs(ms?: number | null) {
  if (ms == null) return "—";
  if (ms < 1000) return `${ms} ms`;
  return `${(ms / 1000).toFixed(2)} s`;
}

function showSection(filter: Filter, key: Filter | "PIPELINE" | "SUMMARY" | "CLAIM" | "DRAFT" | "COMPARE" | "WHY") {
  if (filter === "ALL") return true;
  if (filter === "ERRORS") {
    return ["WHY", "GROUNDS", "VALIDATION", "CLAIM", "DRAFT"].includes(key);
  }
  return filter === key || (key === "CLAIM" && filter === "GROUNDS");
}

export default function CaseTraceConsole({ caseId }: { caseId: string }) {
  const [session, setSession] = useState<{ authorised: boolean; token_configured: boolean } | null>(null);
  const [token, setToken] = useState("");
  const [unlocking, setUnlocking] = useState(false);
  const [unlockError, setUnlockError] = useState<string | null>(null);
  const [data, setData] = useState<CaseConsole | null>(null);
  const [compare, setCompare] = useState<RunCompare | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [filter, setFilter] = useState<Filter>("ALL");
  const [showRaw, setShowRaw] = useState(false);
  const [graphOpen, setGraphOpen] = useState(false);
  const [copyState, setCopyState] = useState<"idle" | "done" | "fail">("idle");

  const refreshSession = useCallback(async () => {
    try {
      setSession(await getTraceSession());
    } catch {
      setSession({ authorised: false, token_configured: true });
    }
  }, []);

  useEffect(() => {
    void refreshSession();
  }, [refreshSession]);

  const load = useCallback(async (raw = false) => {
    setLoading(true);
    setLoadError(null);
    try {
      const [consoleData, cmp] = await Promise.all([
        fetchCaseConsole(caseId, raw),
        fetchRunCompare(caseId).catch(() => null),
      ]);
      setData(consoleData);
      setCompare(cmp);
    } catch (e) {
      setLoadError(e instanceof Error ? e.message : "Trace failed to load");
      setData(null);
    } finally {
      setLoading(false);
    }
  }, [caseId]);

  useEffect(() => {
    if (session?.authorised) void load(false);
  }, [session?.authorised, load]);

  async function onUnlock(e: { preventDefault: () => void }) {
    e.preventDefault();
    setUnlocking(true);
    setUnlockError(null);
    const ok = await unlockTrace(token);
    setUnlocking(false);
    if (!ok) {
      setUnlockError("Admin token rejected.");
      return;
    }
    setToken("");
    await refreshSession();
  }

  async function onCopyReport() {
    try {
      const text = await fetchCopyReport(caseId);
      await navigator.clipboard.writeText(text);
      setCopyState("done");
    } catch {
      setCopyState("fail");
    }
    setTimeout(() => setCopyState("idle"), 2000);
  }

  const errorCount = useMemo(() => {
    if (!data) return 0;
    let n = (data.grounds?.integrity_errors || []).length;
    n += (data.draft_context?.lossy_boundaries || []).length;
    n += (data.identity_issues || []).length;
    n += (data.validation?.checks || []).filter((c) => c.status === "FAIL").length;
    n += (data.pipeline || []).filter((p) => p.status === "FAIL" || p.status === "BLOCKED").length;
    return n;
  }, [data]);

  if (!session) {
    return (
      <div className="trace-console card stack" data-testid="case-trace-console">
        <h2>Case Intelligence Trace</h2>
        <p className="lede">Checking admin authorisation…</p>
      </div>
    );
  }

  if (!session.authorised) {
    return (
      <div className="trace-console card stack" data-testid="case-trace-console">
        <h2>Case Intelligence Trace</h2>
        <p className="lede">
          Test mode is requested (<code>?trace=1</code>), but admin authorisation is required.
          Enter the admin token to unlock the diagnostic console. Ordinary customers never see this.
        </p>
        <form className="stack" onSubmit={onUnlock}>
          <label className="trace-label">
            Admin token
            <input
              className="trace-input"
              type="password"
              autoComplete="current-password"
              value={token}
              onChange={(e) => setToken(e.target.value)}
              placeholder={session.token_configured ? "ADMIN_TRACE_TOKEN" : "Dev unlock (no token set)"}
            />
          </label>
          {unlockError && <p className="trace-error">{unlockError}</p>}
          <button type="submit" className="btn btn-primary trace-btn-full" disabled={unlocking}>
            {unlocking ? "Unlocking…" : "Unlock trace"}
          </button>
        </form>
      </div>
    );
  }

  return (
    <div className="trace-console" data-testid="case-trace-console">
      <header className="trace-sticky">
        <div className="trace-sticky-row">
          <h2>Case Intelligence Trace</h2>
          <StatusChip status={data?.summary.final_state || "LOADING"} />
        </div>
        {data?.summary.synthetic_label && (
          <p className="trace-synthetic">{data.summary.synthetic_label}</p>
        )}
        <div className="trace-actions">
          <button type="button" className="btn btn-secondary" onClick={() => load(showRaw)} disabled={loading}>
            {loading ? "Loading…" : "Refresh"}
          </button>
          <button type="button" className="btn btn-secondary" onClick={onCopyReport}>
            {copyState === "done" ? "Copied" : copyState === "fail" ? "Copy failed" : "Copy case trace"}
          </button>
          <button
            type="button"
            className="btn btn-secondary"
            onClick={async () => {
              await lockTrace();
              setData(null);
              await refreshSession();
            }}
          >
            Lock
          </button>
        </div>
        <div className="trace-filters" role="toolbar" aria-label="Trace filters">
          {FILTERS.map((f) => (
            <button
              key={f}
              type="button"
              className={`trace-filter${filter === f ? " is-active" : ""}`}
              onClick={() => setFilter(f)}
            >
              {f}
              {f === "ERRORS" && errorCount > 0 ? ` (${errorCount})` : ""}
            </button>
          ))}
        </div>
      </header>

      {loadError && (
        <div className="notice" data-tone="attention" data-testid="trace-load-error">
          <h3>Trace could not load</h3>
          <p>{loadError}</p>
          <p>The customer result above is unaffected.</p>
        </div>
      )}

      {loading && !data && <p className="lede">Loading case trace…</p>}

      {data && (
        <div className="trace-body stack">
          {showSection(filter, "SUMMARY") && (
            <section className="trace-card stack">
              <h3>Summary</h3>
              <dl className="trace-summary">
                <Kv label="Case ID"><CopyId id={data.summary.case_id} /></Kv>
                <Kv label="Execution ID">
                  {data.summary.execution_id
                    ? <CopyId id={data.summary.execution_id} />
                    : "—"}
                </Kv>
                <Kv label="Case revision">{data.summary.case_revision ?? "—"}</Kv>
                <Kv label="Final outcome">{data.summary.final_outcome ?? "—"}</Kv>
                <Kv label="App version">{data.summary.app_version ?? "—"}</Kv>
                <Kv label="Git commit">
                  {data.summary.git_commit
                    ? <CopyId id={String(data.summary.git_commit)} />
                    : "—"}
                </Kv>
                <Kv label="KB release">{data.summary.kb_release ?? "—"}</Kv>
                <Kv label="Prompt versions">
                  <code className="trace-inline">
                    {JSON.stringify(data.summary.prompt_versions || {})}
                  </code>
                </Kv>
                <Kv label="Model / provider">
                  {data.summary.provider || "—"}{" "}
                  <code className="trace-inline">{JSON.stringify(data.summary.models || {})}</code>
                </Kv>
                <Kv label="Claim plan version">{data.summary.claim_plan_version ?? "—"}</Kv>
              </dl>
              <h4>Health</h4>
              <ul className="trace-health">
                {data.health.map((h) => (
                  <li key={h.label}>
                    <span>{h.label}</span>
                    <StatusChip status={h.status} />
                  </li>
                ))}
              </ul>
            </section>
          )}

          {data.why_stopped && showSection(filter, "WHY") && (
            <section className="trace-card trace-why stack" data-testid="why-stopped">
              <h3>Why this case stopped</h3>
              <p>
                Final state: <strong>{data.why_stopped.final_state}</strong>
                {data.why_stopped.outcome ? ` · Outcome: ${data.why_stopped.outcome}` : ""}
              </p>
              {data.why_stopped.blocking_stage && (
                <p>
                  Blocking stage: <StatusChip status={data.why_stopped.blocking_status || "BLOCKED"} />{" "}
                  {data.why_stopped.blocking_stage}
                </p>
              )}
              <ul>
                {(data.why_stopped.reasons || []).map((r, i) => (
                  <li key={i}>{r}</li>
                ))}
              </ul>
              {(data.why_stopped.integrity_errors || []).map((e, i) => (
                <div key={i} className="trace-integrity-fail">
                  <strong>GROUND INTEGRITY FAILURE</strong>
                  <p>{e.message}</p>
                </div>
              ))}
            </section>
          )}

          {(data.grounds.integrity_errors || []).length > 0 && showSection(filter, "GROUNDS") && (
            <section className="trace-integrity-fail" data-testid="ground-integrity-fail">
              <strong>GROUND INTEGRITY FAILURE</strong>
              {(data.grounds.integrity_errors || []).map((e, i) => (
                <p key={i}>{e.message}</p>
              ))}
            </section>
          )}

          {(data.identity_issues || []).length > 0 && (
            <section className="trace-integrity-fail" data-testid="identity-integrity">
              <strong>IDENTITY / PLACEHOLDER ERROR</strong>
              {data.identity_issues.map((e, i) => (
                <p key={i}>{e.message}</p>
              ))}
            </section>
          )}

          {showSection(filter, "PIPELINE") && (
            <Accordion id="pipeline" title="Pipeline" defaultOpen badge={<StatusChip status={pipelineWorst(data.pipeline)} />}>
              <ol className="trace-pipeline">
                {data.pipeline.map((p) => (
                  <li key={p.stage}>
                    <div className="trace-pipeline-row">
                      <StatusChip status={p.status} />
                      <span className="trace-pipeline-name">{p.stage.replace(/_/g, " ")}</span>
                      <span className="trace-pipeline-time">{fmtMs(p.duration_ms)}</span>
                    </div>
                    {p.detail && <p className="trace-muted">{p.detail}</p>}
                  </li>
                ))}
              </ol>
            </Accordion>
          )}

          {showSection(filter, "FACTS") && (
            <Accordion id="extraction" title="Extraction" badge={<span className="trace-count">{data.extraction.length}</span>}>
              <div className="trace-cards">
                {data.extraction.length === 0 && <Empty text="No extraction facts" />}
                {data.extraction.map((f) => (
                  <article key={f.fact_id} className="trace-fact-card">
                    <header>
                      <CopyId id={f.fact_id} label={f.fact_id.slice(0, 10)} />
                      <StatusChip status={f.status} />
                    </header>
                    <h4>{f.name}</h4>
                    <p className="trace-value" data-testid={f.name === "pcn_number" ? "trace-pcn-value" : undefined}>
                      {String(f.value)}
                    </p>
                    <p className="trace-muted">
                      Conf {f.confidence.toFixed(2)} · {f.source.kind} · {f.source.ref || "—"}
                      {f.legal_critical ? " · LEGAL-CRITICAL" : ""}
                      {f.conflict ? " · CONFLICT" : ""}
                    </p>
                  </article>
                ))}
              </div>
            </Accordion>
          )}

          {showSection(filter, "FACTS") && (data.fact_write_trace || []).length > 0 && (
            <Accordion id="fact-write-trace" title="Fact write trace" defaultOpen>
              <div className="stack" data-testid="fact-write-trace">
                {(data.fact_write_trace || []).map((row) => (
                  <article key={row.fact} className="trace-fact-card">
                    <header>
                      <h4>FACT: {row.fact}={String(row.value ?? "")}</h4>
                    </header>
                    <ul className="trace-list">
                      <li>
                        First write: {(row.first_write || {}).authority
                          || (row.first_write || {}).source || "—"}
                      </li>
                      {(row.writes || []).map((w, i) => (
                        <li key={`${row.fact}-w-${i}`}>
                          Later: {w.authority || w.source} → {w.decision}
                          {w.reason ? ` · ${w.reason}` : ""}
                        </li>
                      ))}
                      {row.held_authority && <li>Held authority: {row.held_authority}</li>}
                    </ul>
                  </article>
                ))}
              </div>
            </Accordion>
          )}

          {showSection(filter, "FACTS") && (data.fact_lifecycle || []).length > 0 && (
            <Accordion id="fact-lifecycle" title="Fact lifecycle" defaultOpen>
              <ul className="trace-list" data-testid="fact-lifecycle">
                {(data.fact_lifecycle || []).map((row, i) => (
                  <li key={`fl-${i}`}>
                    {row.event}: {row.name}={String(row.value ?? "")}
                    {row.used_by?.length ? ` · used by ${row.used_by.join(", ")}` : ""}
                    {row.reason ? ` · ${row.reason}` : ""}
                  </li>
                ))}
              </ul>
            </Accordion>
          )}

          {showSection(filter, "FACTS") && (
            <Accordion id="facts" title="Fact graph" badge={<span className="trace-count">{data.facts.length}</span>}>
              <div className="trace-cards">
                {data.facts.map((f) => (
                  <article key={f.fact_id} className="trace-fact-card">
                    <header>
                      <CopyId id={f.fact_id} />
                      <StatusChip status={f.status} />
                    </header>
                    <h4>{f.name}</h4>
                    <p className="trace-value">Value: {String(f.value)}</p>
                    <p className="trace-muted">
                      Source: {f.source.kind} · Usable: {f.usable ? "yes" : "no"}
                    </p>
                    {f.used_by?.length > 0 && (
                      <p className="trace-muted">Used by: {f.used_by.join(" → ")}</p>
                    )}
                  </article>
                ))}
              </div>
            </Accordion>
          )}

          {showSection(filter, "FACTS") && (
            <Accordion id="narrative" title="Narrative / customer facts">
              <h4>Customer facts</h4>
              <div className="trace-cards">
                {(data.narrative.customer_facts || []).length === 0 && (
                  <Empty text="No structured narrative facts" />
                )}
                {(data.narrative.customer_facts || []).map((f) => (
                  <article key={f.name} className="trace-fact-card">
                    <h4>{f.name}</h4>
                    <p className="trace-value">{String(f.value)}</p>
                    <StatusChip status={f.status} />
                  </article>
                ))}
              </div>
              {data.narrative.derived && (
                <>
                  <h4>Derived fact</h4>
                  <article className="trace-fact-card">
                    <h4>{data.narrative.derived.name} = {String(data.narrative.derived.value)}</h4>
                    <p className="trace-muted">
                      DERIVED_FROM: {(data.narrative.derived.derived_from || []).join(", ") || "—"}
                    </p>
                  </article>
                </>
              )}
              {data.narrative.raw_available && (
                <div className="stack" style={{ marginTop: 12 }}>
                  <button
                    type="button"
                    className="btn btn-secondary trace-btn-full"
                    onClick={async () => {
                      const next = !showRaw;
                      setShowRaw(next);
                      if (next) await load(true);
                    }}
                  >
                    {showRaw ? "Hide redacted source" : "Show redacted source"}
                  </button>
                  {showRaw && data.narrative.raw_text && (
                    <pre className="trace-code" data-testid="raw-narrative">
                      {(data.narrative.raw_text || []).join("\n")}
                    </pre>
                  )}
                </div>
              )}
            </Accordion>
          )}

          {showSection(filter, "FACTS") && (
            <Accordion id="allegations" title="Allegations">
              <Kv label="Raw allegation">{data.allegations.raw || "—"}</Kv>
              <Kv label="Canonical">{data.allegations.canonical}</Kv>
              <h4>Propositions</h4>
              <ul className="trace-list">
                {(data.allegations.propositions || []).map((p) => (
                  <li key={p.id}>
                    <code>{p.id}</code> {p.name} = {String(p.value)}
                  </li>
                ))}
              </ul>
            </Accordion>
          )}

          {showSection(filter, "RELATIONSHIPS") && (
            <Accordion id="relationships" title="Relationships">
              <ul className="trace-rel-list">
                {(data.relationships || []).length === 0 && <Empty />}
                {(data.relationships || []).map((r, i) => (
                  <li key={i}>
                    <code>{r.from}</code>
                    <span className="trace-rel-type">{r.type}</span>
                    <code>{r.to}</code>
                    {r.reason && <span className="trace-muted"> — {r.reason}</span>}
                  </li>
                ))}
              </ul>
              <button
                type="button"
                className="btn btn-secondary trace-btn-full"
                onClick={() => setGraphOpen((v) => !v)}
              >
                {graphOpen ? "Hide graph view" : "Open graph view"}
              </button>
              {graphOpen && (
                <pre className="trace-code trace-graph-desktop">
                  {(data.relationships || [])
                    .map((r) => `${r.from} --${r.type}--> ${r.to}`)
                    .join("\n") || "(empty)"}
                </pre>
              )}
            </Accordion>
          )}

          {showSection(filter, "FINDINGS") && (
            <Accordion id="findings" title="Legal calculations / findings">
              {(data.legal_findings || []).length === 0 && <Empty text="No legal findings" />}
              {(data.legal_findings || []).map((f, i) => (
                <article key={i} className="trace-fact-card">
                  <header>
                    <h4>{f.family || f.finding_id}</h4>
                    <StatusChip status={f.status || "UNRESOLVED"} />
                  </header>
                  <p className="trace-muted">Module: {f.legal_module_id || "—"}</p>
                  {f.reason && <p>{f.reason}</p>}
                  <JsonBlock value={{ particulars: f.particulars, calculation: f.calculation }} />
                </article>
              ))}
            </Accordion>
          )}

          {showSection(filter, "GROUNDS") && (data.module_journey || []).length > 0 && (
            <Accordion id="module-journey" title="Module journey" defaultOpen>
              <div className="stack" data-testid="module-journey">
                {(data.module_journey || []).map((row) => (
                  <article key={row.module_id} className="trace-fact-card" data-testid="module-journey-row">
                    <header>
                      <CopyId id={row.module_id} />
                      <StatusChip status={row.integrity || "PASS"} />
                    </header>
                    <ul className="trace-list">
                      <li>Knowledge Matcher: {(row.knowledge || {}).decision || "—"}</li>
                      {(row.required_facts || []).length > 0 && (
                        <li>Required facts: {(row.required_facts || []).join(", ")}</li>
                      )}
                      {(row.missing_facts || []).length > 0 && (
                        <li>Missing: {(row.missing_facts || []).join(", ")}</li>
                      )}
                      <li>Case Intelligence: {(row.case_intelligence || {}).decision || "—"}</li>
                      <li>Claim Plan: {(row.claim_plan || {}).decision || (row.claim_plan || {}).status || "—"}</li>
                      <li>Draft: {(row.draft || {}).decision || "—"}</li>
                    </ul>
                    {((row.claim_plan || {}).reason
                      || (row.case_intelligence || {}).reason
                      || (row.applicability || {}).reason) && (
                      <p className="trace-muted">
                        Reason: {(row.claim_plan || {}).reason
                          || (row.case_intelligence || {}).reason
                          || (row.applicability || {}).reason}
                      </p>
                    )}
                    {row.expected_rejection && (
                      <p className="trace-muted">Expected rejection · Integrity: PASS</p>
                    )}
                    {row.integrity_message && row.integrity === "FAIL" && (
                      <p className="trace-error">{row.integrity_message}</p>
                    )}
                  </article>
                ))}
              </div>
            </Accordion>
          )}

          {showSection(filter, "GROUNDS") && (
            <Accordion id="knowledge" title="Knowledge modules">
              {Object.entries(data.knowledge || {}).map(([bucket, rows]) => (
                <div key={bucket} className="stack" style={{ marginBottom: 12 }}>
                  <h4>{bucket} ({rows.length})</h4>
                  {rows.length === 0 && <Empty />}
                  {rows.map((r, i) => (
                    <article key={`${bucket}-${i}`} className="trace-fact-card">
                      <header>
                        <CopyId id={r.module_id || "?"} />
                        <StatusChip status={r.final_decision || bucket} />
                      </header>
                      <p className="trace-muted">{r.reason || "—"}</p>
                      <p className="trace-muted">
                        In claim plan: {r.in_claim_plan == null ? "—" : r.in_claim_plan ? "YES" : "NO"}
                      </p>
                      {r.blocking_condition && (
                        <p className="trace-error">Blocking: {r.blocking_condition}</p>
                      )}
                    </article>
                  ))}
                </div>
              ))}
            </Accordion>
          )}

          {showSection(filter, "GROUNDS") && data.document_baseline && (
            <Accordion id="document-baseline" title="Document baseline" defaultOpen>
              <div className="stack" data-testid="document-baseline">
                <h4>DOCUMENT BASELINE</h4>
                <ul className="trace-list">
                  {(data.document_baseline.document_finding_types
                    || data.document_baseline.document_grounds || []).map((t) => (
                    <li key={`db-${t}`}>✓ {t}</li>
                  ))}
                </ul>
                <h4>CUSTOMER DELTA</h4>
                <ul className="trace-list">
                  {(data.document_baseline.customer_delta || []).map((m) => (
                    <li key={`delta-${m}`}>+ {m}</li>
                  ))}
                  {(data.document_baseline.customer_delta || []).length === 0 && <li>(none)</li>}
                </ul>
                <h4>FINAL</h4>
                <ul className="trace-list">
                  {(data.document_baseline.final || []).map((m) => (
                    <li key={`db-final-${m}`}>✓ {m}</li>
                  ))}
                </ul>
              </div>
            </Accordion>
          )}

          {showSection(filter, "GROUNDS") && data.ground_sources && (
            <Accordion id="ground-sources" title="Ground sources" defaultOpen>
              <div className="stack" data-testid="ground-sources">
                <h4>Verified findings</h4>
                <ul className="trace-list">
                  {(data.ground_sources.verified_findings || []).map((f, i) => (
                    <li key={`vf-${i}`}>✓ {f.finding_type || f.module_id}</li>
                  ))}
                  {(data.ground_sources.verified_findings || []).length === 0 && <li>none</li>}
                </ul>
                <h4>Case Intelligence</h4>
                <ul className="trace-list">
                  {(data.ground_sources.case_intelligence?.not_selected || []).map((mid) => (
                    <li key={`ci-omit-${mid}`}>not selected {mid}</li>
                  ))}
                  {(data.ground_sources.case_intelligence?.not_selected || []).length === 0 && (
                    <li>
                      {(data.ground_sources.case_intelligence?.selected || []).join(", ") || "none"}
                    </li>
                  )}
                </ul>
                <h4>Final Claim Plan</h4>
                <ul className="trace-list">
                  {(data.ground_sources.final_claim_plan || []).map((mid) => (
                    <li key={`final-${mid}`}>✓ {mid}</li>
                  ))}
                </ul>
                {(data.ground_sources.overrides || []).map((o, i) => (
                  <p key={`ov-${i}`} className="trace-muted">
                    Reason: {o.reason}
                  </p>
                ))}
              </div>
            </Accordion>
          )}

          {showSection(filter, "GROUNDS") && (
            <Accordion id="grounds" title="Ground sets" defaultOpen badge={
              (data.grounds.integrity_errors || []).length
                ? <StatusChip status="FAIL" />
                : <StatusChip status="PASS" />
            }>
              <GroundBucket title="Independent notice grounds" rows={data.grounds.independent_notice} />
              <GroundBucket title="Narrative grounds" rows={data.grounds.narrative} />
              <GroundBucket title="Evidence grounds" rows={data.grounds.evidence} />
              <GroundBucket title="Verified-finding grounds" rows={data.grounds.verified_finding} />
              <GroundBucket title="Carried-forward grounds" rows={data.grounds.carried_forward} />
              <GroundBucket title="Invalidated grounds" rows={data.grounds.invalidated} empty="none" />
              <GroundBucket title="Final merged grounds" rows={data.grounds.final_merged} />
            </Accordion>
          )}

          {showSection(filter, "CLAIM") && (
            <Accordion id="claim-plan" title="Claim plan" defaultOpen>
              {!data.claim_plan && <Empty text="No locked claim plan" />}
              {data.claim_plan && (
                <>
                  <p className="trace-muted">
                    v{data.claim_plan.version} · {data.claim_plan.status} ·{" "}
                    <CopyId id={data.claim_plan.claim_plan_id} />
                  </p>
                  {(data.claim_plan.items || []).map((item) => (
                    <article key={item.item_id} className="trace-fact-card" data-testid="claim-plan-item">
                      <header>
                        <h4>{item.module_id}</h4>
                        <StatusChip status={item.status} />
                      </header>
                      <p className="trace-muted">
                        Origin: {item.origin || item.decision} · Priority: {item.priority ?? "—"}
                      </p>
                      <p>{item.reason}</p>
                      <h5>Support bundle</h5>
                      <ul className="trace-list">
                        <li>Allegation: {(item.support_bundle.allegation_refs || []).join(", ") || "—"}</li>
                        <li>
                          Supporting facts:{" "}
                          {(item.support_bundle.supporting_facts || [])
                            .map((f) => String(f.fact || f.condition))
                            .join(", ") || "—"}
                        </li>
                        <li>Derived: {(item.support_bundle.derived_facts || []).join(", ") || "—"}</li>
                        <li>Bundle: {item.support_bundle.complete ? "complete" : "incomplete"}</li>
                        <li>Evidence: {(item.support_bundle.evidence || []).length}</li>
                      </ul>
                      <h5>Draft requirement</h5>
                      <ul className="trace-list">
                        <li>Licence: {item.draft_requirement.legal_licence}</li>
                        <li>Must express: {(item.draft_requirement.must_express || []).join(", ") || "—"}</li>
                        <li>Must not: {(item.draft_requirement.must_not_express || []).join(", ") || "—"}</li>
                        <li>Goal: {(item.draft_requirement.explanation_goal || []).join("; ") || "—"}</li>
                      </ul>
                    </article>
                  ))}
                </>
              )}
            </Accordion>
          )}

          {showSection(filter, "DRAFT") && (
            <Accordion id="draft-context" title="Draft context">
              <Kv label="PCN">{data.draft_context.pcn_number ?? "—"}</Kv>
              <Kv label="VRM">{data.draft_context.vrm ?? "—"}</Kv>
              <Kv label="Operator">{data.draft_context.operator ?? "—"}</Kv>
              <Kv label="Driver">{data.draft_context.driver_status ?? "—"}</Kv>
              <Kv label="Approved grounds">
                {(data.draft_context.approved_grounds || []).join(", ") || "—"}
              </Kv>
              <h4>Verified facts in context</h4>
              <ul className="trace-list">
                {Object.entries(data.draft_context.verified_facts || {}).map(([k, v]) => (
                  <li key={k}><code>{k}</code> = {String(v)}</li>
                ))}
              </ul>
              {(data.draft_context.lossy_boundaries || []).map((L, i) => (
                <div key={i} className="trace-integrity-fail" data-testid="draft-context-loss">
                  <strong>ERROR</strong>
                  <p>{L.message}</p>
                </div>
              ))}
            </Accordion>
          )}

          {showSection(filter, "DRAFT") && (
            <Accordion id="draft" title="Generated draft">
              {!data.draft && <Empty text="Draft not run" />}
              {(data.draft?.paragraphs || []).map((p) => (
                <article key={p.index} className="trace-fact-card">
                  <p>{p.text}</p>
                  <p className="trace-muted">
                    Ground: {p.ground || "—"} · Facts: {(p.fact_refs || []).join(" ") || "—"}
                  </p>
                </article>
              ))}
            </Accordion>
          )}

          {showSection(filter, "VALIDATION") && (
            <Accordion id="validation" title="Validation">
              <ul className="trace-health">
                {(data.validation.checks || []).map((c, i) => (
                  <li key={`${c.rule}-${i}`}>
                    <span>
                      <code>{c.rule}</code>
                      {c.reason ? ` — ${c.reason}` : ""}
                    </span>
                    <StatusChip status={c.status} />
                  </li>
                ))}
              </ul>
            </Accordion>
          )}

          {showSection(filter, "LOGS") && (
            <Accordion id="events" title="Event log">
              <div className="trace-terminal" data-testid="event-log">
                {(data.events || []).length === 0 && <Empty text="No events" />}
                {(data.events || []).map((e, i) => (
                  <div key={i} className="trace-log-row">
                    <span className="trace-log-time">{(e.at || "").slice(11, 19) || "—"}</span>
                    <span className="trace-log-event">{e.event}</span>
                    <span className="trace-log-sum">{e.summary}</span>
                  </div>
                ))}
              </div>
            </Accordion>
          )}

          {showSection(filter, "COMPARE") && (
            <Accordion id="compare" title="Compare runs">
              {!compare && <Empty text="No prior run to compare" />}
              {compare && !compare.error && (
                <div className="trace-compare" data-testid="run-compare">
                  <div className="trace-compare-col">
                    <h4>{compare.a.label}</h4>
                    <ul className="trace-list">
                      {(compare.a.grounds || []).map((g) => (
                        <li key={g}>✓ {g}</li>
                      ))}
                      {(compare.a.grounds || []).length === 0 && <li>— none —</li>}
                    </ul>
                  </div>
                  <div className="trace-compare-col">
                    <h4>{compare.b.label}</h4>
                    <ul className="trace-list">
                      {(compare.b.grounds || []).map((g) => (
                        <li key={g}>✓ {g}</li>
                      ))}
                      {(compare.b.grounds || []).length === 0 && <li>— none —</li>}
                    </ul>
                  </div>
                  <div className="trace-compare-change">
                    <h4>Change</h4>
                    <ul className="trace-list">
                      {(compare.modules?.added || []).map((g) => (
                        <li key={`m+${g}`}>+ module {g}</li>
                      ))}
                      {(compare.modules?.removed || []).map((g) => (
                        <li key={`m-${g}`}>− module {g}</li>
                      ))}
                      {(compare.grounds.added || []).map((g) => (
                        <li key={`+${g}`}>+ {g}</li>
                      ))}
                      {(compare.grounds.removed || []).map((g) => (
                        <li key={`-${g}`} className="trace-error">− {g}</li>
                      ))}
                      {(compare.grounds.added || []).length === 0
                        && (compare.grounds.removed || []).length === 0
                        && <li>No ground changes</li>}
                    </ul>
                    <h4>Facts added</h4>
                    <ul className="trace-list">
                      {(compare.facts.added || []).map((f) => (
                        <li key={f.name}>+ {f.name}={String(f.value)}</li>
                      ))}
                      {(compare.facts.changed || []).map((f) => (
                        <li key={`chg-${f.name}`}>~ {f.name}: {String(f.from)} → {String(f.to)}</li>
                      ))}
                      {(compare.facts.added || []).length === 0
                        && (compare.facts.changed || []).length === 0 && <li>none</li>}
                    </ul>
                    <h4>Findings</h4>
                    <ul className="trace-list">
                      {(compare.findings?.added || []).map((f) => (
                        <li key={`f+${f}`}>+ {f}</li>
                      ))}
                      {(compare.findings?.removed || []).map((f) => (
                        <li key={`f-${f}`}>− {f}</li>
                      ))}
                      {(compare.findings?.added || []).length === 0
                        && (compare.findings?.removed || []).length === 0
                        && <li>unchanged</li>}
                    </ul>
                    {(compare.removals || []).map((r, i) => (
                      <p key={`rm-${i}`} className="trace-muted">
                        {r.module_id}: {r.explain || r.reason || r.kind}
                      </p>
                    ))}
                    {(compare.integrity_errors || []).map((e, i) => (
                      <div key={i} className="trace-integrity-fail">
                        <strong>INTEGRITY ERROR</strong>
                        <p>{e.message}</p>
                      </div>
                    ))}
                  </div>
                </div>
              )}
            </Accordion>
          )}
        </div>
      )}
    </div>
  );
}

function GroundBucket({
  title,
  rows,
  empty = "none",
}: {
  title: string;
  rows: { module_id: string; decision?: string; status?: string }[];
  empty?: string;
}) {
  return (
    <div className="stack" style={{ marginBottom: 14 }}>
      <h4>{title}</h4>
      {(!rows || rows.length === 0) && <Empty text={empty} />}
      <ul className="trace-list">
        {(rows || []).map((r) => (
          <li key={`${title}-${r.module_id}`}>
            ✓ {r.module_id}
            {r.decision ? ` (${r.decision})` : ""}
          </li>
        ))}
      </ul>
    </div>
  );
}

function pipelineWorst(pipeline: { status: TraceStatus }[]): TraceStatus {
  const order = ["FAIL", "BLOCKED", "WARNING", "UNRESOLVED", "NOT_RUN", "PASS"];
  for (const s of order) {
    if (pipeline.some((p) => String(p.status).toUpperCase() === s)) return s;
  }
  return "PASS";
}
