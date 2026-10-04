/** @vitest-environment jsdom */
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import CaseTraceConsole from "./CaseTraceConsole";
import TraceGate from "./TraceGate";
import ResultStep from "../ResultStep";
import type { CaseConsole } from "@/lib/trace";
import type { AppealResponse } from "@/lib/types";

const mockConsole: CaseConsole = {
  schema: "case_console.v1",
  summary: {
    case_id: "C-TEST",
    execution_id: "exec-abc",
    case_revision: 4,
    final_outcome: "NO_SUPPORTED_GROUNDS",
    final_state: "MANUAL_REVIEW",
    app_version: "dev",
    git_commit: "abc123",
    kb_release: "kb-1",
    prompt_versions: { drafting: 16 },
    models: { drafting: "demo" },
    provider: "demo",
    claim_plan_version: 2,
    synthetic_label: "TEST CASE / SYNTHETIC DATA",
  },
  health: [
    { label: "Extraction", status: "PASS" },
    { label: "Ground merge", status: "FAIL" },
    { label: "Claim Plan", status: "BLOCKED" },
  ],
  pipeline: [
    { stage: "UPLOAD", status: "PASS", duration_ms: 10 },
    { stage: "CLASSIFICATION", status: "PASS", duration_ms: 20 },
    { stage: "EXTRACTION", status: "PASS", duration_ms: 30 },
    { stage: "LEGAL_CRITICAL_FACT_GATE", status: "PASS", duration_ms: 5 },
    { stage: "FACT_GRAPH", status: "PASS", duration_ms: 5 },
    { stage: "NARRATIVE_HYPOTHESES", status: "PASS", duration_ms: 5 },
    { stage: "ALLEGATION_PROPOSITIONS", status: "PASS", duration_ms: 5 },
    { stage: "RELATIONSHIPS", status: "PASS", duration_ms: 5 },
    { stage: "LEGAL_CALCULATIONS", status: "PASS", duration_ms: 5 },
    { stage: "VERIFIED_FINDINGS", status: "PASS", duration_ms: 5 },
    { stage: "CASE_INTELLIGENCE", status: "PASS", duration_ms: 5 },
    { stage: "GROUND_MERGE", status: "FAIL", duration_ms: 5, detail: "ground lost" },
    { stage: "CLAIM_PLAN", status: "BLOCKED", duration_ms: 5 },
    { stage: "DRAFT_REQUIREMENTS", status: "NOT_RUN", duration_ms: null },
    { stage: "DRAFT", status: "NOT_RUN", duration_ms: null },
    { stage: "VALIDATION", status: "NOT_RUN", duration_ms: null },
    { stage: "OUTCOME", status: "BLOCKED", duration_ms: 5 },
  ],
  extraction: [
    {
      fact_id: "F-pcn",
      name: "pcn_number",
      value: "88812000363",
      status: "CONFIRMED",
      source: { kind: "DOCUMENT", ref: "page1" },
      confidence: 0.99,
      usable: true,
      legal_critical: true,
      used_by: [],
    },
  ],
  facts: [],
  narrative: {
    customer_facts: [{ name: "left_site", value: true, status: "ASSERTED" }],
    derived: {
      name: "multiple_visits",
      value: true,
      status: "ANSWERED",
      derived_from: ["left_site", "returned_same_day"],
    },
    hypotheses: [],
    raw_available: true,
    raw_text: null,
  },
  allegations: {
    raw: "Maximum stay exceeded",
    canonical: "OVERSTAY",
    propositions: [{ id: "A-01", name: "continuous_single_visit", value: true }],
  },
  relationships: [
    { from: "left_site", to: "multiple_visits", type: "DERIVED_FROM" },
  ],
  legal_findings: [
    { family: "POFA_POSTAL_LATE", status: "VERIFIED", legal_module_id: "KB-POFA-02" },
  ],
  knowledge: {
    SUPPORTED: [{ module_id: "KB-POFA-02", final_decision: "SUPPORTED", in_claim_plan: true }],
    RELEVANT: [],
    REJECTED: [],
    BLOCKED: [],
    UNRESOLVED: [],
  },
  module_journey: [
    {
      module_id: "KB-ANPR-01",
      knowledge: { decision: "MATCHED" },
      case_intelligence: { decision: "SELECTED" },
      claim_plan: { decision: "SUPPORTED" },
      draft: { decision: "USED" },
      required_facts: ["left_site", "returned_same_day"],
      missing_facts: [],
      expected_rejection: false,
      integrity: "PASS",
    },
    {
      module_id: "KB-PAY-01",
      knowledge: { decision: "MATCHED" },
      case_intelligence: { decision: "REJECTED", reason: "payment_made missing" },
      claim_plan: { decision: "REJECTED", reason: "payment_made missing" },
      draft: { decision: "—" },
      required_facts: [],
      missing_facts: ["payment_made"],
      expected_rejection: true,
      integrity: "PASS",
    },
  ],
  grounds: {
    independent_notice: [{ module_id: "KB-POFA-02", decision: "VERIFIED_FINDING" }],
    narrative: [{ module_id: "KB-ANPR-01", decision: "SELECTED" }],
    evidence: [],
    verified_finding: [{ module_id: "KB-POFA-02" }],
    carried_forward: [],
    invalidated: [],
    final_merged: [{ module_id: "KB-ANPR-01" }],
    integrity_errors: [{
      code: "GROUND_INTEGRITY_FAILURE",
      message: "POFA_POSTAL_LATE existed before merge but disappeared from the final set.",
      ground: "KB-POFA-02",
    }],
  },
  claim_plan: {
    claim_plan_id: "CP-1",
    version: 2,
    status: "LOCKED",
    approved: ["KB-ANPR-01"],
    items: [{
      item_id: "I-1",
      module_id: "KB-ANPR-01",
      status: "SUPPORTED",
      decision: "SELECTED",
      origin: "SELECTED",
      priority: 1,
      reason: "multiple visits",
      support_bundle: {
        allegation_refs: ["OVERSTAY"],
        supporting_facts: [{ fact: "left_site" }],
        derived_facts: ["multiple_visits"],
        calculated_facts: [],
        verified_findings: [],
        evidence: [],
        relationship_ids: [],
        draft_requirement: {
          must_express: ["left_site", "returned_same_day"],
          must_not_express: [],
          legal_licence: "SELECTED",
        },
      },
      draft_requirement: {
        must_express: ["left_site", "returned_same_day"],
        must_not_express: [],
        legal_licence: "SELECTED",
      },
    }],
  },
  draft_context: {
    approved_grounds: ["KB-ANPR-01"],
    verified_facts: { multiple_visits: true },
    verified_findings: [],
    evidence_refs: [],
    driver_status: "UNIDENTIFIED",
    operator: "Euro Car Parks",
    pcn_number: "88812000363",
    vrm: "KS58OPW",
    lossy_boundaries: [{
      code: "DRAFT_CONTEXT_LOSS",
      message: "FACT EXISTS IN CLAIM PLAN (left_site) BUT MISSING FROM DRAFT CONTEXT",
      fact: "left_site",
    }],
    draft_ran: false,
  },
  draft: null,
  validation: {
    checks: [
      { rule: "VAL-PLAN", status: "NOT_RUN" },
      { rule: "VAL-IDENTITY", status: "PASS" },
      { rule: "VAL-PLACEHOLDER", status: "FAIL", reason: "unresolved placeholder" },
    ],
    ran: true,
  },
  why_stopped: {
    final_state: "MANUAL_REVIEW",
    outcome: "NO_SUPPORTED_GROUNDS",
    blocking_stage: "GROUND_MERGE",
    blocking_status: "FAIL",
    reasons: ["POFA disappeared without invalidation"],
    integrity_errors: [],
  },
  events: [
    { at: "2026-07-17T18:03:01Z", event: "CLASSIFICATION", summary: "PASS" },
    { at: "2026-07-17T18:03:03Z", event: "LEGAL_FINDING", summary: "POFA_POSTAL_LATE VERIFIED" },
  ],
  identity_issues: [],
  plan_versions: [
    { version: 1, status: "SUPERSEDED", approved: ["KB-POFA-02"], run_number: 1 },
    { version: 2, status: "LOCKED", approved: ["KB-ANPR-01"], run_number: 2 },
  ],
};

