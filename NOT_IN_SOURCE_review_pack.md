# NOT_IN_SOURCE governance review pack

Branch `feature/p7-kb-amendments` · 55 modules · 74 blocks
Suite 964 run / 0 failed / 7 skipped · journeys 5/5 PASS

---

## 1. Read this first: the "25 modules" figure was misleading

You asked for this pack on the basis that *"25 of the 55 active modules contain
system-authored propositions that are not traceable to my approved source KB."*
That was my framing in the earlier report, and it was wrong. I need to correct
it before you spend review time on the wrong 21 modules.

`legal_basis_origin` does **not** record whether a module is in your document.
It records where that module's **legal basis citation** came from. The field's
own definition in `kb_modules.yaml` is:

> `NOT_IN_SOURCE` — added in conversion; needs legal review (KB-GOV-06)

and it is attached to the `legal_basis` list, not to the proposition. The file
header states the actual position plainly: 51 of the modules were converted
from the 51 KB-* records in your document, and only a handful are not in it.

**So the 25 split two ways:**

| | Count | What is actually system-authored |
|---|---|---|
| **A.** Have a `source_reference` into your approved document | **21** | Only the short `legal_basis` tag. The `topic` and `core_proposition` are your source wording — several `change_notes` read "Core proposition restored to source wording." |
| **B.** Have no `source_reference` at all | **4** | The whole module. These are genuinely invented. |

The four in group B are **KB-POFA-06, KB-BAY-01, KB-BAY-02, KB-REC-01** — which
are exactly the four you have already reviewed and ruled on. There is no fifth
surprise and no arbitrary threshold buried behind the other 21.

What group A genuinely needs from you is narrower than a content review: it is
sign-off on **eleven short citation tags** that the conversion invented because
your module records had no formal citation field (§2). I would not call that a
launch blocker, but it is yours to decide.

## 2. The eleven authored legal-basis tags (group A's real exposure)

These strings are the system's own shorthand. They do not appear in letters —
they drive `legal_basis` provenance and the reasoning trace only.

| Tag | Modules | Comment |
|---|---|---|
| `CL-authorisation` | AUTH-01, AUTH-03, CUST-01, ACT-03 | common-law authorisation |
| `CL-contract-formation` | CON-02, ACT-01, ACT-02, EVCH-01 | offer/acceptance |
| `CL-derogation-from-grant` | RES-04 | established property doctrine |
| `CL-evidential-weight` | GRACE-02, EV-01, BAY-01, BAY-02, INFRA-01 | burden/sufficiency of evidence |
| `CL-primacy-of-contract` | RES-03, RES-06 | your document's own section title |
| `CL-quiet-enjoyment` | RES-05 | established property doctrine |
| `Lease` | RES-04, RES-05, RES-07 | the customer's own lease |
| `PoFA-2012-Sch4` | POFA-05, POFA-06 | statute |
| `SCOP-authority` | RES-07, LAND-01/02/03 | Single Code of Practice, authority |
| `SCOP-evidence` | REC-01 | Single Code of Practice, evidence |
| `SCOP-permits` | AUTH-01/02/03 | Single Code of Practice, permits |

`PoFA-2012-Sch4` and `Lease` are not system inventions in any meaningful sense.
The `CL-*` and `SCOP-*` tags are the system's labels for doctrines and Code
sections your document discusses without formally citing.

## 3. System-authored numerical thresholds, assumptions and decision rules

You asked specifically: *"I do not want another arbitrary threshold buried in
the KB."* I audited the module gates, the propositions, the blocks, the derived
facts those gates depend on, and the Python decision rules.

### 3.1 Numerical thresholds in NOT_IN_SOURCE module gates: NONE

After the BAY-01 amendment, **no NOT_IN_SOURCE module gate contains a numeric
comparison.** The only non-boolean comparison left across all 25 is
`hire_docs_supplied == false` in KB-POFA-06, which is a boolean. A regression
test in `tests/test_kb_amendments_v2.py` sweeps every module in the KB and
asserts none thresholds the observation window, so the five-minute figure
cannot return without a red test.

### 3.2 Numbers in propositions and blocks: NONE that are operative

Three matches, all benign: "Schedule 4" and "Protection of Freedoms Act 2012"
(statutory references) and "section 16 priority 4" in a KB-LAND-02 drafting
note (an internal cross-reference to your own document).

### 3.3 Hidden thresholds in the derived facts the gates read: NONE

The 58 gating facts used by these modules resolve to closed customer questions,
document fields, or evidence presence. `short_presence_before_acceptance`,
`exit_congestion` and `independent_evidence_contradicts` all read as though they
might hide a duration test; each is in fact a yes/no question answered by the
customer, with no minute figure anywhere.

### 3.4 Decision rules that ARE system-authored — three, all visible

