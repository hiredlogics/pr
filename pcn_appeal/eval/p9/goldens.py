"""P9 v1 golden definitions.

COMPLETE cases use document-visible fields and independently approved legal
expectations. Journey snapshot `approved` lists are NOT copied as client
ground truth.

NOTICE_ONLY cases are converted from datasets/pcn_finetune_v1. Their
`suggested_modules` and synthetic gold appeals are reference-only.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from . import DATASET_VERSION
from .schema import (
    BLIND_HOLDOUT, COMPLETE, DEVELOPMENT, DEVELOPMENT_APPROVED, NOTICE_ONLY,
    REFERENCE_ONLY, UNOBSERVED, VALIDATION, AppealRequirements, ExpectedLayer,
    GoldenCase, GoldenExpected, GoldenInput, layer,
)

ROOT = Path(__file__).resolve().parents[3]
FINETUNE = ROOT / "datasets" / "pcn_finetune_v1"

ACME_BACK = """HOW TO APPEAL
If you were not the driver, you may pass this notice to the driver or
name them. We are seeking recovery from the keeper under Schedule 4 of
the Protection of Freedoms Act 2012 if the driver is not identified.
If your appeal to us is rejected you may appeal to the independent
appeals service (POPLA) within 28 days using the code we will give you.
Pay or appeal within 28 days of the date of issue.
"""

OVERSTAY_FRONT = """PARKING CHARGE NOTICE
Operator Name: Acme Parking Ltd
PCN Number: PCN778899
Vehicle Registration: KX19 PLT
Location: Riverside Retail Park
Postcode: M1 4BT
Date of Contravention: 12/06/2026
Date of Issue: 02/07/2026
Entry Time: 14:05
Exit Time: 16:58
Charge: 100
Alleged Breach: Overstayed the maximum permitted period
Trade Association: BPA
"""

OVERSTAY_FIELDS = {
    "operator_name": "Acme Parking Ltd",
    "pcn_number": "PCN778899",
    "vrm": "KX19 PLT",
    "parking_location": "Riverside Retail Park",
    "site_postcode": "M1 4BT",
    "parking_event_date": "12/06/2026",
    "notice_issue_date": "02/07/2026",
    "entry_time": "14:05",
    "exit_time": "16:58",
    "charge_amount": "100",
    "alleged_breach": "Overstayed the maximum permitted period",
    "operator_ata": "BPA",
    "notice_route": "POSTAL",
    "notice_type": "NTK",
}

LATE_FRONT = """PARKING CHARGE NOTICE
Operator Name: Acme Parking Ltd
PCN Number: PCN123456
Vehicle Registration: AB12 CDE
Location: Retail Park
Postcode: M1 1AA
Date of Contravention: 01/06/2026
Date of Issue: 20/06/2026
Entry Time: 10:00
Exit Time: 12:47
Charge: £100
Alleged Breach: Overstayed paid time
Trade Association: BPA
"""

LATE_FIELDS = {
    "operator_name": "Acme Parking Ltd",
    "pcn_number": "PCN123456",
    "vrm": "AB12 CDE",
    "parking_location": "Retail Park",
    "site_postcode": "M1 1AA",
    "parking_event_date": "01/06/2026",
    "notice_issue_date": "20/06/2026",
    "entry_time": "10:00",
    "exit_time": "12:47",
    "charge_amount": "£100",
    "alleged_breach": "Overstayed paid time",
    "operator_ata": "BPA",
    "notice_route": "POSTAL",
    "notice_type": "NTK",
}

PAYMENT_FIELDS = dict(LATE_FIELDS, alleged_breach="No valid payment for vehicle",
                      notice_issue_date="05/06/2026")

BREAKDOWN_FIELDS = dict(LATE_FIELDS, alleged_breach="Overstayed paid time",
                        notice_issue_date="05/06/2026")

RESIDENTIAL_FIELDS = dict(LATE_FIELDS, alleged_breach="No valid permit displayed",
                          notice_issue_date="05/06/2026")

LEASE = (
    "1. Definitions apply.\n"
    "3.2 The Tenant shall have the right to park one private motor vehicle in the parking space "
    "numbered 14 shown on the plan.\n4. Rent is payable monthly."
)

GOV_DEV = {
    "approved_by": "development_test_suite",
    "approved_at": "2026-10-04",
    "reason": "Independent document-visible fields and/or pre-existing scenario assertions. "
              "Not a client-signed release of final appeal wording.",
}


def _extraction(fields: dict) -> ExpectedLayer:
    return layer(True, DEVELOPMENT_APPROVED, dict(fields))


def _facts_from(fields: dict, names: tuple) -> ExpectedLayer:
    return layer(True, DEVELOPMENT_APPROVED,
                 [{"name": n, "value": fields[n], "source": "DOCUMENT",
                   "kind": "document"} for n in names if fields.get(n)])


_DOC_FACT_NAMES = (
    "operator_name", "pcn_number", "vrm", "parking_event_date",
    "notice_issue_date", "entry_time", "exit_time", "parking_location",
    "site_postcode", "alleged_breach",
)


def _pcn_input(front: str, fields: dict, narrative: str = "", answers: dict | None = None,
               policy: str = "skip", extra_evidence: list | None = None,
               corrections: dict | None = None, doc_types: dict | None = None) -> GoldenInput:
    types = {"E1": "PCN"}
    types.update(doc_types or {})
    return GoldenInput(
        notice_front=front.strip(),
        notice_back=ACME_BACK.strip(),
        customer_narrative=narrative,
        customer_answers=dict(answers or {}),
        answer_policy=policy,
        evidence=list(extra_evidence or []),
        corrections=dict(corrections or {}),
        extraction_fields=dict(fields),
        doc_types=types,
    )


def _complete(case_id: str, split: str, *, family: str, operator: str, allegation: str,
              legal_route: str, circumstance: str, evidence_type: str, inp: GoldenInput,
              expected: GoldenExpected, notes: str = "", paired_with: str | None = None,
              approval: str = DEVELOPMENT_APPROVED) -> GoldenCase:
    return GoldenCase(
        case_id=case_id,
        dataset_version=DATASET_VERSION,
        completeness=COMPLETE,
        split=split,
        approval_status=approval,
        family=family,
        operator=operator,
        allegation=allegation,
        legal_route=legal_route,
        circumstance=circumstance,
        evidence_type=evidence_type,
        notes=notes,
        paired_with=paired_with,
        input=inp,
        expected=expected,
        governance=dict(GOV_DEV),
    )


def complete_cases() -> list[GoldenCase]:
    late_findings = layer(True, DEVELOPMENT_APPROVED, [
        {"finding_type": "POFA_POSTAL_LATE", "status": "VERIFIED",
         "requires_lineage": True},
    ])
    late_grounds = layer(True, DEVELOPMENT_APPROVED, ["KB-POFA-02"])
    late_outcome = layer(True, DEVELOPMENT_APPROVED, {
        "state": "RELEASED", "letter_must_not_be_empty": True,
    })
    late_appeal = layer(True, DEVELOPMENT_APPROVED, asdict_req(
        AppealRequirements(
            must_express=["not delivered within"],
            must_not_express=["I parked", "I drove"],
        )))

    late = _complete(
        "REG_late_ntk", DEVELOPMENT,
        family="late_postal_ntk", operator="Acme Parking Ltd",
        allegation="overstay", legal_route="POFA",
        circumstance="none", evidence_type="notice_only",
        paired_with="REG_late_ntk_anpr",
        notes="Independent PoFA postal-late calculation: event 01/06/2026, "
              "issue 20/06/2026. Additive-pair A (notice + thin narrative).",
        inp=_pcn_input(LATE_FRONT, LATE_FIELDS, "Got a letter weeks later"),
        expected=GoldenExpected(
            classification=layer(True, DEVELOPMENT_APPROVED, {"document_class": "PCN"}),
            extraction=_extraction(LATE_FIELDS),
            facts=_facts_from(LATE_FIELDS, _DOC_FACT_NAMES),
            derived_facts=layer(False, UNOBSERVED),
            questions=layer(False, UNOBSERVED),
            legal_findings=late_findings,
            knowledge_matches=layer(True, DEVELOPMENT_APPROVED, {
                "required": ["KB-POFA-02"],
            }),
            supported_grounds=late_grounds,
            claim_plan=layer(True, DEVELOPMENT_APPROVED, {
                "must_include": ["KB-POFA-02"],
                "origin": {"KB-POFA-02": "VERIFIED_FINDING"},
            }),
            required_particulars=layer(True, DEVELOPMENT_APPROVED, {
                "KB-POFA-02": ["parking_event_date", "notice_issue_date"],
            }),
            final_outcome=late_outcome,
            final_appeal_requirements=late_appeal,
            ground_origins={"KB-POFA-02": "verified_finding"},
        ),
    )

    late_anpr = _complete(
        "REG_late_ntk_anpr", DEVELOPMENT,
        family="late_postal_ntk", operator="Acme Parking Ltd",
        allegation="overstay", legal_route="POFA+ANPR",
        circumstance="multiple_visits", evidence_type="notice_plus_narrative",
        paired_with="REG_late_ntk",
        notes="Additive-pair B: same notice plus customer multiple-visit account. "
              "KB-POFA-02 must remain. KB-ANPR-01 is development-approved only.",
        inp=_pcn_input(
            LATE_FRONT, dict(LATE_FIELDS, entry_time="10:00", exit_time="13:27"),
            "I visited the car park twice that day. The cameras have joined "
            "two separate stays into one overstay.",
            answers={"multiple_visits": "yes"}, policy="skip",
        ),
        expected=GoldenExpected(
            classification=layer(True, DEVELOPMENT_APPROVED, {"document_class": "PCN"}),
            extraction=_extraction(dict(LATE_FIELDS, exit_time="13:27")),
            facts=_facts_from(dict(LATE_FIELDS, exit_time="13:27"), _DOC_FACT_NAMES),
            derived_facts=layer(True, DEVELOPMENT_APPROVED, [
                {"name": "multiple_visits", "value": True, "source": "ANSWER",
                 "kind": "customer_confirmed"},
            ]),
            questions=layer(False, UNOBSERVED),
            legal_findings=late_findings,
            knowledge_matches=layer(True, DEVELOPMENT_APPROVED, {
                "required": ["KB-POFA-02"],
            }),
            supported_grounds=layer(True, DEVELOPMENT_APPROVED,
                                    ["KB-POFA-02", "KB-ANPR-01"]),
            claim_plan=layer(True, DEVELOPMENT_APPROVED, {
                "must_include": ["KB-POFA-02", "KB-ANPR-01"],
                "origin": {"KB-POFA-02": "VERIFIED_FINDING"},
            }),
            required_particulars=layer(True, DEVELOPMENT_APPROVED, {
                "KB-POFA-02": ["parking_event_date", "notice_issue_date"],
            }),
            final_outcome=late_outcome,
            final_appeal_requirements=layer(True, DEVELOPMENT_APPROVED, asdict_req(
                AppealRequirements(
                    must_express=["not delivered within"],
                    must_not_express=["I parked", "I drove"],
                ))),
            ground_origins={"KB-POFA-02": "verified_finding",
                            "KB-ANPR-01": "narrative"},
        ),
    )

    overstay_notes = (
        "Same Acme overstay notice family. Expected grounds are UNOBSERVED: "
        "journey golden snapshots are regression hashes, not client-approved "
        "supported grounds. PoFA late is not asserted — dates were not "
        "independently signed off for this case."
    )
    overstay_common = dict(
        family="acme_overstay", operator="Acme Parking Ltd",
        allegation="overstay", legal_route="unobserved",
        evidence_type="notice_plus_narrative",
    )
    skip = _complete(
        "REG_overstay_skip_all", DEVELOPMENT, **overstay_common,
        circumstance="skip_all_questions",
        notes=overstay_notes,
        inp=_pcn_input(OVERSTAY_FRONT, OVERSTAY_FIELDS,
                       "the letter only arrived weeks later and there was a queue at the barrier",
                       policy="skip"),
        expected=_notice_only_extraction_expected(OVERSTAY_FIELDS, COMPLETE),
    )
    yes_all = _complete(
        "REG_overstay_yes_all", DEVELOPMENT, **overstay_common,
        circumstance="answer_yes_all",
        notes=overstay_notes + " Narrative mentions two visits and hidden signs.",
        inp=_pcn_input(
            OVERSTAY_FRONT, OVERSTAY_FIELDS,
            "I went in twice that day, once in the afternoon and once in the evening. "
            "The signs were behind a van.",
            policy="yes"),
        expected=_notice_only_extraction_expected(OVERSTAY_FIELDS, COMPLETE),
    )
    dont_know = _complete(
        "REG_overstay_dont_know", DEVELOPMENT, **overstay_common,
        circumstance="dont_know_answers",
        notes=overstay_notes + " 'I don't know' must not become a directional fact.",
        inp=_pcn_input(
            OVERSTAY_FRONT, OVERSTAY_FIELDS,
            "I was not the driver that day and I cannot say who was.",
            policy="dont_know"),
        expected=_dont_know_expected(OVERSTAY_FIELDS),
    )

    payment_receipt = _complete(
        "REG_notice_plus_payment", DEVELOPMENT,
        family="overstay_plus_receipt", operator="Acme Parking Ltd",
        allegation="overstay", legal_route="unobserved",
        circumstance="partial_payment_receipt",
        evidence_type="notice_plus_receipt",
        notes="Receipt is evidence, not an approved ground. Grounds UNOBSERVED.",
        inp=_pcn_input(
            OVERSTAY_FRONT, OVERSTAY_FIELDS,
            "I paid at the machine when I arrived. I did not realise the time had run out.",
            policy="no",
            extra_evidence=[{
                "evidence_id": "E2", "filename": "receipt.txt", "kind": "OTHER",
                "doc_type": "OTHER",
                "text": "Riverside Retail Park - Pay and Display\n"
                        "Ticket 00412  12/06/2026 14:10\nPaid: 2.00  Expires: 16:10",
            }],
            doc_types={"E2": "OTHER"},
        ),
        expected=_notice_only_extraction_expected(OVERSTAY_FIELDS, COMPLETE),
    )

    keying = _complete(
        "REG_payment_keying", VALIDATION,
        family="payment_keying", operator="Acme Parking Ltd",
        allegation="no_valid_payment", legal_route="PAYMENT+KEYING",
        circumstance="app_typo", evidence_type="notice_plus_app_screenshot",
        notes="Development-approved from scenario suite C. Not client-signed wording.",
        inp=_pcn_input(
            _front_from(PAYMENT_FIELDS), PAYMENT_FIELDS,
            "I paid on the app but typo in reg",
            answers={"payment_made": "yes", "payment_method": "APP",
                     "keying_error_type": "MINOR"},
            extra_evidence=[{
                "evidence_id": "E4", "filename": "app.png", "kind": "APP_SCREENSHOT",
                "doc_type": "APP_SCREENSHOT", "text": "Payment confirmed for AB12 CDF",
            }],
            doc_types={"E4": "APP_SCREENSHOT"},
        ),
        expected=GoldenExpected(
            classification=layer(True, DEVELOPMENT_APPROVED, {"document_class": "PCN"}),
            extraction=_extraction(PAYMENT_FIELDS),
            facts=_facts_from(PAYMENT_FIELDS, _DOC_FACT_NAMES),
            derived_facts=layer(True, DEVELOPMENT_APPROVED, [
                {"name": "payment_made", "value": True, "source": "ANSWER",
                 "kind": "customer_confirmed"},
            ]),
            questions=layer(False, UNOBSERVED),
            legal_findings=layer(False, UNOBSERVED),
            knowledge_matches=layer(True, DEVELOPMENT_APPROVED, {
                "required": ["KB-PAY-01", "KB-KEY-01"],
            }),
            supported_grounds=layer(True, DEVELOPMENT_APPROVED,
                                    ["KB-PAY-01", "KB-KEY-01"]),
            claim_plan=layer(True, DEVELOPMENT_APPROVED, {
                "must_include": ["KB-PAY-01", "KB-KEY-01"],
            }),
            final_outcome=layer(True, DEVELOPMENT_APPROVED, {
                "state": "RELEASED", "primary_route": "PAYMENT",
            }),
            final_appeal_requirements=layer(True, DEVELOPMENT_APPROVED, asdict_req(
                AppealRequirements(
                    must_express=["registration-entry error"],
                    must_not_express=["I paid", "I parked", "I drove"],
                ))),
            ground_origins={"KB-PAY-01": "narrative", "KB-KEY-01": "narrative"},
        ),
    )

    no_notice = _complete(
        "REG_no_notice", VALIDATION,
        family="not_a_pcn", operator="unknown",
        allegation="none", legal_route="out_of_scope",
        circumstance="receipt_only", evidence_type="pay_and_display_ticket",
        notes="Not a PCN. Must stop cleanly with no letter and no KB/VAL leak.",
        inp=GoldenInput(
            notice_front="",
            notice_back="",
            customer_narrative="I paid for parking, here is my ticket.",
            answer_policy="skip",
            extraction_fields={},
            doc_types={"E1": "OTHER"},
            evidence=[{
                "evidence_id": "E1", "filename": "receipt.txt", "kind": "OTHER",
                "doc_type": "OTHER",
                "text": "Riverside Retail Park - Pay and Display\n"
                        "Ticket 00412  12/06/2026 14:10\nPaid: 2.00  Expires: 16:10\n"
                        "Thank you for your visit",
            }],
        ),
        expected=GoldenExpected(
            classification=layer(True, DEVELOPMENT_APPROVED, {
                "not_pcn": True,
            }),
            extraction=layer(False, UNOBSERVED),
            facts=layer(False, UNOBSERVED),
            final_outcome=layer(True, DEVELOPMENT_APPROVED, {
                "letter_must_be_empty": True,
                "letter_excludes": ["KB-", "VAL-"],
            }),
            final_appeal_requirements=layer(True, DEVELOPMENT_APPROVED, asdict_req(
                AppealRequirements(must_not_express=["KB-", "VAL-"]))),
        ),
    )

    breakdown = _complete(
        "REG_breakdown", BLIND_HOLDOUT,
        family="breakdown", operator="Acme Parking Ltd",
        allegation="overstay", legal_route="BREAKDOWN",
        circumstance="battery_failure_recovery",
        evidence_type="notice_plus_recovery_report",
        notes="Blind holdout. Different allegation path and evidence type. "
              "Not used to tune prompts, rules, KB, or model parameters.",
        inp=_pcn_input(
            _front_from(BREAKDOWN_FIELDS), BREAKDOWN_FIELDS,
            "The car wouldn't start, battery died, RAC came",
            answers={
                "vehicle_immobilised": "yes",
                "immobilisation_prevented_departure": "yes",
                "recovery_attended": "yes",
                "permitted_period_ended": "yes",
                "exit_delay_min": 47,
            },
            extra_evidence=[{
                "evidence_id": "E2", "filename": "rac.pdf", "kind": "RECOVERY_REPORT",
                "doc_type": "RECOVERY_REPORT", "text": "RAC job — battery replacement",
            }],
            doc_types={"E2": "RECOVERY_REPORT"},
        ),
        expected=GoldenExpected(
            classification=layer(True, DEVELOPMENT_APPROVED, {"document_class": "PCN"}),
            extraction=_extraction(BREAKDOWN_FIELDS),
            facts=_facts_from(BREAKDOWN_FIELDS, _DOC_FACT_NAMES),
            derived_facts=layer(True, DEVELOPMENT_APPROVED, [
                {"name": "vehicle_immobilised", "value": True, "source": "ANSWER",
                 "kind": "customer_confirmed"},
            ]),
            supported_grounds=layer(False, UNOBSERVED),
            knowledge_matches=layer(False, UNOBSERVED),
            final_outcome=layer(True, DEVELOPMENT_APPROVED, {
                "state": "RELEASED", "primary_route": "BREAKDOWN",
            }),
            final_appeal_requirements=layer(True, DEVELOPMENT_APPROVED, asdict_req(
                AppealRequirements(
                    must_express=["mechanically immobilised"],
                    must_not_express=["I parked", "I drove"],
                ))),
            ground_origins={"BREAKDOWN": "evidence"},
        ),
    )

    residential = _complete(
        "REG_residential", BLIND_HOLDOUT,
        family="residential", operator="Acme Parking Ltd",
        allegation="no_permit", legal_route="RESIDENTIAL",
        circumstance="tenant_allocated_bay",
        evidence_type="notice_plus_tenancy",
        notes="Blind holdout. Different allegation, route, and evidence type.",
        inp=_pcn_input(
            _front_from(RESIDENTIAL_FIELDS), RESIDENTIAL_FIELDS,
            "I live there, it's my allocated space",
            answers={"resident_status": "TENANT", "permit_held": "no"},
            corrections={"allocated_bay": "14"},
            extra_evidence=[{
                "evidence_id": "E3", "filename": "tenancy.pdf", "kind": "TENANCY",
                "doc_type": "TENANCY", "text": LEASE,
            }],
            doc_types={"E3": "TENANCY"},
        ),
        expected=GoldenExpected(
            classification=layer(True, DEVELOPMENT_APPROVED, {"document_class": "PCN"}),
            extraction=_extraction(RESIDENTIAL_FIELDS),
            facts=_facts_from(RESIDENTIAL_FIELDS, _DOC_FACT_NAMES),
            supported_grounds=layer(False, UNOBSERVED),
            final_outcome=layer(True, DEVELOPMENT_APPROVED, {
                "state": "RELEASED", "primary_route": "RESIDENTIAL",
            }),
            final_appeal_requirements=layer(True, DEVELOPMENT_APPROVED, asdict_req(
                AppealRequirements(
                    must_express=["parking space numbered 14"],
                    must_not_express=["I parked", "I drove"],
                ))),
            ground_origins={"RESIDENTIAL": "evidence"},
        ),
    )

    return [late, late_anpr, skip, yes_all, dont_know, payment_receipt,
            keying, no_notice, breakdown, residential]


def _front_from(fields: dict) -> str:
    return (
        f"PARKING CHARGE NOTICE\n"
        f"Operator Name: {fields['operator_name']}\n"
        f"PCN Number: {fields['pcn_number']}\n"
        f"Vehicle Registration: {fields['vrm']}\n"
        f"Location: {fields['parking_location']}\n"
        f"Postcode: {fields['site_postcode']}\n"
        f"Date of Contravention: {fields['parking_event_date']}\n"
        f"Date of Issue: {fields['notice_issue_date']}\n"
        f"Entry Time: {fields['entry_time']}\n"
        f"Exit Time: {fields['exit_time']}\n"
        f"Charge: {fields['charge_amount']}\n"
        f"Alleged Breach: {fields['alleged_breach']}\n"
        f"Trade Association: {fields.get('operator_ata') or 'BPA'}\n"
    )


def _notice_only_extraction_expected(fields: dict, completeness: str) -> GoldenExpected:
    return GoldenExpected(
        classification=layer(True, DEVELOPMENT_APPROVED, {"document_class": "PCN"}),
        extraction=_extraction(fields),
        facts=_facts_from(fields, _DOC_FACT_NAMES),
        derived_facts=layer(False, UNOBSERVED),
        questions=layer(False, UNOBSERVED),
        legal_findings=layer(False, UNOBSERVED),
        knowledge_matches=layer(False, UNOBSERVED),
        supported_grounds=layer(False, UNOBSERVED),
        claim_plan=layer(False, UNOBSERVED),
        final_outcome=layer(False, UNOBSERVED),
        final_appeal_requirements=layer(True, DEVELOPMENT_APPROVED, asdict_req(
            AppealRequirements(must_not_express=["I parked", "I drove"]))),
    )


def _dont_know_expected(fields: dict) -> GoldenExpected:
    exp = _notice_only_extraction_expected(fields, COMPLETE)
    exp.questions = layer(True, DEVELOPMENT_APPROVED, {
        "dont_know_must_not_become_fact": True,
    })
    return exp


def asdict_req(req: AppealRequirements) -> dict:
    return {
        "must_express": list(req.must_express),
        "must_not_express": list(req.must_not_express),
        "exact_wording_approved": req.exact_wording_approved,
    }


NOTICE_ONLY_SPLIT = {
    "stn1947529": DEVELOPMENT,          # APCOA Stansted, client exemplar
    "88811908015": DEVELOPMENT,         # Euro / Morrisons
    "70377302": DEVELOPMENT,            # CPM
    "00347261120013": DEVELOPMENT,      # CP Plus NTK (pair with reminder)
    "00347261100014": DEVELOPMENT,      # CP Plus reminder, same VRM family
    "lu3440789": VALIDATION,            # APCOA Luton pair
    "lu3489734": VALIDATION,
    "new1936102153896": BLIND_HOLDOUT,  # ParkMaven
    "sp62712518": BLIND_HOLDOUT,        # Smart Parking
}


def notice_only_cases() -> list[GoldenCase]:
    cases: list[GoldenCase] = []
    manifest_path = FINETUNE / "manifest.json"
    if not manifest_path.exists():
        return cases
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    for row in manifest.get("cases") or []:
        cid = row["id"]
        src = FINETUNE / "cases" / f"{cid}.json"
        if not src.exists():
            continue
        raw = json.loads(src.read_text(encoding="utf-8"))
        facts = dict(raw.get("facts") or {})
        fields = {
            "operator_name": facts.get("operator_name"),
            "pcn_number": facts.get("pcn_number"),
            "vrm": facts.get("vrm"),
            "parking_event_date": _iso_to_uk(facts.get("parking_event_date")),
            "notice_issue_date": _iso_to_uk(facts.get("notice_issue_date")),
            "entry_time": facts.get("entry_time"),
            "exit_time": facts.get("exit_time"),
            "parking_location": facts.get("parking_location"),
            "site_postcode": _postcode_from_location(facts.get("parking_location")),
            "alleged_breach": facts.get("alleged_breach"),
            "charge_amount": facts.get("charge_amount"),
            "notice_route": "POSTAL",
            "notice_type": "REMINDER" if "reminder" in (raw.get("document_type") or "")
            else "NTK",
        }
        fields = {k: v for k, v in fields.items() if v not in (None, "")}
        gold_source = raw.get("gold_source") or row.get("gold_source")
        appeal_layer = ExpectedLayer(evaluate=False, approval=REFERENCE_ONLY, values={
            "gold_source": gold_source,
            "style_only": True,
            "must_not_use_as": [
                "unknown_customer_circumstances",
                "missing_reverse_page_defects",
                "facts_not_visible_on_front",
                "approved_supported_grounds",
            ],
        })
        if gold_source == "client_exemplar":
            appeal_layer = layer(True, REFERENCE_ONLY, asdict_req(
                AppealRequirements(
                    must_express=["registered keeper", "cancelled"],
                    must_not_express=["I parked", "I drove", "I was the driver"],
                )))
        cases.append(GoldenCase(
            case_id=f"REF_{cid}",
            dataset_version=DATASET_VERSION,
            completeness=NOTICE_ONLY,
            split=NOTICE_ONLY_SPLIT.get(cid, DEVELOPMENT),
            approval_status=REFERENCE_ONLY,
            family=raw.get("scenario") or row.get("document_type") or "notice",
            operator=fields.get("operator_name") or "unknown",
            allegation=fields.get("alleged_breach") or "unreadable",
            legal_route="notice_derived_only",
            circumstance="unknown",
            evidence_type="front_notice_photo",
            notes=(
                f"NOTICE_ONLY reference from pcn_finetune_v1/{cid}. "
                f"gold_source={gold_source}. suggested_modules="
                f"{raw.get('suggested_modules')} are NOT approved grounds. "
                "Do not score reverse-page defects or unknown customer facts."
            ),
            input=GoldenInput(
                notice_front=f"[photograph] {row.get('image') or raw.get('image')}",
                notice_back="",
                customer_narrative="",
                answer_policy="skip",
                extraction_fields=fields,
                doc_types={"E1": "PCN"},
                image_path=str(FINETUNE / (row.get("image") or raw.get("image") or "")),
                evidence=[{
                    "evidence_id": "E1",
                    "filename": Path(row.get("image") or f"{cid}.png").name,
                    "kind": "PCN",
                    "doc_type": "PCN",
                    "text": _notice_text_from_labels(fields, raw),
                }],
            ),
            expected=GoldenExpected(
                classification=layer(True, DEVELOPMENT_APPROVED, {
                    "document_class": "PCN",
                    "notice_type": fields.get("notice_type"),
                }),
                extraction=_extraction(fields),
                facts=_facts_from(fields, _DOC_FACT_NAMES),
                derived_facts=layer(False, UNOBSERVED),
                questions=layer(False, UNOBSERVED),
                legal_findings=layer(False, UNOBSERVED),
                knowledge_matches=layer(False, REFERENCE_ONLY, {
                    "suggested_not_approved": list(raw.get("suggested_modules") or []),
                }),
                supported_grounds=layer(False, UNOBSERVED),
                claim_plan=layer(False, UNOBSERVED),
                final_outcome=layer(False, UNOBSERVED),
                final_appeal_requirements=appeal_layer,
            ),
            governance={
                "approved_by": "labeler_from_photograph",
                "approved_at": "2026-10-04",
                "reason": "Front-notice field labels only. Not complete ground truth.",
            },
        ))
    return cases


def _iso_to_uk(value: Any) -> Any:
    if not value or not isinstance(value, str):
        return value
    if len(value) == 10 and value[4] == "-" and value[7] == "-":
        y, m, d = value.split("-")
        return f"{d}/{m}/{y}"
    return value


def _postcode_from_location(location: Any) -> Any:
    if not location or not isinstance(location, str):
        return None
    import re
    m = re.search(r"\b([A-Z]{1,2}\d{1,2}[A-Z]?\s*\d[A-Z]{2})\b", location)
    return m.group(1) if m else None


def _notice_text_from_labels(fields: dict, raw: dict) -> str:
    """Visible-label reconstruction for ingest. Not a second extractor."""
    lines = ["NOTICE TO KEEPER - PARKING CHARGE"]
    for label, key in (
        ("Operator", "operator_name"),
        ("Parking Charge Number", "pcn_number"),
        ("Vehicle Registration", "vrm"),
        ("Location", "parking_location"),
        ("Date of Contravention", "parking_event_date"),
        ("Date Issued", "notice_issue_date"),
        ("Entry Time", "entry_time"),
        ("Exit Time", "exit_time"),
        ("Alleged Breach", "alleged_breach"),
        ("Amount", "charge_amount"),
    ):
        if fields.get(key) not in (None, ""):
            lines.append(f"{label}: {fields[key]}")
    return "\n".join(lines)


def all_cases() -> list[GoldenCase]:
    return complete_cases() + notice_only_cases()
