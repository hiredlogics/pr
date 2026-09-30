# Driver-status provenance & notice completeness — case 171ca28d / PCN 8101353

## Case identity (authorized records)

| Field | Value |
|---|---|
| Case ID | `171ca28d-2786-49fa-982b-5c9df67e40bf` |
| PCN | `8101353` |
| VRM | `YT17RKE` |
| Operator | UK Parking Patrol Office Ltd |
| Created | 2026-09-30 15:34:47 UTC |
| State (as audited) | `MANUAL_REVIEW` |
| `driver_status` | `FORMALLY_IDENTIFIED` |
| KB | `kb-20260930T122308Z` |
| Evidence | 1× JPEG (`WhatsApp Image 2026-09-30 at 8.25.56 PM.jpeg`), OCR chars=0, images=1 (vision used) |

Related same-PCN cases (not substituted):

- `4efd7b15-1519-404f-a829-b0fc403e437c` — `UNIDENTIFIED`, 15:22 UTC
- `cb888d65-1495-4df1-b712-947cebe2349b` — `FORMALLY_IDENTIFIED`, RELEASED payment letter, 16:57 UTC

## 1. Provenance of FORMALLY_IDENTIFIED

### Writers (code search — exhaustive)

| Location | Condition |
|---|---|
| `pcn_appeal/api.py` `POST /appeal` | request `driver_already_named_to_operator` → yes |
| `pcn_appeal/api.py` `POST /appeal/files` | Form field → yes |
| `pcn_appeal/api.py` `POST /cases/{id}/confirm` | JSON field → yes |

**Not writers:** extraction, LLM, migrations, account/narrative promotion, reanalysis, admin (until new correction route), defaults (`UNIDENTIFIED`).

### Chain for this case

| Step | Evidence |
|---|---|
| UI | Live `SituationStep` (commit `2cae0dc` / origin) had checkbox *“operator has already been formally told who was driving”*, default **unchecked**, sent as `driver_already_named_to_operator` on confirm |
| Payload | **UNVERIFIED** — API never logged the submitted raw value or type |
| Stored | `cases.driver_status = FORMALLY_IDENTIFIED` by confirm time (15:36:19) when PoFA notes first show *“Driver formally identified - keeper route not used.”* |
| Previous | Default at insert: `UNIDENTIFIED` (DB default + `new_case`) |
| Next | Remained `FORMALLY_IDENTIFIED` through drafts |
| Narrative | `"nothign"` — no disclosure answer in `raw_answers` |
| Coercion check | Current FastAPI/Pydantic: JSON/`Form` string `"false"` → `False` (reproduced). **Not** proven as the cause on this stack |

### Defects checked

| Hypothesis | Result |
|---|---|
| `"false"` / `"NO"` treated as truthy | **Not reproduced** on current typed bool Form/JSON |
| Wrong field bound to checkbox | Checkbox bound to `alreadyNamed` only (UI code) |
| Default affirmative | `useState(false)` — default unchecked |
| First-person narrative set status | **No** — no writer from narrative; sibling case with fuller narrative stayed `UNIDENTIFIED` |
| Stale cross-case state | **No** — new UUID per case; independent cases tested |
| Reanalysis overwrite | **No** — only API writers; no reanalysis writer |

### Rate signal (7 days)

`FORMALLY_IDENTIFIED` 23 / (`UNIDENTIFIED` 51 + FI 23) ≈ **31%**, often with throwaway narratives (`nothing`, `no`, `please`). Consistent with accidental checkbox use and/or missing request audit — **not** proof of customer intent for this case.

**Verdict:** Source path is the confirm/appeal `driver_already_named_to_operator` flag. Exact submitted value for `171ca28d` is **UNVERIFIED**. Do not blame the customer.

## 2–5. Code changes (this branch — not deployed unless separately shipped)

| Area | Change |
|---|---|
| Disclosure | `pcn_appeal/disclosure.py` — `CONFIRMED_YES` / `CONFIRMED_NO` / `UNKNOWN`; strict parse; audit `driver_disclosure_set`; `keeper_route_blocked` only on YES |
| API | Confirm/appeal use `apply_disclosure`; Form field is **string** (not coerced bool); `POST /cases/{id}/disclosure` auditable correction |
| Frontend | SituationStep no longer collects disclosure; omits field → UNKNOWN |
| Completeness | `pcn_appeal/notice_completeness.py` — rejects duplicate fronts; multipage/distinct pages only; server-side block on confirm/auto with `NEEDS_DOCUMENTS` |
| PoFA 9(2)(e) | Recovery records `pofa_9_2_e_status`: SATISFIED / DEFECT_IDENTIFIED / UNRESOLVED / NOT_APPLICABLE |
| Narrative | Stored before scope stop; keeper-safe rewrite unchanged and does not touch disclosure |

## Affected existing cases

- All historical `FORMALLY_IDENTIFIED` rows without a `driver_disclosure_to_operator` fact remain as-is (**no blanket reset**).
- Case `171ca28d-…` and `cb888d65-…` need an **auditable correction** via `POST /cases/{id}/disclosure` with reason if product confirms disclosure was wrong.
- Front-only private-parking cases will now pause with `NEEDS_DOCUMENTS` instead of running empty-pack merits.

## Regression results (local evidence)

```
python -m unittest tests.test_disclosure_provenance tests.test_pofa_invitation_content
→ Ran 26 tests — OK (disclosure + invitation scan)
```

(See session `_test_out.txt` for the verbose run.)

## Deployment status

**NOT DEPLOYED by this task.** Changes are in the working tree. Do not client-retest production until API + frontend are shipped together.

## Rollback plan

1. Revert deploy of API service to previous Railway release.
2. Revert Vercel frontend to previous deployment.
3. Disclosure corrections already applied remain in `audit_log` / facts (append-only) — do not mass-delete.
4. Feature flags: none; rollback is release rollback only.