const compare = {
  a: { label: "Run A", version: 1, grounds: ["KB-POFA-02"], findings: ["POFA_POSTAL_LATE"] },
  b: { label: "Run B", version: 2, grounds: ["KB-ANPR-01"], findings: ["POFA_POSTAL_LATE"] },
  grounds: { added: ["KB-ANPR-01"], removed: ["KB-POFA-02"], unchanged: [] as string[] },
  facts: { added: [{ name: "left_site", value: true }], removed: [] as unknown[] },
  legal_findings: { a: ["POFA_POSTAL_LATE"], b: ["POFA_POSTAL_LATE"] },
  integrity_errors: [{
    code: "GROUND_INTEGRITY_FAILURE",
    message: "Ground disappeared after customer information was added.",
  }],
};

function mockFetch(auth = true, consoleFails = false) {
  return vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    const method = (init?.method || "GET").toUpperCase();
    if (url.includes("/api/admin/session") && method === "GET") {
      return new Response(JSON.stringify({ authorised: auth, token_configured: true }), {
        status: 200,
        headers: { "content-type": "application/json" },
      });
    }
    if (url.includes("/api/admin/session") && method === "POST") {
      return new Response(JSON.stringify({ authorised: true }), { status: 200 });
    }
    if (url.includes("/console/compare")) {
      return new Response(JSON.stringify(compare), { status: 200 });
    }
    if (url.includes("/console/report.txt")) {
      return new Response("Case: C-TEST\nCommit: abc123\n", { status: 200 });
    }
    if (url.includes("/console")) {
      if (consoleFails) {
        return new Response(JSON.stringify({ detail: "boom" }), { status: 500 });
      }
      return new Response(JSON.stringify(mockConsole), { status: 200 });
    }
    return new Response(JSON.stringify({ detail: "not found" }), { status: 404 });
  });
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  window.history.replaceState({}, "", "/");
});

