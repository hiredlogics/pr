"""100+ cases built from the client's real notices (brief §13).

Two halves, deliberately:

  SEEN      the eight notices in `sample notic/`, each crossed with the
            customer accounts that change the legal analysis for that
            allegation. Same operators, same sites, same wording as the post
            the client actually receives.

  UNSEEN    the same legal shapes with operators, sites, trade bodies and
            customer wording that appear nowhere in the repository or the
            notices. This is the half that tests the brief's §10 claim: a
            totally new operator, unfamiliar wording and a new location must
            still flow UNDERSTAND -> RETRIEVE -> VERIFY -> ASK ONLY MATERIAL ->
            SELECT -> WRITE.

For each case `expect` records what SHOULD happen, in two parts:

  theory    the case theory in a word ("payment", "no-contract", "grace",
            "pofa-late"). Used to check the letter argues the right thing, not
            a generic signage/landowner paragraph.
  must_not  grounds that would be WRONG here. A paid case must not argue
            no-contract; an admitted overstay must not argue it never parked.

Nothing here asserts a fact the customer did not state. Where an account is
silent on something material, that is on purpose: the pipeline should ask.
"""
from __future__ import annotations

from real_notices import REAL_NOTICES

# --------------------------------------------------------------- customer accounts
# Keyed by the allegation family they are a response to. Each is how a real
# person writes: short, unpunctuated, sometimes misspelt, sometimes irrelevant.

ACCOUNTS: dict[str, list[tuple[str, str, dict]]] = {
    "parent_child": [
        ("child-present", "my daughter was with me, she is 3. we went in for nappies",
         {"theory": "bay-entitlement", "must_not": ["KB-CON-02"]}),
        ("child-present-poor", "i had my kid in the car wot do they want",
         {"theory": "bay-entitlement", "must_not": ["KB-CON-02"]}),
        ("no-signage", "there was no sign on that bay at all, nothing painted",
         {"theory": "signage", "must_not": []}),
        ("one-minute", "observation and event time are the same minute, they "
                       "watched me for 60 seconds",
         {"theory": "observation-window", "must_not": []}),
        ("silent", "i want to appeal this charge",
         {"theory": "unknown", "must_not": [], "expect_question": True}),
        ("disabled-passenger", "my mother has a blue badge and was in the car",
         # Must NOT become "needed extra time" - brief §2
         {"theory": "bay-entitlement", "must_not": ["KB-EQ-02"],
          "must_not_fact": ["disability_extra_time"]}),
    ],
    "overstay": [
        ("paid-full", "i paid for 6 hours at the machine, i have the receipt",
         {"theory": "payment", "must_not": ["KB-CON-02"]}),
        ("no-limit-stated", "the notice doesnt even say what the maximum time is",
         {"theory": "allegation-particulars", "must_not": []}),
        ("left-and-returned", "i drove out and came back later, two separate trips",
         {"theory": "multiple-visits", "must_not": []}),
        ("barrier-queue", "the barrier was stuck and there was a big queue to get "
                          "out, took ages",
         {"theory": "grace", "must_not": []}),
        ("shopping-whole-time", "i was in the store the whole time doing a big shop",
         {"theory": "presence-not-parking", "must_not": ["KB-CON-02"]}),
        ("broke-down", "the car wouldnt start when i came back out",
         {"theory": "immobilised", "must_not": []}),
        ("silent", "please cancel this",
         {"theory": "unknown", "must_not": [], "expect_question": True}),
    ],
    "voucher": [
        ("shopped-not-validated", "i did my shop and have the receipt, nobody told "
                                  "me to put it in a machine",
         {"theory": "payment", "must_not": ["KB-CON-02"]}),
        ("kiosk-broken", "the validation machine was out of order that day",
         {"theory": "facility-failure", "must_not": []}),
        ("no-receipt", "i shopped there but i threw the receipt away",
         {"theory": "payment", "must_not": [], "expect_question": True}),
        ("never-shopped", "i didnt go in the shop, i read the sign and left",
         {"theory": "no-contract", "must_not": ["KB-PAY-01"]}),
        ("notice-late", "this came nearly two months after the day it says",
         {"theory": "pofa-late", "must_not": []}),
        ("left-site-returned", "i left the car park and drove back in later to "
                               "collect my husband",
         {"theory": "multiple-visits", "must_not": []}),
    ],
    "dropoff": [
        ("two-minutes", "i was there two minutes dropping my wife at departures",
         {"theory": "grace", "must_not": ["KB-CON-02"]}),
        ("paid-app-failed", "i tried to pay on the app and it wouldnt load",
         {"theory": "payment", "must_not": []}),
        ("no-sign-at-night", "it was 4am, i couldnt see any sign in the dark",
         {"theory": "signage", "must_not": []}),
        ("paid-wrong-reg", "i paid but typed the reg wrong, one letter out",
         {"theory": "keying", "must_not": ["KB-CON-02"]}),
        ("silent", "i dont think this is fair",
         {"theory": "unknown", "must_not": [], "expect_question": True}),
        ("de-minimis", "3 minutes. three. for a drop off.",
         {"theory": "grace", "must_not": []}),
    ],
    "permit": [
        ("staff-permit-held", "i work there and i have a staff permit, its on the "
                              "windscreen",
         {"theory": "permit", "must_not": ["KB-CON-02"]}),
        ("permit-in-app", "my permit is digital, its in the trust app",
         {"theory": "permit", "must_not": []}),
        ("not-the-driver", "i wasnt driving, i lend the car to my son",
         {"theory": "driver-identity", "must_not": []}),
        ("repeat-charge", "this is the second one for the same bay, i park there "
                          "every shift",
         {"theory": "permit", "must_not": []}),
        ("windscreen-not-keeper", "they stuck it on the windscreen, i never got "
                                  "anything in the post",
         {"theory": "notice-route", "must_not": []}),
        ("silent", "appeal please",
         {"theory": "unknown", "must_not": [], "expect_question": True}),
    ],
    "residential": [
        ("resident", "i live at weavers quarter, its my own parking space",
         {"theory": "authority", "must_not": ["KB-CON-02"]}),
        ("lease-right", "my lease gives me the right to park, they cant charge me",
         {"theory": "lease", "must_not": []}),
        ("ambiguous-allegation", "which is it, not registered or too long? it says "
                                 "both",
         {"theory": "allegation-particulars", "must_not": []}),
        ("visitor-registered", "i registered my visitor on their website that night",
         {"theory": "permit", "must_not": []}),
        ("37-minutes", "37 minutes in my own space and they want 100 pounds",
         {"theory": "penalty", "must_not": []}),
        ("never-parked-read-sign", "i read the board, didnt agree with it, turned "
                                   "round and went straight back out. never left "
                                   "the car there",
         # The holdout sentence that found the departure-meaning defects.
         {"theory": "no-contract", "must_not": ["KB-PAY-01"]}),
        ("silent", "i wish to appeal",
         {"theory": "unknown", "must_not": [], "expect_question": True}),
    ],
}