**(a) The strength numbers themselves.** All 25 strength values are
system-assigned. Your document's section 16 gives bands (90–100 dispositive,
70–89 strong, 50–69 real but qualified) but not per-module figures. Every
number in the §5 entries below is therefore the system's judgement within your
bands. This is the single largest body of system-authored decision-making in
the KB, and it is the one thing in this pack I would most want you to sanity
check — particularly the outliers: **KB-POFA-05 at 93** (the only one above 90)
and **KB-LAND-01 at 20**.

**(b) The strength-50 cutoff, used in two places.**
- `pcn_appeal/engines/outcome.py:217` — only modules with `strength >= 50`
  count as "leading" grounds for the customer-facing outcome.
- `pcn_appeal/engines/reasoning.py` R-06 — supporting-only routes
  (`strength < 50`) never lead, max 3 secondary routes.

50 is the lower bound of your own "real but qualified ground (may lead, but
rarely first)" band, so the number is derived from your material. The **"max 3
secondary routes"** figure in R-06 is not — that is system-authored.

**(c) `authority_challenge_proportionate`** (`engines/claim_plan.py:135`).
KB-LAND-01 is only argued where Case Intelligence positively selects it. This
replaced an earlier always-true filler, and it is why LAND-01 sits at strength
20 rather than being dropped. Not numerical, but it is a system-authored
decision rule about when a ground enters an appeal, so you should see it.

### 3.5 One defect found during this audit, not fixed

`HIRE_KEEPER` in `pcn_appeal/engines/extraction.py:144` still contains two raw
`.{0,40}` gaps — the same cross-sentence defect class as the
`payment_attempt_failed` bug you told me to fix. It matches **notice text**
rather than customer prose, so it cannot fabricate a customer assertion, which
is why I have not widened the commit to cover it. It should be fixed with the
POFA-06 work, since that is the module it feeds.

## 4. The four genuinely authored modules

| Module | Status | Your decision | Where it stands |
|---|---|---|---|
| KB-BAY-01 v1.5 | ACTIVE | principle approved, trigger amended | **done**, threshold removed |
| KB-BAY-02 v1.1 | ACTIVE | approved with provenance tightening | **done**, with one limitation (§6) |
| KB-REC-01 v1.2 | ACTIVE | amend, no keyword activation | **done** |
| KB-POFA-06 | **REVIEW** | amend, then activate | **awaiting your approval** of drafted wording |

## 5. Module-by-module entries

Format: module ID → purpose → trigger → proposition → block → legal basis →
strength → prohibited claims → example. Group B (genuinely authored) is marked.

### KB-POFA-05 — Driver not established

`ACTIVE` · v1.1 · POFA · **strength 93**

**Purpose.** A postal Notice to Keeper arrives outside the Schedule 4 window, or omits the keeper-liability warning, and the driver has never been named. The letter concludes that keeper liability has not been established.

**Trigger:**
- **ALL of:**
  - `driver_status` == "UNIDENTIFIED"
  - **ANY of:**
    - `pofa_finding` in ["POFA_POSTAL_LATE", "POFA_NTD_NTK_LATE", "POFA_NTD_NTK_TOO_EARLY", "POFA_NTK_INVITATION_DEFECT"]
    - **ALL of:**
      - `ntk_defect_document_confirmed` is true
      - **ANY of:**
        - `ntk_defect_parking_details` is true
        - `ntk_defect_keeper_warning` is true
        - `ntk_defect_creditor` is true
        - `ntk_defect_charge_amount` is true
        - `ntk_defect_statutory_invitation` is true

**Shut off when:**
- `driver_status` == "FORMALLY_IDENTIFIED"

**Required facts:** `pofa_finding`

**Proposition.** If Schedule 4 conditions are not met and the driver has not been established, the operator cannot transfer the driver's liability to the registered keeper under Schedule 4.

**Legal basis:** `PoFA-2012-Sch4`

**Block `PP-POFA-002`** — ACTIVE, Appendix A:

> The registered keeper has not received a Notice to Keeper capable of establishing keeper liability under Schedule 4. In the absence of compliance with the statutory procedure required to transfer liability, the operator has not established a right to recover this charge from the registered keeper under Schedule 4.

**Block `PP-POFA-006`** — ACTIVE, Appendix A:

> The operator has not established the identity of the driver. As the applicable Schedule 4 requirements have also not been satisfied, liability cannot be transferred to the registered keeper under Schedule 4.

**Block `PP-POFA-007`** — ACTIVE, Appendix A:

> For the verified reasons set out above, the operator has not established keeper liability under Schedule 4 of the Protection of Freedoms Act 2012. The charge should therefore be cancelled as against the registered keeper.

**Guardrails.** Never invite or infer driver identity. This is the conclusion that follows a verified finding, not a free-standing ground.

**Prohibited claims:** the driver was; identity of the driver

