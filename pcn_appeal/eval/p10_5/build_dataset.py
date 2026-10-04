"""Build p10_5_v1 DEVELOPMENT / VALIDATION / sealed HOLDOUT fixtures."""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
DS = ROOT / "datasets" / "p10_5_v1"

BASE_FIELDS = {
    "operator_name": "Fieldstone Parking Ltd",
    "pcn_number": "FS510000",
    "vrm": "AB12CDE",
    "parking_location": "Fieldstone Retail",
    "site_postcode": "M2 2AA",
    "parking_event_date": "01/09/2026",
    "notice_issue_date": "05/09/2026",
    "alleged_breach": "Overstayed paid time",
    "operator_ata": "BPA",
}


def _case(case_id, split, family, narrative, expected, *,
          answers=None, fields=None, evidence=None, sealed=False, notes=""):
    f = dict(BASE_FIELDS)
    f.update(fields or {})
    f["pcn_number"] = f"FS{abs(hash(case_id)) % 900000 + 100000}"
    row = {
        "case_id": case_id,
        "split": split,
        "family": family,
        "input": {
            "customer_narrative": narrative,
            "customer_answers": answers or {},
            "extraction_fields": f,
        },
        "expected": expected,
    }
    if evidence:
        row["input"]["evidence"] = evidence
    if sealed:
        row["sealed"] = True
    if notes:
        row["notes"] = notes
    return row