# allegation family -> which real notices carry it
FAMILY_OF = {
    "R-ECP-PARENTCHILD": "parent_child",
    "R-ECP-OVERSTAY": "overstay",
    "R-ECP-VOUCHER": "voucher",
    "R-APCOA-STANSTED": "dropoff",
    "R-APCOA-LUTON": "dropoff",
    "R-CPP-PERMIT-1": "permit",
    "R-CPP-PERMIT-2": "permit",
    "R-CPM-RESIDENTIAL": "residential",
}

# --------------------------------------------------------------- unseen notices
# Invented operators, sites and trade bodies. The legal SHAPE matches a real
# notice; nothing else does. No string here appears in the repo or the samples.

UNSEEN_TEMPLATE = """{kind}
{operator}
Notice reference: {ref}
Vehicle registration: {vrm}
Location: {site}
Date of event: {event}
Date issued: {issued}
{times}
Alleged contravention: {allegation}
Charge: {charge}, reduced to {discount} if paid within 14 days.
{liability}
{body_line}
"""

UNSEEN = [
    dict(case_id="U-VERGE-OVERSTAY", operator="Kestrel Verge Management",
         ata=None, site="Thornbeck Wharf Retail Yard", vrm="KU51PXR",
         event_date="2026-08-14", issue_date="2026-08-20", charge="£100",
         discount="£60", kind="NOTICE TO KEEPER", ref="KVM-884217",
         times="Entry time: 10:02   Exit time: 15:48",
         allegation="The vehicle remained beyond the maximum stay permitted by the "
                    "posted conditions",
         liability="Issued to the registered keeper under Schedule 4 of the "
                   "Protection of Freedoms Act 2012.",
         body_line="Kestrel Verge Management is an accredited member of a parking "
                   "trade association.",
         family="overstay"),
    dict(case_id="U-MARROW-PERMIT", operator="Marrowgate Civil Enforcement",
         ata="IPC", site="Pellitory Lane Precinct", vrm="YD19LTM",
         event_date="2026-08-02", issue_date="2026-08-09", charge="£100",
         discount="£60", kind="PARKING CHARGE NOTICE", ref="MCE-5512/08",
         times="Entry time: 16:21   Exit time: 16:24",
         allegation="Parked without displaying a valid permit",
         liability="Keeper liability is asserted under Schedule 4 of the "
                   "Protection of Freedoms Act 2012.",
         body_line="Operating under the International Parking Community Code of "
                   "Practice.",
         family="permit"),
    dict(case_id="U-OLDMILL-GRACE", operator="Oldmill Yard Parking Solutions",
         ata="BPA", site="Craythorne Infirmary Annexe", vrm="RE64OWB",
         event_date="2026-07-28", issue_date="2026-08-04", charge="£100",
         discount="£60", kind="NOTICE TO KEEPER", ref="OMY-77301",
         times="Entry time: 09:05   Exit time: 12:13",
         allegation="Stay exceeded the maximum period of three hours",
         liability="Issued to the registered keeper under Schedule 4 of the "
                   "Protection of Freedoms Act 2012.",
         body_line="Member of the British Parking Association.",
         family="overstay"),
    dict(case_id="U-SATTERS-LATE", operator="Sattersby Kerbside Compliance",
         ata="BPA", site="Netherfold Arcade", vrm="GK08ZSA",
         event_date="2026-02-11", issue_date="2026-09-29", charge="£100",
         discount="£60", kind="NOTICE TO KEEPER", ref="SKC-20119",
         times="Entry time: 13:40   Exit time: 15:02",
         allegation="Parked in a restricted area",
         liability="Issued to the registered keeper under Schedule 4 of the "
                   "Protection of Freedoms Act 2012.",
         body_line="Member of the British Parking Association.",
         family="overstay"),
    dict(case_id="U-QUILLON-RESIDENT", operator="Quillon Estate Services",
         ata="IPC", site="Harbrace Mews", vrm="VN72HCD",
         event_date="2026-08-19", issue_date="2026-08-26", charge="£100",
         discount="£60", kind="PARKING CHARGE NOTICE", ref="QES-4480",
         times="Entry time: 19:30   Exit time: 07:15 (following day)",
         allegation="No valid resident permit displayed",
         liability="Keeper liability is asserted under Schedule 4 of the "
                   "Protection of Freedoms Act 2012.",
         body_line="Operating under the International Parking Community Code of "
                   "Practice.",
         family="residential"),
    dict(case_id="U-DRAVEN-DROPOFF", operator="Dravenhill Parking Partners",
         ata=None, site="Stoke Rennet Interchange Forecourt", vrm="BC15EJN",
         event_date="2026-08-07", issue_date="2026-08-15", charge="£90",
         discount="£54", kind="PARKING CHARGE", ref="DPP-61204",
         times="Entry time: 11:12:40   Exit time: 11:15:02",
         allegation="Use of the set-down area without a valid payment",
         liability="Notice is given to the registered keeper of the vehicle.",
         body_line="Dravenhill Parking Partners operates this facility.",
         family="dropoff"),
    dict(case_id="U-PELLWICK-BAY", operator="Pellwick Site Services",
         ata="BPA", site="Brackenfell Superstore", vrm="MH63TQA",
         event_date="2026-09-02", issue_date="2026-09-09", charge="£100",
         discount="£60", kind="NOTICE TO KEEPER", ref="PSS-9912",
         times="Observation time: 14:07   Event time: 14:07",
         allegation="The vehicle occupied a family bay while no child was present",
         liability="Issued to the registered keeper under Schedule 4 of the "
                   "Protection of Freedoms Act 2012.",
         body_line="Member of the British Parking Association.",
         family="parent_child"),
    dict(case_id="U-GRINDLOW-VOUCHER", operator="Grindlow Retail Parking",
         ata="IPC", site="Causey Tor Shopping Quarter", vrm="SF17NDP",
         event_date="2026-08-23", issue_date="2026-08-30", charge="£100",
         discount="£60", kind="NOTICE TO KEEPER", ref="GRP-33418",
         times="Entry time: 11:40   Exit time: 12:58",
         allegation="The parking session was not registered at the in-store terminal",
         liability="Keeper liability is asserted under Schedule 4 of the "
                   "Protection of Freedoms Act 2012.",
         body_line="Operating under the International Parking Community Code of "
                   "Practice.",
         family="voucher"),
]