### KB-POFA-06 — Hire vehicle keeper documentation  **[GROUP B — genuinely system-authored]**

`REVIEW` · v1.0 · POFA · **strength 88**

**Purpose.** The notice names a leasing company as registered keeper and no hire agreement or statement of liability has been supplied. Currently dormant (REVIEW).

**Trigger:**
- **ALL of:**
  - `keeper_is_hire_firm` is true
  - `hire_docs_supplied` == false

**Shut off when:**
- **ANY of:**
  - `pofa_route` == "NOT_APPLICABLE"
  - `driver_status` == "FORMALLY_IDENTIFIED"

**Required facts:** `keeper_is_hire_firm`, `hire_docs_supplied`

**Proposition.** Where the registered keeper is a vehicle-hire firm, Schedule 4 imposes additional documentary conditions before keeper liability can transfer; those documents have not been shown to have been supplied.

**Legal basis:** `PoFA-2012-Sch4`

**Block `PP-POFA-008`** — REVIEW, **authored, not in Appendix A**:

> The notice identifies the registered keeper as a vehicle-hire firm. Where Schedule 4 is relied upon against a hire-firm keeper, the operator must also satisfy the hire-specific documentary conditions. Those documents have not been evidenced in the materials supplied with this appeal.

**Block `PP-POFA-006`** — ACTIVE, Appendix A:

> The operator has not established the identity of the driver. As the applicable Schedule 4 requirements have also not been satisfied, liability cannot be transferred to the registered keeper under Schedule 4.

**Block `PP-POFA-007`** — ACTIVE, Appendix A:

> For the verified reasons set out above, the operator has not established keeper liability under Schedule 4 of the Protection of Freedoms Act 2012. The charge should therefore be cancelled as against the registered keeper.

**Guardrails.** State only that the hire-specific documents have not been evidenced. Do not invent which paragraph failed. Never identify a driver.

**Prohibited claims:** the driver was; identity of the driver

### KB-RES-03 — Allocated bay

`ACTIVE` · v1.0 · RESIDENTIAL · **strength 80**

**Purpose.** A resident's lease identifies a specific allocated bay and the charge was issued for parking in it.

**Trigger:**
- **ALL of:**
  - `lease_parking_clause_found` is true
  - `allocated_bay` exists

**Shut off when:**
- never (no shut-off conditions)

**Required facts:** `allocated_bay`

**Proposition.** Analyse the resident's rights in that bay and whether the operator has authority to impose the alleged additional contractual term.

**Legal basis:** `CL-primacy-of-contract`

**Block `AI-RES-003`** — ACTIVE, Appendix A:

> The relevant agreement grants or identifies parking rights connected with the allocated space {{bay_reference}}. The operator must address those pre-existing rights before treating the vehicle as an unauthorised user of that space.

**Guardrails.** Bay reference must come from the lease or plan.

**Prohibited claims:** — none declared

### KB-RES-04 — Derogation from grant

`ACTIVE` · v1.0 · RESIDENTIAL · **strength 65**

**Purpose.** A parking scheme imposed after the lease was granted now prevents the leaseholder from using a parking right the lease grants.

**Trigger:**
- **ALL of:**
  - `lease_parking_clause_found` is true
  - `substantial_interference` is true

**Shut off when:**
- never (no shut-off conditions)

**Required facts:** `lease_parking_clause_found`, `substantial_interference`, `lease_clauses`

**Proposition.** Consider derogation from grant only where the actual grant and later interference support the doctrine.

**Legal basis:** `Lease`, `CL-derogation-from-grant`

**Block `AI-RES-004`** — ACTIVE, Appendix A:

> The uploaded agreement confers a parking benefit. Where the later parking arrangement substantially interferes with the exercise of that granted right, the operator is requested to address the principle that a grantor cannot substantially deprive the grantee of the benefit already conferred. Use only after legal/factual validation.

**Guardrails.** Do not use as boilerplate in every residential appeal. Describe the actual interference, then the principle.

**Prohibited claims:** any parking scheme is a derogation

### KB-RES-05 — Quiet enjoyment

`ACTIVE` · v1.0 · RESIDENTIAL · **strength 45**

**Purpose.** Repeated enforcement activity against a leaseholder's own parking interferes with their use of the property.

**Trigger:**
- **ALL of:**
  - `resident_status` in ["TENANT", "LEASEHOLDER"]
  - `lease_parking_clause_found` is true
  - `enforcement_interference` is true

**Shut off when:**
- never (no shut-off conditions)

**Required facts:** `resident_status`, `enforcement_interference`, `lease_clauses`

**Proposition.** Use only where the contractual wording/facts support a quiet-enjoyment point; it is not a universal answer to a PCN.

**Legal basis:** `Lease`, `CL-quiet-enjoyment`

**Guardrails.** Supporting point only. Do not treat quiet enjoyment as meaning freedom from all parking regulation.

