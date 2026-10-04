/**
 * Admin/test Case Trace client. Only talks to /api/admin/* — never the
 * customer proxy. Trace data is fetched only when the caller opts in.
 */

export type TraceStatus =
  | "PASS"
  | "FAIL"
  | "WARNING"
  | "WARN"
  | "BLOCKED"
  | "UNRESOLVED"
  | "NOT_RUN"
  | "VERIFIED"
  | string;

export type CaseConsole = {
  schema: string;
  summary: {
    case_id: string;
    execution_id?: string;
    case_revision?: number;
    final_outcome?: string;
    final_state?: string;
    app_version?: string;
    git_commit?: string;
    kb_release?: string;
    prompt_versions?: Record<string, unknown>;
    models?: Record<string, unknown>;
    provider?: string;
    claim_plan_version?: number | null;
    synthetic_label?: string | null;
  };
  health: { label: string; status: TraceStatus }[];
  pipeline: {
    stage: string;
    status: TraceStatus;
    duration_ms?: number | null;
    detail?: string | null;
  }[];
  extraction: FactRow[];
  facts: FactRow[];
  fact_lifecycle?: {
    event: string;
    name?: string;
    value?: unknown;
    reason?: string;
    used_by?: string[];
    supersedes?: string | null;
  }[];
  fact_write_trace?: {
    fact: string;
    value?: unknown;
    first_write?: { authority?: string | null; source?: string; status?: string; at?: string } | null;
    writes?: { authority?: string; source?: string; decision?: string; reason?: string; at?: string }[];
    held_authority?: string | null;
    provenance_count?: number;
  }[];
  narrative: {
    customer_facts: { name: string; value: unknown; status: string; fact_id?: string }[];
    derived?: {
      name: string;
      value: unknown;
      status: string;
      derived_from: string[];
      fact_id?: string;
    } | null;
    hypotheses: Record<string, unknown>[];
    raw_available: boolean;
    raw_text?: string[] | null;
  };
  allegations: {
    raw?: string | null;
    canonical: string;
    propositions: { id: string; name: string; value: unknown; kind?: string }[];
  };
  relationships: {
    from?: string;
    to?: string;
    type?: string;
    reason?: string;
    from_id?: string | null;
    to_id?: string | null;
  }[];
  legal_findings: {
    finding_id?: string;
    family?: string;
    status?: string;
    legal_module_id?: string;
    particulars?: Record<string, unknown>;
    calculation?: Record<string, unknown>;
    reason?: string;
  }[];
  knowledge: Record<string, KnowledgeRow[]>;
  module_journey?: ModuleJourneyRow[];
  module_decisions?: ModuleDecisionRow[];
  module_trace_checks?: { rule: string; status: TraceStatus; module_id?: string; message?: string }[];
  grounds: {
    independent_notice: GroundRow[];
    narrative: GroundRow[];
    evidence: GroundRow[];
    verified_finding: GroundRow[];
    carried_forward: GroundRow[];
    invalidated: GroundRow[];
    final_merged: GroundRow[];
    verified_families?: string[];
    integrity_errors: IntegrityError[];
  };
  ground_sources?: {
    verified_findings: { finding_type?: string; module_id?: string; status?: string }[];
    case_intelligence: { selected: string[]; not_selected: string[] };
    final_claim_plan: string[];
    overrides: { module_id: string; reason: string }[];
    source_trace?: string[];
    invalidations?: Record<string, unknown>[];
  };
  document_baseline?: {
    created?: string[];
    version?: number | null;
    digest?: string | null;
    legal_findings?: { type?: string; status?: string }[];
    document_grounds?: string[];
    document_finding_types?: string[];
    customer_delta?: string[];
    final?: string[];
    analysis_version?: number | null;
    trace?: string[];
  };
  claim_plan: ClaimPlanView | null;
  draft_context: {
    approved_grounds: string[];
    verified_facts: Record<string, unknown>;
    fact_basis?: Record<string, string>;
    verified_findings: Record<string, unknown>[];
    evidence_refs: string[];
    driver_status?: string;
    operator?: string | null;
    pcn_number?: string | null;
    vrm?: string | null;
    lossy_boundaries: IntegrityError[];
    draft_ran?: boolean;
  };
  draft: {
    letter_present: boolean;
    paragraphs: {
      index: number;
      text: string;
      ground?: string | null;
      module_refs: string[];
      fact_refs: string[];
      evidence_refs: string[];
    }[];
  } | null;
  validation: {
    checks: { rule: string; status: TraceStatus; reason?: string }[];
    ran: boolean;
  };
  why_stopped: {
    final_state?: string;
    outcome?: string;
    blocking_stage?: string;
    blocking_status?: string;
    reasons: string[];
    integrity_errors?: IntegrityError[];
  } | null;
  events: { at?: string; event: string; summary?: string; run_id?: number }[];
  identity_issues: IntegrityError[];
  integrity?: { passed?: boolean; checks?: { check?: string; status?: string; detail?: string }[] };
  plan_versions?: { version: number; status: string; approved: string[]; run_number?: number }[];
};

export type FactRow = {
  fact_id: string;
  name: string;
  value: unknown;
  status: string;
  source: { kind: string; ref: string };
  confidence: number;
  usable: boolean;
  legal_critical?: boolean;
  conflict?: boolean;
  used_by: string[];
  provenance?: string;
  value_redacted?: boolean;
};

