# P7.5 — In-page Case Trace Console + mobile test view

Branch `feature/p7.5-case-trace-console` from `main` @ `17046ca`.

## WHAT

Admin/test **Case Intelligence Trace** rendered **below** the existing customer
result (`ResultStep`), only when:

1. `?trace=1` is present, **and**
2. an admin session cookie is authorised against `ADMIN_TRACE_TOKEN` /
   `ADMIN_TOKEN` (query string alone is never enough).

### Backend

- `GET /admin/cases/{id}/console` — structured console payload (facts, narrative
  interpretation, allegations, relationships, legal findings, knowledge,
  ground sets + integrity errors, claim plan support bundles / draft
  requirements, draft-context loss detection, validation catalogue, why-stopped,
  events, identity/placeholder issues). Works even when `output is None`.
- `GET /admin/cases/{id}/console/compare` — A/B plan comparison.
- `GET /admin/cases/{id}/console/report.txt` — PII-safe pasteable report.
- Aggregator: `pcn_appeal/integrity/console.py` (read-only; no re-reasoning).

### Frontend

- `/api/admin/session` — unlock / status / lock (httpOnly cookie).
- `/api/admin/[...path]` — admin proxy injecting the server token; separate from
  the customer allowlist.
- `components/trace/*` — mobile-first accordions, status chips, ground integrity
  banners, run comparison, copy report.
- Customer outcome copy **unchanged**.

## WHY

Live testing on phone needs the full decision trail under the same UI that
customers see, without exposing internals when trace mode is off.

## NOT CHANGING

- Reasoning engines, Claim Plan Authority decision logic, PoFA calculators.
- Customer `AppealResponse` fields.
- Public `/api/[...path]` allowlist (still customer-only).

## TESTS

- `tests/test_case_console.py` — payload, integrity failure, placeholder, API auth.
- `frontend` vitest — 13 tests covering the §31 checklist items.

## SCREENSHOTS

`frontend/trace-screenshots/generate.mjs` writes HTML fixtures A–D; PNGs when
Playwright Chromium is installed.