**Prohibited claims:** freedom from all parking regulation

### KB-RES-06 — Permit scheme expressly incorporated

`ACTIVE` · v1.0 · RESIDENTIAL · **strength 60**

**Purpose.** The lease expressly incorporates a permit scheme, so the permit terms are contractual rather than imposed by the operator.

**Trigger:**
- **ALL of:**
  - `lease_parking_clause_found` is true
  - `lease_has_regulations_clause` is true

**Shut off when:**
- never (no shut-off conditions)

**Required facts:** `lease_has_regulations_clause`

**Proposition.** The AI must confront the clause and analyse its scope rather than omitting it. Primacy may be weaker or differently framed.

**Legal basis:** `CL-primacy-of-contract`

**Guardrails.** Mandatory when the clause exists - omission is a validator failure.

**Prohibited claims:** unfettered

### KB-RES-07 — Managing agent / operator authority

`ACTIVE` · v1.0 · RESIDENTIAL · **strength 55**

**Purpose.** The operator's authority derives from a managing agent, and the lease or the Code requires authority traceable to the landholder.

**Trigger:**
- **ALL of:**
  - `resident_status` in ["RESIDENT", "TENANT", "LEASEHOLDER"]
  - `lease_parking_clause_found` is true

**Shut off when:**
- never (no shut-off conditions)

**Required facts:** `resident_status`, `lease_parking_clause_found`, `parking_location`

**Proposition.** Assess whether the managing agent/landowner could grant enforcement rights consistent with the resident's existing agreement.

**Legal basis:** `Lease`, `SCOP-authority`

**Block `PP-LAND-001`** — ACTIVE, Appendix A:

> The operator is requested to establish that it had sufficient authority from the landowner or other entitled party to operate and enforce the parking scheme at the location on the material date.

**Guardrails.** One request, merged with the landowner-authority point so the letter asks once. Never assert that no authority exists.

**Prohibited claims:** no authority exists

### KB-CON-02 — Terms rejected / vehicle left

`ACTIVE` · v1.0 · CONSIDERATION · **strength 75**

**Purpose.** The vehicle entered, the terms were read and not accepted, and it left without parking. The presence is not an accepted contract.

**Trigger:**
- **ALL of:**
  - `no_parking_took_place` is true
  - **NOT:**
    - `payment_made` is true

**Shut off when:**
- `permitted_period_ended` is true

**Required facts:** `no_parking_took_place`, `short_presence_before_acceptance`, `total_recorded_duration_min`

**Proposition.** Analyse whether a parking contract was ever accepted rather than treating all site presence as parking.

**Legal basis:** `CL-contract-formation`

**Block `PP-CON-003`** — ACTIVE, Appendix A:

> Having considered the terms, they were not accepted and the vehicle left. The initial presence should not automatically be treated as an accepted parking contract.

**Block `PP-CON-001`** — ACTIVE, Appendix A:

> The vehicle's recorded presence must be considered in the context of the applicable consideration period. Entry onto controlled land does not, without more, establish immediate acceptance of every parking term or that the entire entry-to-exit interval was a period of parking.

**Guardrails.** The case theory is that no contract was accepted - it cannot be combined with a payment or an overstay account.

**Prohibited claims:** free allowance; entry is never acceptance

### KB-GRACE-02 — Exit congestion / barrier delay

`ACTIVE` · v1.0 · GRACE · **strength 70**

**Purpose.** The customer confirms a queue or barrier delayed exit, so the ANPR exit timestamp records passage at the camera rather than the end of parking.

**Trigger:**
- **ALL of:**
  - `exit_congestion` is true
  - `exit_delay_min` exists

**Shut off when:**
- never (no shut-off conditions)

**Required facts:** `exit_congestion`, `exit_delay_min`, `exit_time`

**Proposition.** An exit-camera timestamp may include non-parking time caused by the process of leaving the site.

**Legal basis:** `CL-evidential-weight`

**Block `PP-GRACE-003`** — ACTIVE, Appendix A:

> The vehicle's departure was affected by verified congestion or delay in exiting. An ANPR exit timestamp records passage at the camera and does not necessarily establish that the vehicle remained parked until that moment.

**Block `PP-GRACE-002`** — ACTIVE, Appendix A:

> The period following the permitted parking session included time reasonably required to return to the vehicle, prepare to leave and exit the controlled land.

**Block `PP-GRACE-004`** — ACTIVE, Appendix A:

> Additional time was reasonably required before the vehicle could leave. The operator should consider the verified circumstances together with the applicable Code requirement rather than relying solely on an automated timestamp comparison.

**Guardrails.** The source gives this record no Code basis - argue what the exit timestamp does and does not prove, not a Code entitlement.

**Prohibited claims:** 10 minutes always

### KB-EV-01 — Independent evidence contradicts allegation

