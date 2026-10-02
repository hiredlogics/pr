# KB-POFA-06 v2.0 — amended proposition for approval

**Status: DRAFTED, NOT ACTIVATED.** Module remains `status: REVIEW` and block
`PP-POFA-008` remains `status: REVIEW` pending your approval of the wording below.

---

## 1. What changed and why

Your direction: the v1.0 module was framed too narrowly around the *registered
keeper being the hire firm*, and must deal properly with hire/lease cases
**including where the customer is the hirer or lessee who received the notice**.

The v1.0 defect, precisely stated: Schedule 4 treats these as **two different
routes**, and v1.0 only addressed the first.

| | Who is pursued | Statutory hook |
|---|---|---|
| Route A | The hire firm, as registered keeper | para 13 — the firm's escape route |
| Route B | The **hirer**, as if they were the keeper | para 14 — the conditions for transferring liability to the hirer |

v1.0 asserted a documentary failure against a hire-firm keeper. But in the
common real case the customer **is** the hirer: the firm has already named them
to the operator, and the operator pursues them under para 14. In that case v1.0
was silent — it would not activate at all, because `keeper_is_hire_firm` is read
off the notice naming the firm, not off the customer's own position.

---

## 2. Statutory basis (Schedule 4, PoFA 2012)

**Paragraph 13** — where the vehicle was hired under a hire agreement and the
hirer had signed a statement of liability, the creditor may not pursue the
hire firm as keeper if, within the period for response, the firm gives the
creditor: (a) a statement that the vehicle was hired under a hire agreement
for a period including the material time, (b) a copy of that hire agreement,
and (c) a copy of the hirer's statement of liability.

**Paragraph 14** — the creditor may recover from the **hirer** as if the hirer
were the keeper only if the conditions in para 13 have been met (i.e. those
documents were in fact provided) **and** the notice to the hirer is given in
the form and within the time Schedule 4 requires, accompanied by copies of the
hire agreement and the statement of liability.

**The appeal point in Route B is therefore narrow and factual:** para 14
liability depends on documents the *operator* must have received and served. A
hirer who has not been given copies of the hire agreement and their own signed
statement of liability with the notice has not been shown to be liable under
para 14. That is an evidential challenge, not an allegation of wrongdoing.

---

## 3. Proposed amended wording

### 3a. Module `core_proposition` (KB-POFA-06 v2.0)

> Where Schedule 4 is relied upon in respect of a hired or leased vehicle, it
> imposes hire-specific documentary conditions before liability may be
> transferred — to the hire firm as keeper under paragraph 13, or to the hirer
> as if they were the keeper under paragraph 14. The materials supplied do not
> establish that those conditions have been met, and the operator is put to
> proof of them.

### 3b. Block `PP-POFA-008` — Route A (hire-firm keeper)

> The notice identifies the registered keeper as a vehicle-hire or leasing
> firm. Where Schedule 4 to the Protection of Freedoms Act 2012 is relied upon
> in respect of a hired vehicle, paragraph 13 of that Schedule imposes
> documentary conditions governing the transfer of liability, and paragraph 14
> permits recovery from a hirer as if the hirer were the keeper only where
> those conditions have been satisfied. The materials supplied with this
> appeal do not evidence that those conditions have been met. The operator is
> requested to confirm which paragraph of Schedule 4 it relies upon and to
> produce the documents on which it relies.

### 3c. Block `PP-POFA-009` — Route B (customer is the hirer/lessee) — NEW

> This notice has been issued in respect of a vehicle held under a hire or
> lease agreement, and has been addressed to the hirer rather than to the
> registered keeper. Paragraph 14 of Schedule 4 to the Protection of Freedoms
> Act 2012 permits a creditor to recover from a hirer as if the hirer were the
> keeper only where the conditions of paragraph 13 have been met and the
> notice served on the hirer complies with the requirements of that Schedule,
> including the service of a copy of the hire agreement and of the hirer's
> statement of liability. The documents served with this notice do not include
> those copies. The operator is requested to produce them, and to confirm the
> date on which the hire firm provided the documents required by paragraph 13,
> before maintaining the charge against the hirer.

---

## 4. Trigger (revised)

Consistent with your wider V2 rule — a module activates on **case facts**, not
on a keyword — this needs a fact establishing the customer's own position, which
does not currently exist in the fact vocabulary.

**Route A** (unchanged shape, documented basis):

```yaml
use_when:
  all: [{is: keeper_is_hire_firm}, {eq: [hire_docs_supplied, false]}]
```

**Route B** (new) requires a new fact `customer_is_hirer`, set only from a
closed question or an uploaded hire agreement naming the customer — never
inferred from prose:

```yaml
use_when:
  all:
    - is: customer_is_hirer
    - eq: [hire_para14_docs_served, false]
```

Proposed new question (`questions.yaml`):

> `customer_is_hirer`: "Was the vehicle hired or leased, with the agreement in
> your name?" (bool)

> `hire_para14_docs_served`: "Did the operator send you a copy of the hire
> agreement and the statement of liability you signed?" (bool)

Both `do_not_use_when` guards from v1.0 are retained:
`pofa_route == NOT_APPLICABLE`, and `driver_status == FORMALLY_IDENTIFIED`.

---

## 5. Guardrails (how the two constraints you set are enforced)

**"It must not invent a missing document or statutory failure that has not
actually been established."**

The propositions above assert only that the documents **are not present in the
materials supplied** and put the operator to proof. They do not assert that the
documents do not exist, that the hire firm failed to provide them, or that any
paragraph has been breached. The negative facts are therefore false-by-default
rather than true-by-default: `hire_para14_docs_served` is only `false` when the
customer has positively answered that the copies were not served. An unanswered
question leaves the module shut.

Added to `prohibited_claims`:
- `the operator has breached paragraph 13`
- `the operator has breached paragraph 14`
- `no hire agreement exists`
- `the hire company failed to`
- `liability has not transferred` (a conclusion, not an evidential point)

**"It must continue not to identify the driver."**

Route B says the notice was *addressed to the hirer*. That is the keeper-side
position the operator itself asserted; it does not state who was driving. The
existing `prohibited_claims` (`the driver was`, `identity of the driver`) are
retained, and the `driver_status == FORMALLY_IDENTIFIED` guard still shuts the
module where identification has already occurred.

---

## 6. Governance note

`legal_basis_origin` stays `NOT_IN_SOURCE`. The paragraph numbers above are
statutory, but the *propositions* are system-authored and are not in your
Appendix A. Both blocks stay `status: REVIEW`, and the module stays
`status: REVIEW`, until you approve this wording — so nothing here can reach a
customer letter in the meantime.

**No numerical thresholds are introduced by this module.** The only period
referenced ("the period for response") is not quantified in the drafted text.

## 7. What I need from you

1. Approve / amend the three wordings in §3.
2. Confirm the two new questions in §4 are acceptable customer-facing wording.
3. Confirm you want Route B at all — it is the larger change, and it introduces
   two new facts and one new block.

On approval I will set the module and both blocks to `ACTIVE`, add the facts and
questions, and write the regression tests (including a case proving an
unanswered hire question leaves the module shut).