def unseen_text(spec: dict) -> str:
    return UNSEEN_TEMPLATE.format(event=spec["event_date"],
                                  issued=spec["issue_date"], **spec)


def build() -> list[dict]:
    """Every case: real notices x accounts, then unseen notices x accounts."""
    cases: list[dict] = []

    by_id = {n["case_id"]: n for n in REAL_NOTICES}
    for notice_id, family in FAMILY_OF.items():
        notice = by_id[notice_id]
        for label, account, expect in ACCOUNTS[family]:
            cases.append({
                "case_id": f"{notice_id}::{label}",
                "cohort": "SEEN",
                "family": family,
                "operator": notice["operator"],
                "site": notice["site"],
                "notice_text": notice["text"],
                "account": account,
                "expect": expect,
            })

    for spec in UNSEEN:
        for label, account, expect in ACCOUNTS[spec["family"]]:
            cases.append({
                "case_id": f"{spec['case_id']}::{label}",
                "cohort": "UNSEEN",
                "family": spec["family"],
                "operator": spec["operator"],
                "site": spec["site"],
                "notice_text": unseen_text(spec),
                "account": account,
                "expect": expect,
            })

    return cases


if __name__ == "__main__":
    cases = build()
    seen = sum(c["cohort"] == "SEEN" for c in cases)
    print(f"{len(cases)} cases: {seen} SEEN, {len(cases) - seen} UNSEEN")
    fams: dict[str, int] = {}
    for c in cases:
        fams[c["family"]] = fams.get(c["family"], 0) + 1
    for f, n in sorted(fams.items(), key=lambda kv: -kv[1]):
        print(f"  {f:14s} {n}")