`ACTIVE` · v1.1 · EVIDENCE · **strength 70**

**Purpose.** The customer supplies a dashcam clip, receipt or location record that conflicts with the operator's stated duration or location.

**Trigger:**
- **ALL of:**
  - `independent_evidence_contradicts` is true
  - **ANY of:**
    - evidence `DASHCAM` supplied
    - evidence `PHOTO` supplied
    - evidence `RECEIPT` supplied
    - evidence `LOCATION_RECORD` supplied
    - evidence `BANK_STATEMENT` supplied

**Shut off when:**
- **ANY of:**
  - `parking_validation_status` == "UNKNOWN"

**Required facts:** `independent_evidence_contradicts`

**Proposition.** Give the independent evidence appropriate weight and require the operator to reconcile the contradiction.

**Legal basis:** `CL-evidential-weight`

**Block `PP-ANPR-004`** — ACTIVE, Appendix A:

> Independent evidence demonstrates that the vehicle was not continuously present for the period alleged. The operator must reconcile that evidence with its ANPR sequence.

**Guardrails.** Only describe evidence that is actually uploaded, and only the contradiction it actually shows. A purchase receipt alone is not a contradiction of a validation allegation.

**Prohibited claims:** proves the operator fabricated; shopping receipt proves parking validation

### KB-BAY-01 — Recorded observation does not establish the bay-eligibility element  **[GROUP B — genuinely system-authored]**

`ACTIVE` · v1.5 · BAY · **strength 65**

**Purpose.** A parent-and-child or disabled bay allegation where the notice shows an observation time but nothing evidencing that the bay's conditions of use were not met. GROUP B.

**Trigger:**
- **ALL of:**
  - `restricted_bay_alleged` is true
  - `bay_eligibility_unevidenced` is true

**Shut off when:**
- **ANY of:**
  - `bay_eligibility_evidenced` is true
  - `bay_conditions_met_accounted` is true

**Required facts:** `restricted_bay_alleged`, `bay_eligibility_unevidenced`

**Proposition.** An allegation that a reserved bay was used without meeting its conditions of use turns on who or what was using the bay, which the operator's recorded observation and event times do not themselves establish, so the operator is put to proof of that element with the photographs and records it relies on and their timestamps.

**Legal basis:** `CL-evidential-weight`

**Block `PP-BAY-001`** — ACTIVE, **authored, not in Appendix A**:

> The notice records an observation time of {{observation_time}} and an event time of {{event_time}}. This bay is reserved for a particular class of user, so the alleged contravention turns on who or what was using the bay during the visit. The recorded times evidence the vehicle's presence; they do not themselves establish that the bay's conditions of use were not met. The operator is requested to produce every photograph and record it relies on, with their timestamps, showing that those conditions were not met.

**Guardrails.** State the recorded observation and event times as the operator printed them, and the element the operator must prove. Do not invent occupancy, do not assert the bay's conditions were met, and do not characterise the observation as brief or as an instant. Account contradiction is argued under KB-BAY-02, not here.

**Prohibited claims:** the bay was not reserved; the restriction is unenforceable; inventing that an eligible occupant was present; pasting customer free text; asserting the observation period was too short to be lawful; treating any particular observation duration as legally insufficient

### KB-BAY-02 — Keeper account inconsistent with restricted-bay eligibility premise  **[GROUP B — genuinely system-authored]**

`ACTIVE` · v1.1 · BAY · **strength 65**

**Purpose.** A parent-and-child bay allegation where the customer has explicitly confirmed a child was in the vehicle. Withheld where the system only inferred it. GROUP B.

**Trigger:**
- **ALL of:**
  - `restricted_bay_alleged` is true
  - `account_contradicts_allegation` is true
  - `material_account_proposition` exists

**Shut off when:**
- never (no shut-off conditions)

**Required facts:** `restricted_bay_alleged`, `account_contradicts_allegation`, `material_account_proposition`

**Proposition.** The keeper's professionally restated account is inconsistent with the factual premise that the bay's conditions of use were unmet.

**Legal basis:** `CL-evidential-weight`

**Block `PP-BAY-002`** — ACTIVE, **authored, not in Appendix A**:

> Further, the information available to the registered keeper indicates that {{material_account_proposition}}. That is inconsistent with the factual premise of the allegation that the bay's conditions of use were unmet. The operator is requested to identify the evidence relied upon to conclude otherwise.

**Guardrails.** Incorporate material_account_proposition only. Never paste customer free text. Never invent who was driving. Do not equate child presence with legal entitlement beyond the inconsistency with the allegation's premise. The proposition is restated to the operator as the keeper's own material assertion, so it may only rest on a fact the customer expressly stated in a closed-form answer or confirmed when asked; the system's own reading of their prose is not sufficient and account_contradicts_allegation is not set from one.