# Paraphrase matrix — generic wording, NOT prior sealed-holdout sentences.
PARAPHRASES: dict[str, list[tuple[str, dict]]] = {
    "LEFT_SITE": [
        ("I departed the location for a while during the stay.",
         {"semantic_concepts_affirmed": ["LEFT_SITE"]}),
        ("Drove away before coming back later that morning.",
         {"semantic_concepts_affirmed": ["LEFT_SITE", "RETURNED"]}),
        ("Went elsewhere between the two stays on site.",
         {"semantic_concepts_affirmed": ["LEFT_SITE"]}),
        ("Was no longer on site during the middle interval.",
         {"semantic_concepts_affirmed": ["LEFT_SITE"]}),
        ("Exited the car park briefly mid-visit.",
         {"semantic_concepts_affirmed": ["LEFT_SITE"]}),
        ("I did not leave the site at any point.",
         {"must_not_affirm_concepts": ["LEFT_SITE"],
          "negated_concepts_any": ["LEFT_SITE"]}),
        ("I might have left briefly, not sure.",
         {"uncertain_concepts_any": ["LEFT_SITE"],
          "must_not_promote_facts": ["left_site"]}),
        ("They told me the car had left; I was inside shopping.",
         {"semantic_concepts_any": ["SHOPPING"]}),
        ("Sunny day. Nothing about leaving the car park.",
         {"must_not_affirm_concepts": ["LEFT_SITE", "BROKEN_DOWN"]}),
        ("First we were there, then we went off site, then returned.",
         {"semantic_concepts_affirmed": ["LEFT_SITE", "RETURNED", "MULTIPLE_VISITS"]}),
    ],
    "RETURNED": [
        ("Came back to the bay after a short errand.",
         {"semantic_concepts_affirmed": ["RETURNED"]}),
        ("Returned to the site later the same day.",
         {"semantic_concepts_affirmed": ["RETURNED"]}),
        ("Went back in after collecting something nearby.",
         {"semantic_concepts_affirmed": ["RETURNED"]}),
        ("I don't remember whether I came back.",
         {"uncertain_concepts_any": ["RETURNED"],
          "must_not_promote_facts": ["returned_same_day"]}),
        ("Never returned after leaving.",
         {"semantic_concepts_any": ["LEFT_SITE"],
          "must_not_affirm_concepts": ["RETURNED"]}),
    ],
    "KEYING_ERROR": [
        ("Entered one character incorrectly when paying.",
         {"semantic_concepts_affirmed": ["KEYING_ERROR"]}),
        ("The registration did not exactly match what I typed.",
         {"semantic_concepts_affirmed": ["REGISTRATION_MISMATCH"]}),
        ("Mistyped the vehicle details on the app.",
         {"semantic_concepts_affirmed": ["KEYING_ERROR"]}),
        ("Payment was made under an incorrect registration.",
         {"semantic_concepts_affirmed": ["PAYMENT_MADE", "KEYING_ERROR"]}),
        ("I keyed the plate wrong by a single letter.",
         {"semantic_concepts_affirmed": ["KEYING_ERROR"]}),
        ("Settled the tariff online; the plate entry had a typo.",
         {"semantic_concepts_affirmed": ["PAYMENT_MADE", "KEYING_ERROR"]}),
        ("No keying mistake — registration was correct.",
         {"must_not_affirm_concepts": ["KEYING_ERROR"]}),
        ("Maybe I mistyped the plate; unsure.",
         {"uncertain_concepts_any": ["KEYING_ERROR"],
          "must_not_promote_facts": ["keying_error_type"]}),
        ("Paid via app and the character differed from the VRM.",
         {"semantic_concepts_affirmed": ["PAYMENT_MADE", "KEYING_ERROR"]}),
        ("Finished payment; entered registration incorrectly.",
         {"semantic_concepts_affirmed": ["PAYMENT_MADE", "KEYING_ERROR"]}),
    ],
    "BROKEN_DOWN": [
        ("The vehicle developed a fault while parked.",
         {"semantic_concepts_affirmed": ["BROKEN_DOWN"]}),
        ("Car stopped functioning normally on the forecourt.",
         {"semantic_concepts_affirmed": ["BROKEN_DOWN"]}),
        ("A mechanical problem prevented normal departure.",
         {"semantic_concepts_affirmed": ["BROKEN_DOWN", "IMMOBILISED"]}),
        ("Vehicle failure occurred while on site.",
         {"semantic_concepts_affirmed": ["BROKEN_DOWN"]}),
        ("Engine died and we could not move the car.",
         {"semantic_concepts_affirmed": ["BROKEN_DOWN", "IMMOBILISED"]}),
        ("It would not restart; we waited for recovery.",
         {"semantic_concepts_affirmed": ["BROKEN_DOWN", "IMMOBILISED"]}),
        ("There was no mechanical fault that day.",
         {"must_not_affirm_concepts": ["BROKEN_DOWN"],
          "negated_concepts_any": ["BROKEN_DOWN"]}),
        ("Possibly the vehicle lost power; I'm not sure.",
         {"uncertain_concepts_any": ["BROKEN_DOWN"],
          "must_not_promote_facts": ["vehicle_immobilised"]}),
        ("They told me the car had broken down.",
         {"must_not_promote_facts": ["vehicle_immobilised"]}),
        ("Stalled near the exit and needed roadside assistance.",
         {"semantic_concepts_affirmed": ["BROKEN_DOWN", "IMMOBILISED"]}),
    ],
    "LOADING_DELIVERY": [
        ("Unloading crates for the shop next door.",
         {"semantic_concepts_affirmed": ["LOADING"]}),
        ("Courier drop for unit B; briefly stationary.",
         {"semantic_concepts_affirmed": ["DELIVERY"]}),
        ("Collecting a parcel order from the warehouse desk.",
         {"semantic_concepts_affirmed": ["COLLECTION"]}),
        ("Goods were being loaded into the van.",
         {"semantic_concepts_affirmed": ["LOADING"]}),
        ("Making a delivery run to the trade counter.",
         {"semantic_concepts_affirmed": ["DELIVERY"]}),
        ("Not loading — just waiting for a friend.",
         {"must_not_affirm_concepts": ["LOADING"]}),
        ("Perhaps we were unloading; cannot remember.",
         {"uncertain_concepts_any": ["LOADING"],
          "must_not_promote_facts": ["loading_activity"]}),
        ("Stock drop-off and then a second short stay.",
         {"semantic_concepts_any": ["DROP_OFF", "DELIVERY", "MULTIPLE_VISITS"]}),
        ("Parked only while the courier collected an order.",
         {"semantic_concepts_affirmed": ["COLLECTION"]}),
        ("Unloading a delivery for the hardware unit.",
         {"semantic_concepts_affirmed": ["LOADING", "DELIVERY"]}),
    ],
    "PERMIT": [
        ("I hold a resident permit for the block.",
         {"semantic_concepts_affirmed": ["PERMIT_HELD"]}),
        ("Have a permit; bay on the notice does not match my agreement.",
         {"semantic_concepts_affirmed": ["PERMIT_HELD"]}),
        ("Permit was displayed in the windscreen.",
         {"semantic_concepts_affirmed": ["PERMIT_DISPLAYED"]}),
        ("Not certain whether a permit was shown.",
         {"uncertain_concepts_any": ["PERMIT_DISPLAYED"],
          "must_not_promote_facts": ["permit_held"]}),
        ("I do not hold a permit.",
         {"must_not_affirm_concepts": ["PERMIT_HELD"]}),
    ],
    "MULTI_CONCEPT": [
        ("Paid on the app, mistyped the plate, then left and returned later.",
         {"semantic_concepts_affirmed": [
             "PAYMENT_MADE", "KEYING_ERROR", "LEFT_SITE", "RETURNED"]}),
        ("Mechanical failure immobilised us; we never made a payment.",
         {"semantic_concepts_affirmed": ["BROKEN_DOWN", "IMMOBILISED"],
          "negated_concepts_any": ["PAYMENT_MADE"]}),
    ],
    # Prior holdout failure *classes* as DEV observations — reworded, not copies.
    "P104_OBS": [
        ("After stopping briefly the car failed to restart; recovery was called.",
         {"semantic_concepts_affirmed": ["BROKEN_DOWN", "IMMOBILISED"],
          "notes": "DEV observation of P10.4 mechanical paraphrase class"}),
        ("Finished paying in the operator app; one character on the plate was wrong.",
         {"semantic_concepts_affirmed": ["PAYMENT_MADE", "KEYING_ERROR"],
          "notes": "DEV observation of P10.4 payment/keying paraphrase class"}),
    ],
}


