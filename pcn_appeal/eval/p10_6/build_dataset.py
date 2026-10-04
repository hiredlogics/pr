"""Build P10.6 drafting fidelity DEVELOPMENT + fresh holdout cases."""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
DS = ROOT / "datasets" / "p10_6_v1"


def _base(operator, pcn, vrm, location, postcode, event, issue, breach, ata="BPA"):
    return {
        "operator_name": operator,
        "pcn_number": pcn,
        "vrm": vrm,
        "parking_location": location,
        "site_postcode": postcode,
        "parking_event_date": event,
        "notice_issue_date": issue,
        "alleged_breach": breach,
        "operator_ata": ata,
    }


CASES = [
    {
        "case_id": "DEV_DRAFT_single_payment",
        "split": "DEVELOPMENT",
        "family": "single_ground",
        "input": {
            "customer_narrative": "I paid for parking on the app for the full stay.",
            "customer_answers": {"payment_made": "yes", "payment_method": "APP"},
            "extraction_fields": _base(
                "Cedar Park Ltd", "CP610001", "AA11BBB", "Cedar Retail",
                "B1 1AA", "01/08/2026", "05/08/2026", "No valid payment"),
            "evidence": [{"evidence_id": "E4", "kind": "APP_SCREENSHOT",
                          "filename": "pay.png", "doc_type": "APP_SCREENSHOT"}],
        },
        "expected": {
            "facts_any": {"payment_made": True},
            "substantive_grounds_any": ["KB-PAY-01"],
            "draft_ground_coverage": 1.0,
            "required_particular_coverage": 1.0,
            "unsupported_assertion_rate": 0.0,
        },
    },
    {
        "case_id": "DEV_DRAFT_multi_independent",
        "split": "DEVELOPMENT",
        "family": "multiple_independent",
        "input": {
            "customer_narrative": (
                "The notice arrived very late. Separately the car broke down "
                "and would not restart on site."
            ),
            "customer_answers": {
                "vehicle_immobilised": "yes",
                "breakdown_severity": "IMMOBILISED",
            },
            "extraction_fields": _base(
                "Northgate Parking", "NG720011", "BB22CCC", "Northgate MSCP",
                "NE1 2BB", "01/07/2026", "25/07/2026", "Overstay"),
        },
        "expected": {
            "facts_any": {"vehicle_immobilised": True},
            "substantive_grounds_any": ["KB-BREAK-01", "KB-POFA-02"],
            "draft_ground_coverage": 1.0,
        },
    },
    {
        "case_id": "DEV_DRAFT_pay_key_merge",
        "split": "DEVELOPMENT",
        "family": "merged_compatible",
        "input": {
            "customer_narrative": "I paid on the app but mistyped one character of the plate.",
            "customer_answers": {
                "payment_made": "yes",
                "payment_method": "APP",
                "keying_error_type": "MINOR",
            },
            "extraction_fields": _base(
                "Oakridge Parking", "OR610201", "CC33DDD", "Oakridge Quarter",
                "NG1 2AA", "03/08/2026", "07/08/2026", "No valid payment for vehicle"),
            "evidence": [{"evidence_id": "E4", "kind": "APP_SCREENSHOT",
                          "filename": "pay.png", "doc_type": "APP_SCREENSHOT"}],
        },
        "expected": {
            "facts_any": {"payment_made": True, "keying_error_type": "MINOR"},
            "substantive_grounds_any": ["KB-PAY-01", "KB-KEY-01"],
            "draft_ground_coverage": 1.0,
        },
    },
    {
        "case_id": "DEV_DRAFT_legal_finding_rebuttal",
        "split": "DEVELOPMENT",
        "family": "legal_finding_factual",
        "input": {
            "customer_narrative": "Notice came weeks after the event.",
            "customer_answers": {},
            "extraction_fields": _base(
                "Late Notice Ops", "LN800001", "DD44EEE", "Riverside Car Park",
                "LS1 1AA", "01/06/2026", "28/06/2026", "Failure to display ticket"),
        },
        "expected": {
            "substantive_grounds_any": ["KB-POFA-02"],
            "draft_ground_coverage": 1.0,
        },
    },
    {
        "case_id": "DEV_DRAFT_multiple_visits",
        "split": "DEVELOPMENT",
        "family": "multiple_visits",
        "input": {
            "customer_narrative": (
                "The vehicle left the car park mid-morning and returned later "
                "the same day for a second visit."
            ),
            "customer_answers": {
                "left_site": "yes",
                "returned_same_day": "yes",
                "multiple_visits": "yes",
            },
            "extraction_fields": _base(
                "VisitCam Parking", "VC900101", "EE55FFF", "VisitCam Centre",
                "CB1 1AA", "10/08/2026", "14/08/2026", "Overstay"),
        },
        "expected": {
            "facts_any": {"left_site": True, "returned_same_day": True},
            "substantive_grounds_any": ["KB-ANPR-01"],
            "draft_ground_coverage": 1.0,
        },
    },
    {
        "case_id": "DEV_DRAFT_loading_delivery",
        "split": "DEVELOPMENT",
        "family": "loading_delivery",
        "input": {
            "customer_narrative": (
                "I was collecting a parcel from the retail unit and loading it into the car."
            ),
            "customer_answers": {"loading_activity": "yes", "activity_type": "COLLECTION"},
            "extraction_fields": _base(
                "LoadBay Parking", "LB100301", "FF66GGG", "LoadBay Retail",
                "S1 2AA", "12/08/2026", "16/08/2026", "Parked in loading bay"),
        },
        "expected": {
            "facts_any": {"loading_activity": True},
            "substantive_grounds_any": ["KB-ACT-01"],
            "draft_ground_coverage": 1.0,
        },
    },
    {
        "case_id": "DEV_DRAFT_permit",
        "split": "DEVELOPMENT",
        "family": "permit",
        "input": {
            "customer_narrative": "I hold a resident permit for that bay.",
            "customer_answers": {"permit_held": "yes", "permit_type": "RESIDENT"},
            "extraction_fields": _base(
                "PermitCo", "PR200401", "GG77HHH", "Harbour Residences",
                "BS1 4AA", "15/08/2026", "19/08/2026", "No valid permit"),
            "evidence": [{"evidence_id": "E5", "kind": "PERMIT",
                          "filename": "permit.pdf", "doc_type": "PERMIT"}],
        },
        "expected": {
            "facts_any": {"permit_held": True},
            "substantive_grounds_any": ["KB-AUTH-01", "KB-RES-01"],
            "draft_ground_coverage": 1.0,
        },
    },
    {
        "case_id": "DEV_DRAFT_mechanical",
        "split": "DEVELOPMENT",
        "family": "mechanical",
        "input": {
            "customer_narrative": (
                "The car broke down on site; it would not restart and was immobilised."
            ),
            "customer_answers": {
                "vehicle_immobilised": "yes",
                "breakdown_severity": "IMMOBILISED",
            },
            "extraction_fields": _base(
                "MechPark Ltd", "MP300501", "HH88III", "MechPark Site",
                "L1 1AA", "18/08/2026", "22/08/2026", "Overstay"),
        },
        "expected": {
            "facts_any": {"vehicle_immobilised": True},
            "substantive_grounds_any": ["KB-BREAK-01"],
            "draft_ground_coverage": 1.0,
        },
    },
    {
        "case_id": "DEV_DRAFT_multi_particulars",
        "split": "DEVELOPMENT",
        "family": "multi_particulars",
        "input": {
            "customer_narrative": (
                "Paid via app for AB12 CDE; one character was mistyped at the terminal."
            ),
            "customer_answers": {
                "payment_made": "yes",
                "payment_method": "APP",
                "keying_error_type": "MINOR",
                "payment_amount": "3.50",
            },
            "extraction_fields": _base(
                "Particulars Park", "PP400601", "II99JJJ", "Particulars Site",
                "M1 1AA", "20/08/2026", "24/08/2026", "No valid payment"),
            "evidence": [{"evidence_id": "E4", "kind": "APP_SCREENSHOT",
                          "filename": "pay.png", "doc_type": "APP_SCREENSHOT"}],
        },
        "expected": {
            "facts_any": {"payment_made": True, "keying_error_type": "MINOR"},
            "substantive_grounds_any": ["KB-PAY-01", "KB-KEY-01"],
            "required_particular_coverage": 1.0,
        },
    },
    {
        "case_id": "DEV_DRAFT_support_only",
        "split": "DEVELOPMENT",
        "family": "support_only",
        "input": {
            "customer_narrative": "I just want the operator to prove landowner authority.",
            "customer_answers": {},
            "extraction_fields": _base(
                "Authority Only", "AO500701", "JJ00KKK", "Authority Site",
                "W1 1AA", "21/08/2026", "25/08/2026", "Breach of terms"),
        },
        "expected": {
            "outcome_any": ["NO_SUPPORTED_GROUNDS", "NEED_MORE_INFORMATION"],
            "must_not_release_empty_appeal": True,
        },
    },
    {
        "case_id": "DEV_DRAFT_no_ground",
        "split": "DEVELOPMENT",
        "family": "no_supported_ground",
        "input": {
            "customer_narrative": "The weather was nice that day.",
            "customer_answers": {},
            "extraction_fields": _base(
                "No Ground Ops", "NG600801", "KK11LLL", "Neutral Site",
                "E1 1AA", "22/08/2026", "26/08/2026", "Breach of terms"),
        },
        "expected": {
            "outcome_any": ["NO_SUPPORTED_GROUNDS", "NEED_MORE_INFORMATION"],
            "must_not_release_empty_appeal": True,
        },
    },
    # Fresh drafting holdout (not P10.5 sealed copies)
    {
        "case_id": "HOLDOUT_DRAFT_A",
        "split": "HOLDOUT",
        "family": "pay_key_unseen",
        "input": {
            "customer_narrative": (
                "Completed the tariff on RingGo; the VRM keyed was one digit out."
            ),
            "customer_answers": {
                "payment_made": "yes",
                "payment_method": "APP",
                "keying_error_type": "MINOR",
            },
            "extraction_fields": _base(
                "Holdout Park A", "HA700901", "LL22MMM", "Holdout Plaza A",
                "G1 1AA", "02/09/2026", "06/09/2026", "No payment recorded"),
            "evidence": [{"evidence_id": "E4", "kind": "APP_SCREENSHOT",
                          "filename": "ringgo.png", "doc_type": "APP_SCREENSHOT"}],
        },
        "expected": {
            "facts_any": {"payment_made": True, "keying_error_type": "MINOR"},
            "substantive_grounds_any": ["KB-PAY-01", "KB-KEY-01"],
            "draft_ground_coverage": 1.0,
        },
    },
    {
        "case_id": "HOLDOUT_DRAFT_B",
        "split": "HOLDOUT",
        "family": "mechanical_unseen",
        "input": {
            "customer_narrative": (
                "Engine cut out in the bay; recovery attended because it would not restart."
            ),
            "customer_answers": {
                "vehicle_immobilised": "yes",
                "breakdown_severity": "IMMOBILISED",
            },
            "extraction_fields": _base(
                "Holdout Park B", "HB801001", "MM33NNN", "Holdout Plaza B",
                "EH1 1AA", "04/09/2026", "08/09/2026", "Overstay"),
        },
        "expected": {
            "facts_any": {"vehicle_immobilised": True},
            "substantive_grounds_any": ["KB-BREAK-01"],
            "draft_ground_coverage": 1.0,
        },
    },
    {
        "case_id": "HOLDOUT_DRAFT_C",
        "split": "HOLDOUT",
        "family": "visits_unseen",
        "input": {
            "customer_narrative": (
                "Drove away after the first stop, then came back later that afternoon."
            ),
            "customer_answers": {
                "left_site": "yes",
                "returned_same_day": "yes",
                "multiple_visits": "yes",
            },
            "extraction_fields": _base(
                "Holdout Park C", "HC901101", "NN44OOO", "Holdout Plaza C",
                "CF1 1AA", "05/09/2026", "09/09/2026", "Overstay"),
        },
        "expected": {
            "facts_any": {"left_site": True, "returned_same_day": True},
            "draft_ground_coverage": 1.0,
        },
    },
    {
        "case_id": "HOLDOUT_DRAFT_D",
        "split": "HOLDOUT",
        "family": "no_ground_unseen",
        "input": {
            "customer_narrative": "I have nothing particular to add about the visit.",
            "customer_answers": {},
            "extraction_fields": _base(
                "Holdout Park D", "HD011201", "OO55PPP", "Holdout Plaza D",
                "BT1 1AA", "06/09/2026", "10/09/2026", "Breach of terms"),
        },
        "expected": {
            "outcome_any": ["NO_SUPPORTED_GROUNDS", "NEED_MORE_INFORMATION"],
            "must_not_release_empty_appeal": True,
        },
    },
]


def main() -> None:
    for split in ("development", "holdout"):
        (DS / split).mkdir(parents=True, exist_ok=True)
    rows = []
    for spec in CASES:
        split = "development" if spec["split"] == "DEVELOPMENT" else "holdout"
        path = DS / split / f"{spec['case_id']}.json"
        path.write_text(json.dumps(spec, indent=2) + "\n", encoding="utf-8")
        rows.append({"case_id": spec["case_id"], "split": spec["split"],
                     "family": spec["family"], "path": str(path.relative_to(ROOT))})
    manifest = {
        "dataset_id": "p10_6_v1",
        "purpose": "P10.6 draft fidelity — DEVELOPMENT + fresh drafting holdout",
        "note": "Do not tune against previously sealed P10.5 semantic holdout",
        "cases": rows,
    }
    (DS / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {len(rows)} cases -> {DS}")


if __name__ == "__main__":
    main()
