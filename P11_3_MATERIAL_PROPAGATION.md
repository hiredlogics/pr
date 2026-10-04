# P11.3 — Material Narrative Particular Propagation

**Verdict:** `MATERIAL_PROPAGATION_FIX_REQUIRED`

| Item | Value |
|---|---|
| case_id | `2bcf17ff-aef2-416e-98a3-8b53d058293d` |
| state | `MANUAL_REVIEW` |
| departure_reason | `a necessary item was realised to have been left elsewhere, prompting the departure` |
| validation | `{'passed': False, 'issues': ['VAL-CONFLICT']}` |

## Trace
```
{
  "raw_narrative": "I attended the car park for shopping at the retail estate. Partway through I realised I had forgotten my purse at home, so I left the site. I returned later the same day to continue my visit.",
  "narrative_atom": [
    {
      "atom_id": "NA-departure_reason",
      "name": "departure_reason",
      "proposition": "a necessary item was realised to have been left elsewhere, prompting the departure",
      "source_text": "realised I had forgotten my purse at home",
      "source_excerpt": "I attended the car park for shopping at the retail estate. Partway through I realised I had forgotten my purse at home, so I left the site. I returned later the same day to continue my visit.",
      "attribution": "CUSTOMER",
      "polarity": "AFFIRMED",
      "confidence": 0.85
    },
    {
      "atom_id": "NA-departure_reason",
      "name": "departure_reason",
      "proposition": "a necessary item was realised to have been left elsewhere, prompting the departure",
      "source_text": "realised I had forgotten my purse at home",
      "source_excerpt": "I attended the car park for shopping at the retail estate. Partway through I realised I had forgotten my purse at home, so I left the site. I returned later the same day to continue my visit.",
      "attribution": "CUSTOMER",
      "polarity": "AFFIRMED",
      "confidence": 0.85
    }
  ],
  "support_bundle_particulars": {
    "departure_in_support": true
  },
  "draft_plan_particulars": {
    "required": [
      "left_site",
      "returned_same_day",
      "visited_premises",
      "purpose_of_visit",
      "departure_reason",
      "multiple_visits"
    ],
    "values": {
      "purpose_of_visit": "shopping",
      "left_site": true,
      "returned_same_day": true,
      "multiple_visits": true,
      "departure_reason": "a necessary item was realised to have been left elsewhere, prompting the departure"
    },
    "narrative_atoms": [
      {
        "atom_id": "NA-departure_reason",
        "name": "departure_reason",
        "proposition": "a necessary item was realised to have been left elsewhere, prompting the departure",
        "source_text": "realised I had forgotten my purse at home",
        "source_excerpt": "I attended the car park for shopping at the retail estate. Partway through I realised I had forgotten my purse at home, so I left the site. I returned later the same day to continue my visit.",
        "attribution": "CUSTOMER",
        "polarity": "AFFIRMED",
        "confidence": 0.85
      },
      {
        "atom_id": "NA-departure_reason",
        "name": "departure_reason",
        "proposition": "a necessary item was realised to have been left elsewhere, prompting the departure",
        "source_text": "realised I had forgotten my purse at home",
        "source_excerpt": "I attended the car park for shopping at the retail estate. Partway through I realised I had forgotten my purse at home, so I left the site. I returned later the same day to continue my visit.",
        "attribution": "CUSTOMER",
        "polarity": "AFFIRMED",
        "confidence": 0.85
      }
    ]
  },
  "generated_section": "I am appealing this Parking Charge Notice as the registered keeper of the vehicle EX15CZT. I do not admit to being the driver and will not be identifying the driver.\nThis Parking Charge Notice was issued by post.\nThe notice consists of front and back pages, and the contents have been checked against the Schedule 4 requirements for a postal Notice to Keeper.\nThat review confirms that the notice does not contain a compliant, route-specific keeper-liability warning, even though the document is complete.\nThe operator has therefore not met the applicable Schedule 4 content conditions and cannot rely on Schedule 4 to transfer liability to the registered keeper where the driver has not been identified.\nAccording to the notice, the alleged parking event at Canada Water Estate, SE16 7LL took place on 22 April 2026 and the postal Notice to Keeper was issued on 11 May 2026.\nFor a postal Notice to Keeper under Schedule 4 in England and Wales, the statutory deadline for delivery in these circumstances was 6 May 2026.\nUsing the standard presumption of delivery for post, the notice is treated as having been delivered on 13 May 2026.\nThis is 7 days after the statutory deadline.\nThe Notice to Keeper was therefore not delivered within the applicable statutory period for the postal notice route, and the operator has failed to satisfy a condition required to transfer liability to the registered keeper under Schedule 4.\nThe keeper's account is that the vehicle visited Canada Water Estate, SE16 7LL for shopping, left the site, and then returned later the same day.\nThe keeper states that a necessary item was realised to have been left elsewhere, which prompted the vehicle to leave the site before returning once that had been resolved.\nThis means the vehicle attended the site on more than one separate occasion on the date in question.\
```