def build() -> dict:
    for sub in ("development", "validation", "holdout"):
        (DS / sub).mkdir(parents=True, exist_ok=True)

    dev_ids = []
    n = 0
    for family, rows in PARAPHRASES.items():
        for i, (narrative, exp) in enumerate(rows, 1):
            n += 1
            cid = f"DEV_{family.lower()}_{i:02d}"
            notes = exp.pop("notes", "") if "notes" in exp else ""
            # shallow copy expected
            expected = dict(exp)
            case = _case(cid, "DEVELOPMENT", family.lower(), narrative, expected,
                         notes=notes)
            if "PAYMENT" in family or "KEYING" in family or "keying" in str(expected):
                if "PAYMENT_MADE" in (expected.get("semantic_concepts_affirmed") or []):
                    case["input"]["customer_answers"] = {
                        "payment_made": "yes", "payment_method": "APP",
                        "keying_error_type": "MINOR",
                    }
                    case["input"]["extraction_fields"]["alleged_breach"] = (
                        "No valid payment for vehicle")
                    case["input"]["evidence"] = [{
                        "evidence_id": "E4", "kind": "APP_SCREENSHOT",
                        "filename": "app.png", "doc_type": "APP_SCREENSHOT",
                    }]
            path = DS / "development" / f"{cid}.json"
            path.write_text(json.dumps(case, indent=2) + "\n", encoding="utf-8")
            dev_ids.append(cid)

    val_specs = [
        ("VAL_ mechan_paraphrase".replace(" ", ""), "mechanical",
         "The vehicle ceased normal operation and we were stuck until help arrived.",
         {"semantic_concepts_affirmed": ["BROKEN_DOWN", "IMMOBILISED"]}),
        ("VAL_pay_key_paraphrase", "payment_keying",
         "I settled parking on the phone app but one character of the plate was wrong.",
         {"semantic_concepts_affirmed": ["PAYMENT_MADE", "KEYING_ERROR"],
          "substantive_grounds_any": ["KB-PAY-01", "KB-KEY-01"]},
         {"payment_made": "yes", "payment_method": "APP", "keying_error_type": "MINOR"}),
        ("VAL_left_return_paraphrase", "multiple_visits",
         "Departed mid-morning, re-entered that afternoon for another stay.",
         {"semantic_concepts_affirmed": ["LEFT_SITE", "RETURNED", "MULTIPLE_VISITS"]}),
        ("VAL_loading_paraphrase", "loading_delivery",
         "Brief stop while goods were unloaded for the trade unit.",
         {"semantic_concepts_affirmed": ["LOADING"]}),
        ("VAL_negation_paraphrase", "negation",
         "Never paid that day and no vehicle fault occurred.",
         {"must_not_affirm_concepts": ["PAYMENT_MADE", "BROKEN_DOWN"],
          "negated_concepts_any": ["PAYMENT_MADE", "BROKEN_DOWN"]}),
        ("VAL_uncertain_paraphrase", "uncertainty",
         "Might have left the site; I cannot remember clearly.",
         {"uncertain_concepts_any": ["LEFT_SITE"],
          "must_not_promote_facts": ["left_site"]}),
        ("VAL_permit_paraphrase", "permit",
         "I am a permit holder for the residence; bay allocation looks wrong.",
         {"semantic_concepts_affirmed": ["PERMIT_HELD"]},
         {"resident_status": "TENANT", "permit_held": "yes"}),
        ("VAL_irrelevant_paraphrase", "irrelevant",
         "Nice weather. Queued for a coffee. No parking defence facts stated.",
         {"must_not_affirm_concepts": [
             "PAYMENT_MADE", "BROKEN_DOWN", "KEYING_ERROR", "LOADING"]}),
        ("VAL_legal_late_ntk", "legal_finding",
         "The notice arrived weeks after the parking date.",
         {"substantive_grounds_any": ["KB-POFA-02"],
          "must_not_include_grounds": ["KB-POFA-01", "KB-POFA-05"]},
         None, {"parking_event_date": "01/08/2026", "notice_issue_date": "25/08/2026"}),
        ("VAL_multi_concept", "multi_concept",
         "Paid via app with a mistyped plate; later left and came back.",
         {"semantic_concepts_affirmed": [
             "PAYMENT_MADE", "KEYING_ERROR", "LEFT_SITE", "RETURNED"]},
         {"payment_made": "yes", "payment_method": "APP", "keying_error_type": "MINOR"}),
    ]
    val_ids = []
    for spec in val_specs:
        cid, family, narr, exp = spec[0], spec[1], spec[2], spec[3]
        answers = spec[4] if len(spec) > 4 else None
        fields = spec[5] if len(spec) > 5 else None
        case = _case(cid, "VALIDATION", family, narr, exp, answers=answers, fields=fields)
        if answers and answers.get("payment_made"):
            case["input"]["extraction_fields"]["alleged_breach"] = (
                "No valid payment for vehicle")
            case["input"]["evidence"] = [{
                "evidence_id": "E4", "kind": "APP_SCREENSHOT",
                "filename": "app.png", "doc_type": "APP_SCREENSHOT",
            }]
        (DS / "validation" / f"{cid}.json").write_text(
            json.dumps(case, indent=2) + "\n", encoding="utf-8")
        val_ids.append(cid)

    # NEW sealed holdout — expectations sealed; do not inspect during implementation.
    holdout = [
        ("HOLDOUT_E",
         "After a short halt the engine refused to fire again; assistance was arranged."),
        ("HOLDOUT_F",
         "Tariff paid through the parking app; the entered VRM was off by one letter."),
        ("HOLDOUT_G",
         "Stopped only to take a parcel from the collection point at the unit."),
        ("HOLDOUT_H",
         "Unsure if we went off-site; permit situation also unclear."),
    ]
    hold_ids = []
    for cid, narr in holdout:
        case = _case(
            cid, "HOLDOUT", "sealed_holdout", narr,
            {"sealed": True, "note": "Sealed until P10.5 evaluation cycle."},
            sealed=True,
            notes="New P10.5 sealed holdout. Do not inspect expected during implementation.",
        )
        (DS / "holdout" / f"{cid}.json").write_text(
            json.dumps(case, indent=2) + "\n", encoding="utf-8")
        hold_ids.append(cid)

    manifest = {
        "dataset_id": "p10_5_v1",
        "dataset_version": "p10_5_v1",
        "evaluation_release": "P10_5_SEMANTIC_2026-10-04",
        "immutable": True,
        "governance": {
            "prior_p10_4_holdout_opened": True,
            "prior_holdout_not_reused_as_blind": [
                "HOLDOUT_A", "HOLDOUT_B", "HOLDOUT_C", "HOLDOUT_D"],
            "new_holdout_sealed_until_eval": True,
            "no_exact_prior_holdout_phrase_patches": True,
            "no_operator_specific_rules": True,
            "no_pgvector": True,
        },
        "split": {
            "DEVELOPMENT": dev_ids,
            "VALIDATION": val_ids,
            "HOLDOUT": hold_ids,
        },
        "counts": {
            "development": len(dev_ids),
            "validation": len(val_ids),
            "holdout": len(hold_ids),
        },
    }
    (DS / "manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return manifest


if __name__ == "__main__":
    m = build()
    print(json.dumps(m["counts"], indent=2))