**Prohibited claims:** the bay was not reserved; inventing entitlement; pasting customer free text; asserting legal entitlement solely from child presence; asserting a material fact the customer did not expressly state or confirm

### KB-AUTH-01 — Underlying authorisation

`ACTIVE` · v1.0 · AUTHORISATION · **strength 75**

**Purpose.** The customer confirms permission to park was obtained from a resident, employer or landholder.

**Trigger:**
- **ALL of:**
  - `authorisation_source` exists
  - `authorisation_source` != "NONE"

**Shut off when:**
- `lease_parking_clause_found` is true

**Required facts:** `authorisation_source`, `parking_event_date`, `vrm`

**Proposition.** Analyse the underlying permission before treating an admin/display mismatch as absence of authority.

**Legal basis:** `SCOP-permits`, `CL-authorisation`

**Block `PP-AUTH-001`** — ACTIVE, Appendix A:

> The vehicle was authorised to park at the material time. The operator should review the relevant authorisation records and evidence before maintaining an allegation of unauthorised parking.

**Block `PP-AUTH-004`** — ACTIVE, Appendix A:

> Although there was an identified display or registration issue, a valid underlying entitlement to park existed. The operator should distinguish this from a case involving no parking authority.

**Guardrails.** Appendix B - underlying authorisation first, then the admin/display issue. Residential rights supersede this.

**Prohibited claims:** automatically cancels

### KB-AUTH-02 — Permit held / digital permit

`ACTIVE` · v1.1 · AUTHORISATION · **strength 70**

**Purpose.** A permit was held but a digital or display problem meant it was not recorded by the operator.

**Trigger:**
- `permit_held` is true

**Shut off when:**
- `lease_parking_clause_found` is true

**Required facts:** `permit_held`

**Proposition.** Require operator to check physical/digital permit and whitelist records; distinguish entitlement from display/registration administration.

**Legal basis:** `SCOP-permits`

**Block `PP-AUTH-002`** — ACTIVE, Appendix A:

> A valid parking permit existed in connection with the vehicle's use of the location. The existence and scope of that underlying entitlement must be considered.

**Block `PP-AUTH-004`** — ACTIVE, Appendix A:

> Although there was an identified display or registration issue, a valid underlying entitlement to park existed. The operator should distinguish this from a case involving no parking authority.

**Guardrails.** Require the operator to check physical/digital permit and whitelist records. Residential rights supersede generic permit logic.

**Prohibited claims:** automatically cancels

### KB-AUTH-03 — Visitor authorisation

`ACTIVE` · v1.0 · AUTHORISATION · **strength 72**

**Purpose.** The vehicle was present as a visitor with a resident's or host's permission.

**Trigger:**
- `visitor_authorised` is true

**Shut off when:**
- never (no shut-off conditions)

**Required facts:** `visitor_authorised`, `parking_event_date`

**Proposition.** Analyse visitor permission and any registration process; do not assume a registration error extinguished underlying permission.

**Legal basis:** `SCOP-permits`, `CL-authorisation`

**Block `PP-AUTH-006`** — ACTIVE, Appendix A:

> The vehicle was present as an authorised visitor. The operator should review visitor records and the evidence of permission.

**Block `PP-AUTH-004`** — ACTIVE, Appendix A:

> Although there was an identified display or registration issue, a valid underlying entitlement to park existed. The operator should distinguish this from a case involving no parking authority.

**Guardrails.** Refer to host/resident confirmation only where it has actually been supplied.

**Prohibited claims:** registration error extinguished the permission

### KB-CUST-01 — Genuine customer

`ACTIVE` · v1.0 · CUSTOMER · **strength 55**

**Purpose.** A customer-only site where the visit was connected with genuine use of the premises.

**Trigger:**
- **ALL of:**
  - `genuine_customer` is true
  - `customer_only_site` is true
  - **ANY of:**
    - evidence `RECEIPT` supplied
    - evidence `BANK_STATEMENT` supplied

**Shut off when:**
- never (no shut-off conditions)

**Required facts:** `genuine_customer`, `customer_only_site`, `parking_location`

**Proposition.** Use as a factual/authorisation route where relevant, not as an automatic cancellation rule.

**Legal basis:** `CL-authorisation`

**Block `PP-AUTH-001`** — ACTIVE, Appendix A:

> The vehicle was authorised to park at the material time. The operator should review the relevant authorisation records and evidence before maintaining an allegation of unauthorised parking.

**Guardrails.** Use where site terms, policy or landholder authorisation make it relevant. Never present it as a rule that the charge must be cancelled.

**Prohibited claims:** automatic cancellation; customers are always exempt

### KB-ACT-01 — Loading / unloading

`ACTIVE` · v1.0 · ACTIVITY · **strength 60**

**Purpose.** The vehicle was stationary for loading or unloading goods rather than parked.

**Trigger:**
- **ALL of:**
  - `loading_activity` is true
  - `alleged_breach` exists