describe("TraceGate", () => {
  it("1. normal customer cannot see trace", () => {
    window.history.replaceState({}, "", "/");
    const { container } = render(<TraceGate caseId="C-TEST" />);
    expect(container.querySelector("[data-testid='case-trace-console']")).toBeNull();
  });

  it("2. authorised trace mode can see it", async () => {
    window.history.replaceState({}, "", "/?trace=1");
    vi.stubGlobal("fetch", mockFetch(true));
    render(<TraceGate caseId="C-TEST" />);
    await waitFor(() => {
      expect(screen.getByTestId("case-trace-console")).toBeInTheDocument();
    });
    await waitFor(() => {
      expect(screen.getByText("TEST CASE / SYNTHETIC DATA")).toBeInTheDocument();
    });
  });
});

describe("CaseTraceConsole", () => {
  beforeEach(() => {
    vi.stubGlobal("fetch", mockFetch(true));
  });

  it("4. all pipeline stages render and 5. failed stage is visible", async () => {
    render(<CaseTraceConsole caseId="C-TEST" />);
    await waitFor(() => expect(screen.getByText("GROUND MERGE")).toBeInTheDocument());
    expect(screen.getByText("CLAIM PLAN")).toBeInTheDocument();
    expect(document.querySelector(".trace-pipeline-name")?.textContent).toBeTruthy();
    expect(screen.getByText("DRAFT REQUIREMENTS")).toBeInTheDocument();
    expect(screen.getAllByText("FAIL").length).toBeGreaterThan(0);
  });

  it("shows the module journey chain", async () => {
    render(<CaseTraceConsole caseId="C-TEST" />);
    await waitFor(() => expect(screen.getByTestId("module-journey")).toBeInTheDocument());
    expect(screen.getAllByText(/Knowledge Matcher: MATCHED/).length).toBeGreaterThan(0);
    expect(screen.getByText(/payment_made missing/)).toBeInTheDocument();
    expect(screen.getByText(/Expected rejection · Integrity: PASS/)).toBeInTheDocument();
  });

  it("6. Claim Plan support bundle displays", async () => {
    render(<CaseTraceConsole caseId="C-TEST" />);
    await waitFor(() => expect(screen.getByTestId("claim-plan-item")).toBeInTheDocument());
    const item = screen.getByTestId("claim-plan-item");
    expect(within(item).getByText(/Support bundle/i)).toBeInTheDocument();
    expect(within(item).getByText(/multiple_visits/)).toBeInTheDocument();
  });

  it("7. run comparison works", async () => {
    const user = userEvent.setup();
    render(<CaseTraceConsole caseId="C-TEST" />);
    await waitFor(() => expect(screen.getByRole("button", { name: /Compare runs/i })).toBeInTheDocument());
    await user.click(screen.getByRole("button", { name: /Compare runs/i }));
    expect(screen.getByTestId("run-compare")).toBeInTheDocument();
    expect(screen.getByText(/Ground disappeared after customer information/i)).toBeInTheDocument();
  });

  it("8. event logs render", async () => {
    const user = userEvent.setup();
    render(<CaseTraceConsole caseId="C-TEST" />);
    await waitFor(() => expect(screen.getByRole("button", { name: /Event log/i })).toBeInTheDocument());
    await user.click(screen.getByRole("button", { name: /Event log/i }));
    expect(screen.getByTestId("event-log")).toBeInTheDocument();
    expect(screen.getByText("LEGAL_FINDING")).toBeInTheDocument();
  });

  it("9. raw sensitive data is hidden by default", async () => {
    render(<CaseTraceConsole caseId="C-TEST" />);
    await waitFor(() => expect(screen.getByTestId("ground-integrity-fail")).toBeInTheDocument());
    expect(screen.queryByTestId("raw-narrative")).not.toBeInTheDocument();
  });

  it("10. actual PCN value displays when available", async () => {
    const user = userEvent.setup();
    render(<CaseTraceConsole caseId="C-TEST" />);
    await waitFor(() => expect(screen.getByRole("button", { name: /^Extraction/i })).toBeInTheDocument());
    await user.click(screen.getByRole("button", { name: /^Extraction/i }));
    expect(screen.getByTestId("trace-pcn-value")).toHaveTextContent("88812000363");
  });

  it("11. unresolved placeholder appears as integrity error", async () => {
    const user = userEvent.setup();
    render(<CaseTraceConsole caseId="C-TEST" />);
    await waitFor(() => expect(screen.getByTestId("ground-integrity-fail")).toBeInTheDocument());
    await user.click(document.getElementById("trace-btn-validation")!);
    expect(screen.getByText("VAL-PLACEHOLDER")).toBeInTheDocument();
  });

  it("requires unlock form when not authorised", async () => {
    vi.stubGlobal("fetch", mockFetch(false));
    render(<CaseTraceConsole caseId="C-TEST" />);
    await waitFor(() => expect(screen.getByText(/Unlock trace/i)).toBeInTheDocument());
    expect(screen.queryByTestId("event-log")).not.toBeInTheDocument();
  });

  it("3. mobile viewport marker uses overflow-safe console class", async () => {
    Object.defineProperty(window, "innerWidth", { configurable: true, value: 390 });
    render(<CaseTraceConsole caseId="C-TEST" />);
    await waitFor(() => expect(screen.getByTestId("case-trace-console")).toBeInTheDocument());
    expect(screen.getByTestId("case-trace-console").className).toContain("trace-console");
  });
});