export type GroundRow = {
  module_id: string;
  status?: string;
  decision?: string;
  reason?: string;
  label?: string;
};

export type ModuleDecisionRow = {
  module_id: string;
  case_id?: string;
  stage: string;
  decision: string;
  reason?: string;
  supporting_fact_ids?: string[];
  missing_fact_ids?: string[];
  blocking_conditions?: string[];
  timestamp?: string;
  created_by?: string;
};

export type ModuleStageView = {
  decision?: string;
  reason?: string;
  supporting_fact_ids?: string[];
  missing_fact_ids?: string[];
  blocking_conditions?: string[];
  status?: string | null;
  origin?: string | null;
};

export type ModuleJourneyRow = {
  module_id: string;
  knowledge?: ModuleStageView;
  applicability?: ModuleStageView;
  case_intelligence?: ModuleStageView;
  claim_plan?: ModuleStageView;
  draft?: ModuleStageView;
  required_facts?: string[];
  missing_facts?: string[];
  expected_rejection?: boolean;
  integrity?: TraceStatus;
  integrity_message?: string;
  history?: ModuleDecisionRow[];
};

export type KnowledgeRow = {
  module_id?: string;
  reason?: string;
  facts_available?: string[];
  missing_facts?: string[];
  blocking_condition?: string | null;
  final_decision?: string;
  in_claim_plan?: boolean | null;
};

export type IntegrityError = {
  code?: string;
  message?: string;
  fact?: string;
  ground?: string;
  field?: string;
  value?: unknown;
  extracted?: unknown;
};

export type ClaimPlanView = {
  claim_plan_id: string;
  version: number;
  status: string;
  plan_digest?: string;
  approved: string[];
  items: {
    item_id: string;
    module_id: string;
    claim_type?: string;
    status: string;
    decision?: string;
    origin?: string;
    priority?: number | null;
    reason?: string;
    topic?: string;
    support_bundle: {
      allegation_refs: string[];
      supporting_facts: Record<string, unknown>[];
      derived_facts: string[];
      source_fact_ids?: string[];
      derived_fact_ids?: string[];
      calculated_facts: string[];
      verified_findings: string[];
      evidence: Record<string, unknown>[];
      relationship_ids: (string | undefined)[];
      complete?: boolean;
      draft_requirement: {
        must_express: string[];
        must_not_express: string[];
        explanation_goal?: string[];
        legal_licence?: string;
      };
    };
    draft_requirement: {
      must_express: string[];
      must_not_express: string[];
      explanation_goal?: string[];
      legal_licence?: string;
    };
  }[];
  material_fact_accounting?: Record<string, unknown>[];
  trace?: string[];
};

export type RemovalExplain = {
  module_id: string;
  kind?: string;
  reason?: string;
  explain?: string;
};

export type RunCompare = {
  a: { label: string; version: number; run_number?: number; grounds: string[]; modules?: string[]; findings: string[] };
  b: { label: string; version: number; run_number?: number; grounds: string[]; modules?: string[]; findings: string[] };
  modules?: { added: string[]; removed: string[]; unchanged: string[] };
  grounds: { added: string[]; removed: string[]; unchanged: string[] };
  facts: {
    added: { name: string; value: unknown }[];
    removed: unknown[] | { name: string; value: unknown }[];
    changed?: { name: string; from?: unknown; to?: unknown }[];
  };
  findings?: { added: string[]; removed: string[]; unchanged: string[] };
  legal_findings: { a: string[]; b: string[] };
  removals?: RemovalExplain[];
  explained?: boolean;
  integrity_errors: IntegrityError[];
  error?: string;
};

export async function getTraceSession(): Promise<{ authorised: boolean; token_configured: boolean }> {
  const res = await fetch("/api/admin/session", { cache: "no-store" });
  if (!res.ok) return { authorised: false, token_configured: false };
  return res.json();
}

export async function unlockTrace(token: string): Promise<boolean> {
  const res = await fetch("/api/admin/session", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ token }),
  });
  return res.ok;
}

export async function lockTrace(): Promise<void> {
  await fetch("/api/admin/session", { method: "DELETE" });
}

export async function fetchCaseConsole(caseId: string, raw = false): Promise<CaseConsole> {
  const q = raw ? "?raw=1" : "";
  const res = await fetch(`/api/admin/cases/${encodeURIComponent(caseId)}/console${q}`, {
    cache: "no-store",
  });
  if (!res.ok) {
    const detail = await res.json().catch(() => ({}));
    throw new Error(detail?.detail || `Trace unavailable (${res.status})`);
  }
  return res.json();
}

export async function fetchRunCompare(caseId: string): Promise<RunCompare> {
  const res = await fetch(`/api/admin/cases/${encodeURIComponent(caseId)}/console/compare`, {
    cache: "no-store",
  });
  if (!res.ok) throw new Error(`Compare unavailable (${res.status})`);
  return res.json();
}

export async function fetchCopyReport(caseId: string): Promise<string> {
  const res = await fetch(`/api/admin/cases/${encodeURIComponent(caseId)}/console/report.txt`, {
    cache: "no-store",
  });
  if (!res.ok) throw new Error(`Report unavailable (${res.status})`);
  return res.text();
}

/** True when the URL asks for trace AND we should attempt the gate. */
export function wantsTrace(search: string): boolean {
  const q = new URLSearchParams(search.startsWith("?") ? search.slice(1) : search);
  return q.get("trace") === "1" || q.get("trace") === "true";
}
