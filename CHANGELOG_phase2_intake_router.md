# Change log — Phase 2: common document classifier + service router

Scope: classification and routing only. **No OFR or Charge Certificate business
logic.** No change to Case Intelligence, the Claim Plan, PoFA, question
generation, drafting, the private KB, validators or the extraction prompt.
Builds on `1e7b103` (Phase 0).

## What changed

| Area | Change |
|---|---|
| Neutral classifier | New prompt task `classification` (v1) and `pcn_appeal/intake/classifier.py`. One call per upload, before any service reads a document. Contract: `document_type`, `service_family`, `stage`, `issuer`, `references`, `document_date`, `confidence`, `evidence_spans` (checked against the text where there is one), `ambiguity_reason`. Malformed output is corrected to the contract (unknown label → UNKNOWN, bad stage → default, skipped document → UNKNOWN "not classified"); no usable answer → `ClassificationFailed`. |
| Document types | `pcn_appeal/intake/document_types.py`: the 11 canonical types plus `SUPPORTING_EVIDENCE` (receipts, photos, leases), which never decides a route. Stage list per type, a fixed regime family where the type implies one, and the stage-precedence order. |
| Route registry | `pcn_appeal/intake/router.py`: `ServiceRouteRegistry` (type → route) and `resolve()`. Exactly one route. The most advanced document decides. Confidence below 0.6, or a family that contradicts the type, → `UNSUPPORTED_REVIEW`, never a guessed service. |
| Service engines | `pcn_appeal/services/`: `ServiceEngine` interface; `PrivateParkingService` delegates every step to the unchanged `AppealPipeline`; the other 8 routes are `RedirectService`s (identify, stop, raise `ServiceNotAvailable` for every engine step). One package per route. |
| Route completeness | Moved after classification. Private parking: front + back or a multipage PDF (enforced, same 422 as before). Charge Certificate: front + back (declared, enforced once the service is live). OFR: the Order itself (declared). Debt, council, claims, bailiff, CCJ, unknown: no requirement. |
| Debt recovery | The `civil\s+enforcement` pattern is removed from `DEBT_SIGNALS`. EX-13 no longer relabels a document the model classified as PCN/NTK/NTD, so a routine "may be passed to debt recovery" warning on a notice is not a debt demand. New customer wording as specified, with an optional CTA link (`CTA_URL_DEBT_RECOVERY_TEMPLATE`). |
| Validation boundary | `pcn_appeal/services/validators.py`: CORE vs route rules. Private rules (including VAL-STAGE's "county court" ban) are registered for PRIVATE_PARKING only. Any other route has no validator, so its output cannot be released (fail closed). |
| CaseFile | New fields `route`, `document_type`, `stage`, `classifications`, `timeline`. |
| Persistence | `cases` table gets `route`, `document_type`, `stage`, `scope_stop`, `document_classes`, `classifications` and `timeline` columns (`ALTER … IF NOT EXISTS`); `save()`/`load()` round-trip them. This also fixes the audit finding that a rehydrated case lost `document_classes` and became CLASSIFICATION_FAILED. |
| API | Every upload route runs intake first. `/confirm`, `/appeal/{id}`, `/answers` and `/generate` return the intake stop without running any engine. New `GET /cases/{id}` (state, route, document type, stage, stop wording; no facts). Payloads carry `route` and `stop_title`. |
| Defence in depth | `scope.decide()` honours the router: called directly, the private pipeline refuses a case routed elsewhere. A case the router never saw (`route=None`, which is the whole scenario suite) is gated exactly as before. |
| Frontend | When the upload response is a stop, the journey goes straight to the result screen instead of asking the customer to confirm details and describe what happened first. The result screen shows the route's `stop_title`; a `RESOURCES` CTA key was added. `tsc --noEmit` passes. |
| Test doubles | `FakeLLM`, `ReferenceAnalysisLLM` and `DemoLLM` answer `classification`. A fixture that scripts only an extraction response is routed by its `doc_types` labels (peeked, not consumed), so existing fixtures route exactly as their labels already said. |

## Routing before → after

| Upload | Before | After |
|---|---|---|
| Private NTK | Proceeds | Proceeds (route PRIVATE_PARKING) |
| NTK from "Civil Enforcement Ltd", or with a "may be passed to debt recovery" warning, as a text PDF | **DEBT_RECOVERY stop** (regex) | Proceeds |
| Debt letter, one page | **422 "upload both sides"** | Debt stop with the specified wording |
| Order for Recovery | **COURT_CLAIM stop** (or 422 if one page) | ORDER_FOR_RECOVERY: "available through the OFR flow" |
| Charge Certificate | Debt or council stop, or 422 | CHARGE_CERTIFICATE redirect |
| Letter before claim / county claim | COURT_CLAIM stop | CLAIMS stop (same guidance CTA) |
| Bailiff letter | DEBT_RECOVERY stop | BAILIFF stop |
| CCJ | COURT_CLAIM or UNSUPPORTED | CCJ_REMOVAL stop |
| Operator's appeal rejection | Usually proceeded to an *initial* appeal | Stop: "already replied to an appeal" |
| Receipt only / unreadable | UNSUPPORTED, or proceeded via the fact valve | UNSUPPORTED_REVIEW: "couldn't identify, upload again" |
| Classifier failure | CLASSIFICATION_FAILED | CLASSIFICATION_FAILED (no engine runs) |

## Private-parking blast radius

- **Unchanged:** `orchestrator.py`, `analysis.py`, `claim_plan.py`, `reasoning.py`, `pofa.py`, `drafter.py`, `validation.py`, `outcome.py`, the KB YAML and the `extraction` prompt (v9).
- **Changed on the private path:**
  - One extra LLM call per upload (classification on gpt-5.1): about 20s live end to end in the run below.
  - EX-13 no longer relabels a model-labelled notice.
  - The debt regex no longer matches a company name.
  - An operator rejection letter now stops instead of producing an initial appeal.
  - An UNKNOWN-only upload no longer reaches the private engine through the "notice-shaped facts" valve. That valve still works inside the pipeline when the router has sent the case to private parking.

## Test results

- Baseline before the change (`python -m unittest discover -s tests`, no secrets, no DB): **403 run, 23 failures, 6 errors, 7 skipped**.
- After: **428 run, 23 failures, 6 errors, 7 skipped.** The failure set is identical to the baseline (diffed by test id). No existing expected result was edited.
- New: `tests/test_intake_routing.py`, 25 tests. Every listed document type: one classification, one route, its completeness policy, its customer outcome, and no extraction call or private fact for non-private routes. Also: Civil Enforcement Ltd notice, both-sides rule still enforced for private, precedence, low confidence, family contradiction, classifier contract, private stage stops, downstream gates, direct-call refusal, no fake engines, validator separation, rehydration round trip.
- **Live (real OpenAI, synthetic text documents, local, no DB):** 11/11 document types routed correctly. The private journey ran end to end through the API: the Civil Enforcement Ltd NTK with a debt-recovery warning was classified as a notice, extracted as NTK, and reached questions with no stop.
- Not live-tested: photographed or scanned uploads, and the client's genuine OFRs and Charge Certificates (none supplied yet).

## Known gaps

1. Customer wording for the OFR, Charge Certificate, claims, bailiff, CCJ, operator-response and unknown stops is **DRAFT** and needs client approval. Only the debt wording was specified.
2. CORE validators are catalogued but still execute inside the private `ValidationEngine`. VAL-SIGNATURE and VAL-DOCUMENT have no implementation yet (Phase 3).
3. No human review queue: UNSUPPORTED_REVIEW tells the customer to re-upload; nobody reviews it.
4. A notice the classifier calls UNKNOWN now stops instead of being let through by its fields. That is safer, but it can strand a customer whose notice is badly misread.
5. When a council document and a private document are uploaded together, the council document wins by precedence. They are probably two different charges; this should become review.
6. `case.facts` is owned by the private route. Typed per-route facts arrive with the first non-private engine.
7. Preferred API names: `/cases/{id}/documents` is still the operator text route; customers upload via `/files` and `/blobs`. `GET /cases/{id}` is not on the Next proxy allowlist because the frontend doesn't call it.
8. The legacy FastAPI-served page at `/` has no route-specific handling. The Next frontend is the customer surface.

## Deployment status

**Not deployed.** Local commit only. Phase 0 is not deployed either. Before deploying this build:

1. Run `python -m pcn_appeal.store init` against the production DB (new columns; without them every case save fails).
2. Publish a new KB release (`python -m pcn_appeal.store sync`). The new `classification` prompt makes the current release `kb-20260930T231446Z` drift from the YAML, and the API refuses to start on drift (the intended gate).
3. Deploy the backend, then the frontend (route-aware upload flow).
4. Optional: set `CTA_URL_DEBT_RECOVERY_TEMPLATE`.

**Rollback:** redeploy the previous backend and frontend. The new columns are additive and nullable, so the old code ignores them. The new KB release can be superseded by re-syncing from the old build.
