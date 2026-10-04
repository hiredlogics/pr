# REG_overstay_skip_all

- Result: **PASS**
- Dataset split: `DEVELOPMENT`
- Completeness: `COMPLETE`
- Family: acme_overstay / Acme Parking Ltd / overstay
- First failed stage: `NONE`
- Extraction method: `INJECTED_FROM_DOCUMENT_GOLD`

## Versions

- git commit: `be577e4111cefe0e9d22ad562aa1bf89d82542f2`
- KB release: `None`
- prompt versions: `{'extraction': 10, 'drafting': 16, 'case_analysis': 8, 'validation': 3, 'page_references': 2, 'classification': 2}`
- model/provider: `ReferenceAnalysisLLM`
- Claim Plan builder: `3`
- Master Case Object: `1`

## Expected

```json
{
  "classification": {
    "document_class": "PCN"
  },
  "extraction_evaluate": true,
  "findings": null,
  "supported_grounds": null,
  "outcome": null,
  "appeal": {
    "must_express": [],
    "must_not_express": [
      "I parked",
      "I drove"
    ],
    "exact_wording_approved": false
  }
}
```

## Actual

- state: `RELEASED` outcome: `None`
- supported grounds: `['KB-POFA-02', 'KB-REC-01']`
- findings: `[('POFA_POSTAL_LATE', 'VERIFIED'), ('POFA_NTD_NTK_LATE', 'NOT_SUPPORTED'), ('POFA_NTD_NTK_TOO_EARLY', 'NOT_SUPPORTED'), ('POFA_NTK_INVITATION_DEFECT', 'NOT_SUPPORTED'), ('NTK_CONTENT_DEFECT', 'NOT_SUPPORTED')]`
- asked: `[]`
- primary route: `POFA`

## Diffs

### FACT DIFF

```json
{
  "status": "SCORED",
  "tp": [
    "operator_name",
    "pcn_number",
    "vrm",
    "parking_event_date",
    "notice_issue_date",
    "entry_time",
    "exit_time",
    "parking_location",
    "site_postcode",
    "alleged_breach"
  ],
  "fn": [],
  "provenance_mismatch": [],
  "precision": 1.0,
  "recall": 1.0,
  "by_kind": {
    "document": {
      "tp": 10,
      "fp": 0,
      "fn": 0
    },
    "customer_stated": {
      "tp": 0,
      "fp": 0,
      "fn": 0
    },
    "customer_confirmed": {
      "tp": 0,
      "fp": 0,
      "fn": 0
    },
    "derived": {
      "tp": 0,
      "fp": 0,
      "fn": 0
    }
  },
  "passed": true
}
```

### FINDING DIFF

```json
{
  "status": "N/A"
}
```

### GROUND DIFF

```json
{
  "status": "N/A",
  "selected": [
    "KB-POFA-02",
    "KB-REC-01"
  ]
}
```

### CLAIM PLAN DIFF

```json
{
  "status": "N/A",
  "item_count": 26
}
```

### DRAFT REQUIREMENT DIFF

```json
{
  "status": "SCORED",
  "must_express_missing": [],
  "must_not_express_hit": [],
  "placeholders": [],
  "driver_unsafe": false,
  "ground_coverage": 0.5,
  "material_fact_coverage": 1.0,
  "unsupported_assertion_rate": 0.0,
  "passed": true
}
```


## Validation

- passed: `True`
- issues: `[]`

## Claim Plan preservation

```json
{
  "passed": true,
  "before": [
    "KB-POFA-02",
    "KB-REC-01"
  ],
  "after": [
    "KB-POFA-02",
    "KB-REC-01"
  ],
  "digest_before": "c4152e96a361c2822c22c1471fddb9d4e5352bf45d1b71e9c1fa4456d079bb65",
  "digest_after": "c4152e96a361c2822c22c1471fddb9d4e5352bf45d1b71e9c1fa4456d079bb65",
  "master_roundtrip_ok": true
}
```

## Final outcome

- state `RELEASED` / outcome `None`
- letter empty: `False`
