# Change log: EV false match, save/reload persistence, WebP uploads

Found by the live end-to-end run on a photographed Parent and Child bay Notice to Keeper (front and back, WebP). Builds on `2441db5` (Phase 2). No KB, prompt, validator, Claim Plan or drafting change.

## 1. EV charging invented from the word "charge" (critical, live on main since `2cae0dc`)

| | |
|---|---|
| **What** | The `ev_charging_session` rule in `engines/account.py` now matches only EV terms: a charger or charge point, an electric vehicle, "EV", plugged in, on charge, charging *the car*, and similar. A negated account ("I was not charging my car") is ignored. The rule's allegation-relevance tokens are whole terms. |
| **Why** | A released letter told the operator the vehicle was there for "a genuine charging session" and made EV_CHARGING the lead ground. The customer had written "I would like to appeal the charge." |
| **Root cause** | The pattern `\b(charg(e\|ing)\|…)\b` matched the plain word "charge". 10 of 12 generic narratives tested set the fact. The relevance tokens `"ev"` and `"charg"` were substrings of "every", "event" and "parking charge". |
| **Blast radius** | Only free-text extraction of `ev_charging_session`. Genuine EV accounts are still read (10/10 phrasings tested). |
| **Not changing** | The other circumstance rules, the KB `EV_CHARGING` module, and validation. |
| **Known, not fixed** | Two other rules over-match: `resident_connection_stated` ("I'm a UK resident", "I live nearby") and `loading_activity` ("waiting for a delivery"). They are not in scope here and are noted for a follow-up. |

## 2. Save and reload (a restart, a redeploy or a second worker)

| Defect | Cause | Fix |
|---|---|---|
| The both-sides gate asked again for a notice already uploaded | Page images were never stored | New `evidence_pages` table (bytea, cascade-deleted with the case). Only changed pages are written. |
| A confirmed fact came back EXTRACTED | `save()` versioned facts on a value change only | The comparison includes status and source. Still append-only. |
| A fact removed from the case came back | Nothing superseded it | It is superseded on save |
| letter.pdf returned 404 after a reload | The draft was stored but never loaded; the released state and letter text were not stored | `drafts` gains state, letter, evidence_list, outcome and no_ground_reason. `load_output()` rebuilds the AppealOutput, and `_rehydrate` attaches it. A legacy row is treated as RELEASED only if the case is RELEASED and its validation passed. |
| An internal key appeared as an asked question | `asked_questions` was rebuilt from raw_answers, including `_material_source_texts` | `asked_questions` and `pending_questions` are stored columns. `_` working keys are not stored. The legacy fallback skips them. |
| The customer's account was wiped on the next round | The narrative was not loaded, so the account engine cleared every fact taken from it | raw_answers is restored, latest wording per question (an edit is a new row). The drafter still sees only Facts (Q-06). |
| The question-round cap was reset | The audit trail was not loaded, and analysis counts its rounds from it | The audit is loaded, marked as already persisted |
| A reloaded case crashed with a 500 (date vs str) | jsonb stores dates as strings | An exact `YYYY-MM-DD` value is revived as a date on load |
| A cleared upload came back | Evidence rows were never removed | Labels no longer on the case are deleted |

## 3. WebP accepted, then rejected as unreadable

- `ingest._to_jpeg` falls back to Pillow when PyMuPDF can't read the file. Pillow is now declared in both requirements files; it was already installed as a WeasyPrint dependency.
- `IMAGE_TYPES` lists only formats that decode: JPEG, PNG, WebP, TIFF, GIF, BMP. HEIC/HEIF are removed and give a clear message to upload JPEG or PNG.
- Image signatures are sniffed before the content type. An upload with no content type was being decoded as text.
- Frontend: the file picker accepts WebP (`compress.ts` already re-encodes it to JPEG). The blob token no longer allows HEIC/HEIF. `tsc --noEmit` passes.

## Tests

- New: `test_ev_charging_free_text.py` (5), `test_image_formats.py` (7) and `test_reload_persistence.py` (18). The persistence tests run the real store SQL against `tests/sqlite_store.py`.
- On the old code: the EV tests fail with 19 subtest failures, including the end-to-end pipeline test that reproduces the invented ground. The persistence tests fail 16 of 18; the other 2 are guards.
- Full suite: **458 run, 23 failures, 6 errors, 7 skipped.** The failure set is identical to the pre-change baseline (428 run, same 29 known failures), diffed by test id.
- **Live** (real OpenAI, your original WebP files with no conversion, the narrative "I would like to appeal the charge", reloaded from storage before every step):
  - Route PRIVATE_PARKING, then RELEASED.
  - Grounds: bay evidence and PoFA keeper liability. No EV wording.
  - Every reload identical.
  - letter.pdf returned 200 both before and after the reload.

## Not tested

- A real Postgres round trip. No local server was available; the database in `.env` may be production.
- Deployed Railway/Vercel.
- HEIC: no decoder; it is now refused clearly.

## Deployment

Not deployed. Before deploying:

1. Run `python -m pcn_appeal.store init` (new table and columns; all additive and nullable).
2. Rebuild the image so Pillow is pinned.

Rollback: redeploy the previous build. The old code ignores the new columns and table.
