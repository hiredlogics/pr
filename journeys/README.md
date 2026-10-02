# Journey regression harness

Each `*.yaml` here is a **customer journey as input only**: the documents the
customer uploaded, what they said, and how they answer whatever the system
asks. Nothing in a journey tells the system what to conclude. The harness
drives the real customer routes (`POST /appeal`, `POST /appeal/{id}`), reads
the admin audit (`GET /admin/cases/{id}/audit`) and marks the journey
PASS / FAIL from the integrity checks, the journey's own `expect` block (if
any) and determinism (`repeat`).

    python -m pcn_appeal.integrity journeys journeys/ --out reports/
    python -m pcn_appeal.integrity journeys journeys/ --base-url https://<staging> --admin-token "$ADMIN_TOKEN" --out reports/

`reports/<case_id>_CASE_REPORT.md` is the audit report for each run.

Rules for journey files: synthetic notices only - no real names, addresses or
registrations; no operator-specific expectations; `expect` states what the
*architecture* promises (state, outcome, which argument families, what must
be asked), not a wording.