## ANPR paragraph
```
I am appealing this Parking Charge Notice as the registered keeper of the vehicle EX15CZT. I do not admit to being the driver and will not be identifying the driver.
This Parking Charge Notice was issued by post.
The notice consists of front and back pages, and the contents have been checked against the Schedule 4 requirements for a postal Notice to Keeper.
That review confirms that the notice does not contain a compliant, route-specific keeper-liability warning, even though the document is complete.
The operator has therefore not met the applicable Schedule 4 content conditions and cannot rely on Schedule 4 to transfer liability to the registered keeper where the driver has not been identified.
According to the notice, the alleged parking event at Canada Water Estate, SE16 7LL took place on 22 April 2026 and the postal Notice to Keeper was issued on 11 May 2026.
For a postal Notice to Keeper under Schedule 4 in England and Wales, the statutory deadline for delivery in these circumstances was 6 May 2026.
Using the standard presumption of delivery for post, the notice is treated as having been delivered on 13 May 2026.
This is 7 days after the statutory deadline.
The Notice to Keeper was therefore not delivered within the applicable statutory period for the postal notice route, and the operator has failed to satisfy a condition required to transfer liability to the registered keeper under Schedule 4.
The keeper's account is that the vehicle visited Canada Water Estate, SE16 7LL for shopping, left the site, and then returned later the same day.
The keeper states that a necessary item was realised to have been left elsewhere, which prompted the vehicle to leave the site before returning once that had been resolved.
This means the vehicle attended the site on more than one separate occasion on the date in question.
In these circumstances, the first recorded ANPR entry and the final recorded exit must not be assumed to represent a single continuous stay without checking all intermediate captures.
The operator is therefore requested to review and disclose the complete ANPR record for the vehicle on that date, including any intermediate entry and exit images, rather than relying solely on the two images selected for the Parking Charge Notice.
For the reasons set out above, CP Plus has not established keeper liability under Schedule 4 of the Protection of Freedoms Act 2012, and the ANPR record relied on requires full review. As the driver has not been identified, there is no lawful basis to pursue this charge against the registered keeper on a Schedule 4 footing. I invite CP Plus to cancel the Parking Charge Notice 00347261120013 accordingly.
```

## Acceptance
```
{
  "shopping_rendered": true,
  "reason_for_leaving_rendered": true,
  "left_site_rendered": true,
  "returned_rendered": true,
  "multiple_visits_rendered": true,
  "pofa_preserved": true,
  "unsupported_assertions_zero": false,
  "driver_disclosure_zero": true,
  "claim_plan_not_rewritten_by_drafter": false,
  "departure_reason_in_bundle": true,
  "regression_offline": true,
  "released": false
}
```

## Regression (offline)
```
{
  "cases": {
    "forgot_wallet": {
      "atom": {
        "atom_id": "NA-departure_reason",
        "name": "departure_reason",
        "proposition": "a necessary item had been forgotten, prompting the departure",
        "source_text": "forgot my wallet",
        "source_excerpt": "I went shopping, forgot my wallet, left the site and returned later the same day.",
        "attribution": "CUSTOMER",
        "polarity": "AFFIRMED",
        "confidence": 0.85
      },
      "departure_reason": "a necessary item had been forgotten, prompting the departure",
      "bundle_has_departure_reason": true,
      "passed": true
    },
    "collect_card": {
      "atom": {
        "atom_id": "NA-departure_reason",
        "name": "departure_reason",
        "proposition": "the departure was to collect a necessary item",
        "source_text": "left to collect my payment",
        "source_excerpt": "I left to collect my payment card and returned later the same day.",
        "attribution": "CUSTOMER",
        "polarity": "AFFIRMED",
        "confidence": 0.85
      },
      "departure_reason": "the departure was to collect a necessary item",
      "bundle_has_departure_reason": true,
      "passed": true
    },
    "item_at_home": {
      "atom": {
        "atom_id": "NA-departure_reason",
        "name": "departure_reason",
        "proposition": "a necessary item was realised to have been left elsewhere, prompting the departure",
        "source_text": "realised a necessary item was at home",
        "source_excerpt": "I realised a necessary item was at home, left and returned the same day.",
        "attribution": "CUSTOMER",
        "polarity": "AFFIRMED",
        "confidence": 0.85
      },
      "departure_reason": "a necessary item was realised to have been left elsewhere, prompting the departure",
      "bundle_has_departure_reason": true,
      "passed": true
    }
  },
  "passed": true
}
```

## PoFA
grounds=['KB-POFA-04', 'KB-POFA-02'] findings=['POFA_POSTAL_LATE', 'NTK_CONTENT_DEFECT'] independent=True
