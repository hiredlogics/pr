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
  | "RELEASED"
  | "NO_APPEAL_RIGHT"
  /** Our own document classifier returned nothing: retryable, and not the
   *  customer's fault. Distinct from NO_APPEAL_RIGHT, which is a refusal. */
  | "CLASSIFICATION_FAILED";

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

/** The service intake routed the case to. Only PRIVATE_PARKING has a journey
 *  today; every other route ends at a stop screen. */
export type Route =
  | "PRIVATE_PARKING"
  | "DEBT_RECOVERY"
  | "ORDER_FOR_RECOVERY"
  | "CHARGE_CERTIFICATE"
  | "COUNCIL_PCN"
  | "CLAIMS"
  | "BAILIFF"
  | "CCJ_REMOVAL"
  | "UNSUPPORTED_REVIEW";

export type Cta = { label: string | null; action: string | null; href?: string } | null;

/** Result of uploading into an existing case. Either extraction has run (the
 *  private parking journey continues), or intake stopped the case and the stop
 *  fields say why - in which case there is nothing to confirm. */
export type UploadResult = {
  case_id: string;
  state: CaseState;
  route?: Route | null;
  flags: string[];
  rejected: Rejected[];
  read_as: ReadAs[];
  questions?: Question[];
  skipped_questions?: string[];
  stop_code?: string;
  stop_title?: string | null;
  stop_reason?: string;
  recommendation?: string;
  cta?: Cta;
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
  // Present once the pipeline has produced an output pack. Routes, PoFA codes,
  // Code versions, module IDs and the retrieval trace are deliberately not part
  // of this contract: they are internal, and live on GET /cases/{id}/trace.
  grounds?: string[]; // plain-English route labels; only when state === "RELEASED"
  evidence_list?: string[];
  letter?: string; // only when state === "RELEASED"
  route?: Route | null;
  /** Set when state === NO_APPEAL_RIGHT: the document was routed out of this service. */
  stop_code?: string;
  stop_title?: string | null;
  stop_reason?: string;
  recommendation?: string;
  cta?: Cta;
  /** Why a held case stopped — never collapsed to a single merits message. */
  outcome?:
    | "NO_SUPPORTED_GROUNDS"
    | "PROCESSING_ERROR"
    | "NEEDS_DOCUMENTS"
    | "NEEDS_FACTS"
    | "SCOPE_INELIGIBLE"
    | "CLASSIFICATION_FAILED";
  outcome_title?: string;
  outcome_message?: string;
  outcome_next?: string;
  can_continue?: boolean;
};

export type Health = {
  status: string;
  modules: number;
  blocks: number;
  /** Product label from the API (e.g. version_2) so deploys are identifiable. */
  app_version?: string;
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
