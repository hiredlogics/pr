"use client";

import { useCallback, useEffect, useState } from "react";
import DataTable from "@/components/admin/DataTable";
import JsonBlock from "@/components/admin/JsonBlock";
import RefreshControls from "@/components/admin/RefreshControls";
import { dbCase, dbCases } from "@/lib/admin_db";

export default function AdminCasesPage() {
  const [list, setList] = useState<any>(null);
  const [detail, setDetail] = useState<any>(null);
  const [caseId, setCaseId] = useState("");
  const [showSensitive, setShowSensitive] = useState(false);
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);

  const loadList = useCallback(async () => {
    setBusy(true);
    setErr("");
    try {
      setList(await dbCases(showSensitive));
    } catch (e: any) {
      setErr(String(e?.message || e));
    } finally {
      setBusy(false);
    }
  }, [showSensitive]);

  useEffect(() => {
    loadList();
  }, [loadList]);

  async function openCase(id: string) {
    setCaseId(id);
    setBusy(true);
    setErr("");
    try {
      setDetail(await dbCase(id, showSensitive));
    } catch (e: any) {
      setErr(String(e?.message || e));
      setDetail(null);
    } finally {
      setBusy(false);
    }
  }

  const cases = list?.cases || [];
  const cols = cases[0] ? Object.keys(cases[0]) : ["case_id", "state"];

  return (
    <div className="admin-stack">
      <div className="admin-row">
        <h2>Cases</h2>
        <RefreshControls onRefresh={loadList} busy={busy} />
      </div>
      <label className="admin-check">
        <input
          type="checkbox"
          checked={showSensitive}
          onChange={(e) => setShowSensitive(e.target.checked)}
        />
        Show sensitive data
      </label>
      {err && <p className="admin-error">{err}</p>}

      <section className="admin-card">
        <h3>Latest cases</h3>
        <DataTable columns={cols} rows={cases} />
        <div className="admin-row" style={{ marginTop: 12, flexWrap: "wrap", gap: 8 }}>
          {cases.map((c: any) => (
            <button
              key={c.case_id}
              type="button"
              className="btn btn-secondary"
              onClick={() => openCase(String(c.case_id))}
            >
              {String(c.case_id).slice(0, 8)}… ({c.state})
            </button>
          ))}
        </div>
      </section>

      {detail && (
        <section className="admin-card admin-stack">
          <h3>Case pipeline — {detail.case_id}</h3>
          <p className="admin-muted">{(detail.pipeline || []).join(" → ")}</p>

          {detail.release_trace && (
            <div className="admin-card">
              <h4>Release Trace</h4>
              {detail.legacy_class?.class && (
                <p className="admin-muted">
                  Classification: {detail.legacy_class.class}
                  {detail.legacy_class.note
                    ? " — " + String(detail.legacy_class.note)
                    : ""}
                </p>
              )}
              <ul style={{ listStyle: "none", padding: 0, margin: "8px 0" }}>
                {(
                  [
                    ["Code version", "code_version"],
                    ["KB release", "kb_release"],
                    ["Ontology", "ontology"],
                    ["Module roles", "module_roles"],
                    ["Facts", "facts"],
                    ["Legal findings", "legal_findings"],
                    ["Claim Plan", "claim_plan"],
                    ["DraftPlan", "draft_plan"],
                    ["Draft", "draft"],
                    ["Validation", "validation"],
                    ["Final outcome", "final_outcome"],
                  ] as const
                ).map(([label, key]) => {
                  const ok = Boolean(detail.release_trace?.checks?.[key]);
                  const released = detail.case?.state === "RELEASED";
                  const labelOut =
                    key === "final_outcome" && released
                      ? "RELEASED"
                      : ok
                        ? "PASS"
                        : "FAIL";
                  return (
                    <li
                      key={key}
                      style={{
                        display: "flex",
                        justifyContent: "space-between",
                        padding: "4px 0",
                        color: ok ? "inherit" : "var(--danger, #b00020)",
                        fontWeight: ok ? 400 : 600,
                      }}
                    >
                      <span>{label}</span>
                      <span>{labelOut}</span>
                    </li>
                  );
                })}
              </ul>
              {(detail.release_trace.missing || []).length > 0 && (
                <p className="admin-error">
                  Missing links: {(detail.release_trace.missing || []).join(", ")}
                </p>
              )}
              {(detail.release_invariants || []).length > 0 && (
                <p className="admin-error">
                  Invariant failures: {(detail.release_invariants || []).join(", ")}
                </p>
              )}
            </div>
          )}

          {(
            [
              ["CASE", detail.case],
              ["DOCUMENT BASELINE", detail.document_baselines],
              ["NOTICE FACTS", detail.notice_facts],
              ["CUSTOMER FACTS", detail.customer_facts],
              ["FACT SOURCES", detail.fact_sources],
              ["DERIVED FACTS", detail.derived_facts],
              ["FACT LINEAGE", detail.fact_lineage],
              ["LEGAL FINDINGS", detail.legal_findings],
              ["GROUND DECISIONS", detail.ground_decisions],
              ["CLAIM PLAN", detail.claim_plans],
              ["CLAIM PLAN ITEMS", detail.claim_plan_items],
              ["DRAFT PLAN", detail.draft_plan],
              ["DRAFT VERSION", detail.drafts],
              ["VALIDATION", detail.validations],
              ["FINAL OUTCOME", detail.final_outcome],
            ] as const
          ).map(([title, data]) => (
            <div key={title}>
              <h4>{title}</h4>
              <JsonBlock value={data} />
            </div>
          ))}

          <h4>Fact graph</h4>
          <DataTable
            columns={
              detail.all_facts?.[0]
                ? Object.keys(detail.all_facts[0])
                : ["fact_id", "name", "value", "status"]
            }
            rows={detail.all_facts || []}
          />
        </section>
      )}

      {!detail && caseId && <p className="admin-muted">Loading {caseId}…</p>}
    </div>
  );
}