describe("ResultStep isolation", () => {
  const held: AppealResponse = {
    case_id: "C-TEST",
    state: "MANUAL_REVIEW",
    flags: [],
    questions: [],
    skipped_questions: [],
    outcome: "NO_SUPPORTED_GROUNDS",
    outcome_title: "We could not write an appeal we can stand behind",
    outcome_message: "From the notice and the answers you gave…",
    can_continue: true,
  };

  it("customer result unchanged and no trace without ?trace=1", () => {
    window.history.replaceState({}, "", "/");
    render(<ResultStep data={held} onRestart={() => {}} />);
    expect(screen.getByText("We could not write an appeal we can stand behind")).toBeInTheDocument();
    expect(screen.queryByTestId("case-trace-console")).not.toBeInTheDocument();
  });

  it("12. trace loading failure does not break normal customer result", async () => {
    window.history.replaceState({}, "", "/?trace=1");
    vi.stubGlobal("fetch", mockFetch(true, true));
    render(<ResultStep data={held} onRestart={() => {}} />);
    expect(screen.getByText("We could not write an appeal we can stand behind")).toBeInTheDocument();
    await waitFor(() => expect(screen.getByTestId("trace-load-error")).toBeInTheDocument());
    expect(screen.getByText("We could not write an appeal we can stand behind")).toBeInTheDocument();
  });
});
