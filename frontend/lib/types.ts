// Mirrors the FastAPI surface in pcn_appeal/api.py exactly. Nothing here is
// invented: every field is one the backend actually returns.

export type CaseState =
  | "CREATED"
  | "EXTRACTED"
  | "CONFIRMED"
  | "QUESTIONING"
  | "ANALYSED"
  | "DRAFTED"
  | "VALIDATION_FAILED"
  | "MANUAL_REVIEW"
  | "RELEASED";

export type QuestionType = "bool" | "choice" | "int" | "text";

export type Question = {
  fact: string;
  text: string;
  type: QuestionType;
  options?: string[];
};

export type ReadAs = {
  evidence_id: string;
  filename: string;
  chars: number;
  images: number;
};

export type Rejected = { filename: string; reason: string };

/** Result of uploading into an existing case: extraction has run, nothing more. */
export type UploadResult = {
  case_id: string;
  state: CaseState;
  flags: string[];
  rejected: Rejected[];
  read_as: ReadAs[];
};

/** One row on the "Check your details" screen. */
export type Detail = {
  name: string;
  label: string;
  value: string;
  /** Missing, or read too unreliably to rely on. Draw the eye to it. */
  needs_attention: boolean;
};

export type Confirmation = {
  case_id: string;
  state: CaseState;
  flags: string[];
  details: Detail[];
};

export type BlockingIssue = {
  rule: string;
  severity: string;
  message: string;
  sentence: string | null;
};

export type AppealResponse = {
  case_id: string;
  state: CaseState;
  flags: string[];
  questions: Question[];
  skipped_questions: string[];
  read_as?: ReadAs[];
  rejected?: Rejected[];
  // present once the pipeline has produced an output pack
  primary_route?: string | null;
  secondary_routes?: string[];
  grounds?: string[]; // plain-English route labels, supplied by the backend
  pofa_route?: string;
  pofa_findings?: string[];
  code_version?: string | null;
  module_ids?: string[];
  evidence_list?: string[];
  letter?: string; // only when state === "RELEASED"
  blocking_issues?: BlockingIssue[];
};

export type Health = {
  status: string;
  modules: number;
  blocks: number;
  kb_release: string | null;
  store: string;
  provider: string;
  models: Record<string, string>;
  provider_note: string;
  /** False when no vision-capable model is configured: a photographed notice or
   *  scanned PDF will rasterise fine but yield no facts, so say so up front. */
  vision: boolean;
};

export type Trace = {
  trace: string[];
  module_ids: string[];
  missing_facts: string[];
  prohibited_claims: string[];
  sentences: {
    text: string;
    fact_refs: string[];
    module_refs: string[];
    evidence_refs: string[];
  }[];
  audit: Record<string, unknown>[];
};

/** Thrown for any non-2xx from the proxy, with the backend's own words where it gave some. */
export class ApiError extends Error {
  status: number;
  rejected?: Rejected[];
  constructor(message: string, status: number, rejected?: Rejected[]) {
    super(message);
    this.status = status;
    this.rejected = rejected;
  }
}