**Shut off when:**
- never (no shut-off conditions)

**Required facts:** `loading_activity`, `alleged_breach`

**Proposition.** Distinguish the activity from ordinary parking where the contract/site rights recognise the distinction; analyse actual terms and evidence.

**Legal basis:** `CL-contract-formation`

**Block `AI-ACT-001`** — ACTIVE, Appendix A:

> The vehicle's presence was connected with genuine loading/unloading activity. The operator should assess the actual site terms and the factual distinction between temporary stopping for that activity and ordinary parking, rather than assuming that every stationary period is parking.

**Guardrails.** Do not assume loading is always exempt on private land - the argument is about the site terms actually in force.

**Prohibited claims:** loading is always exempt; automatic exemption

### KB-ACT-02 — Passenger drop-off / pick-up

`ACTIVE` · v1.0 · ACTIVITY · **strength 55**

**Purpose.** The vehicle stopped only to drop off or collect a passenger.

**Trigger:**
- **ALL of:**
  - `dropoff_activity` is true
  - **NOT:**
    - `permitted_period_ended` is true

**Shut off when:**
- `payment_made` is true

**Required facts:** `dropoff_activity`, `alleged_breach`, `total_recorded_duration_min`

**Proposition.** Analyse the precise site restriction and whether stopping/parking/contract formation is established; do not assume a universal exemption.

**Legal basis:** `CL-contract-formation`

**Block `PP-CON-001`** — ACTIVE, Appendix A:

> The vehicle's recorded presence must be considered in the context of the applicable consideration period. Entry onto controlled land does not, without more, establish immediate acceptance of every parking term or that the entire entry-to-exit interval was a period of parking.

**Guardrails.** No numeric threshold is invented - argue from the site restriction and the recorded duration actually derived.

**Prohibited claims:** stopping is never parking; universal exemption

### KB-ACT-03 — Hotel / restaurant / collection delay

`ACTIVE` · v1.0 · ACTIVITY · **strength 55**

**Purpose.** A hotel check-in, restaurant or collection process extended the time on site.

**Trigger:**
- **ALL of:**
  - `collection_or_checkin_delay` is true
  - **ANY of:**
    - `genuine_customer` is true
    - **ALL of:**
      - `authorisation_source` exists
      - `authorisation_source` != "NONE"

**Shut off when:**
- never (no shut-off conditions)

**Required facts:** `collection_or_checkin_delay`

**Proposition.** Use the underlying authorisation and factual delay where relevant; do not convert ordinary waiting into an automatic exemption.

**Legal basis:** `CL-authorisation`

**Block `PP-AUTH-001`** — ACTIVE, Appendix A:

> The vehicle was authorised to park at the material time. The operator should review the relevant authorisation records and evidence before maintaining an allegation of unauthorised parking.

**Guardrails.** The authorisation carries the point; the delay is context.

**Prohibited claims:** waiting is automatically exempt

### KB-EVCH-01 — EV charging

`ACTIVE` · v1.0 · EV_CHARGING · **strength 60**

**Purpose.** The vehicle was connected to an EV charger, so the charging terms are distinct from any separate parking terms.

**Trigger:**
- **ALL of:**
  - `ev_charging_session` is true
  - `alleged_breach` exists

**Shut off when:**
- never (no shut-off conditions)

**Required facts:** `ev_charging_session`, `alleged_breach`

**Proposition.** Analyse charging terms separately from ordinary parking terms and establish whether the charge concerned parking, charging, overstay or tariff.

**Legal basis:** `CL-contract-formation`

**Block `AI-EVCH-001`** — ACTIVE, Appendix A:

> The vehicle's presence was connected with a genuine charging session. The operator should distinguish the charging terms from any separate parking terms and reconcile the charge-session records with the allegation.

**Guardrails.** Ask the operator to reconcile the charge-session record with the allegation before arguing the parking terms.

**Prohibited claims:** charging bays are not parking bays

### KB-INFRA-01 — Barrier / access system failure

`ACTIVE` · v1.0 · INFRASTRUCTURE · **strength 65**

**Purpose.** A barrier, gate or access-system fault affected entry, exit or compliance.

**Trigger:**
- `barrier_or_access_failure` is true

**Shut off when:**
- never (no shut-off conditions)

**Required facts:** `barrier_or_access_failure`

**Proposition.** Time caused by operator/landholder infrastructure should be distinguished from voluntary parking where supported.

**Legal basis:** `CL-evidential-weight`

**Block `PP-GRACE-003`** — ACTIVE, Appendix A:

> The vehicle's departure was affected by verified congestion or delay in exiting. An ANPR exit timestamp records passage at the camera and does not necessarily establish that the vehicle remained parked until that moment.

**Block `PP-GRACE-004`** — ACTIVE, Appendix A:

> Additional time was reasonably required before the vehicle could leave. The operator should consider the verified circumstances together with the applicable Code requirement rather than relying solely on an automated timestamp comparison.

**Guardrails.** Describe only the fault reported; a timestamp records passage at the camera, not a decision to remain.

**Prohibited claims:** the site was unusable

### KB-LAND-01 — Authority to operate

`ACTIVE` · v1.1 · LANDOWNER · **strength 20**

**Purpose.** Only where Case Intelligence positively selects it — never as filler. Strength 20 keeps it from ever leading.

**Trigger:**
- `authority_challenge_proportionate` is true

**Shut off when:**
- never (no shut-off conditions)

**Required facts:** —

**Proposition.** Require evidence that the operator had sufficient authority to operate/enforce at the specific site and material date.

**Legal basis:** `SCOP-authority`

**Block `PP-LAND-001`** — ACTIVE, Appendix A:

> The operator is requested to establish that it had sufficient authority from the landowner or other entitled party to operate and enforce the parking scheme at the location on the material date.

**Guardrails.** One concise sentence at initial appeal when selected. Never assert no authority exists merely because the customer has not seen the contract. May coexist with fact-specific grounds when CI judged it proportionate.

**Prohibited claims:** no authority exists

### KB-REC-01 — Proportionate records request  **[GROUP B — genuinely system-authored]**

`ACTIVE` · v1.2 · RECORDS · **strength 55**

**Purpose.** A validation, permit or payment mechanism is genuinely material on the confirmed facts — not merely mentioned in the allegation. GROUP B.

**Trigger:**
- **ALL of:**
  - `validation_mechanism_alleged` is true
  - `validation_mechanism_material` is true

**Shut off when:**
- never (no shut-off conditions)

**Required facts:** `alleged_breach`, `validation_mechanism_material`

**Proposition.** Where the allegation turns on a site validation, voucher, kiosk or similar step and the keeper's materials do not prove that step either way, request the operator's relevant system records without asserting that compliance occurred or that a defect is already proven.


**Legal basis:** `SCOP-evidence`

**Block `PP-REC-001`** — ACTIVE, **authored, not in Appendix A**:

> The allegation turns on whether a required validation, authorisation or payment step was completed at the site. The materials available to the keeper do not establish that step one way or the other. The operator is requested to review its validation, kiosk, permit and transaction records for the vehicle, location and material date, and to explain the specific records relied upon before maintaining the charge.

**Guardrails.** Evidence request only. Never claim validation occurred, never claim the charge is invalid solely because records were not produced in advance, and never treat a shopping receipt as parking validation.


**Prohibited claims:** validation occurred; validation failed; the charge is automatically invalid; shopping receipt proves parking validation; requesting records merely because the allegation mentions a validation or permit mechanism

### KB-LAND-02 — Scope / site boundary / material date

`ACTIVE` · v1.0 · LANDOWNER · **strength 45**

**Purpose.** The authority evidence supplied relates to a different site boundary or a date outside the material period.

**Trigger:**
- `authority_mismatch_identified` is true

**Shut off when:**
- never (no shut-off conditions)

**Required facts:** `authority_mismatch_identified`, `parking_location`, `parking_event_date`

**Proposition.** Focus on the specific mismatch: land boundary, term, expiry, cancellation powers or enforcement scope.

**Legal basis:** `SCOP-authority`

**Block `PP-LAND-002`** — ACTIVE, Appendix A:

> Any authority relied upon should cover the specific location and material date. Evidence for another site or period does not establish authority for this PCN.

**Block `PP-LAND-005`** — ACTIVE, Appendix A:

> The operator must establish that the authority relied upon covers the specific land on which the alleged event occurred.

**Block `PP-LAND-006`** — ACTIVE, Appendix A:

> Any evidence of authority must show that the operator's authority was in force on {{parking_event_date}}.

**Guardrails.** Name the mismatch only. Concise at initial operator stage (section 16 priority 4).

**Prohibited claims:** no authority exists

### KB-LAND-03 — Redacted authority evidence

`ACTIVE` · v1.0 · LANDOWNER · **strength 40**

**Purpose.** The operator's authority evidence has been supplied with material parts obscured or redacted.

**Trigger:**
- `authority_evidence_redacted` is true

**Shut off when:**
- never (no shut-off conditions)

**Required facts:** `authority_evidence_redacted`

**Proposition.** Challenge only redactions that prevent verification of parties, land, duration or enforcement scope.

**Legal basis:** `SCOP-authority`

**Block `PP-LAND-008`** — ACTIVE, Appendix A:

> Where authority evidence is supplied but redactions prevent verification of parties, land, period or enforcement scope, the operator should provide sufficient evidence to establish those matters.

**Guardrails.** Redaction of commercial terms is not a ground; only redaction that blocks verification of those four matters is.

**Prohibited claims:** the redactions prove there is no authority
