# REG_residential

- Result: **FAIL**
- Dataset split: `BLIND_HOLDOUT`
- Completeness: `COMPLETE`
- Family: residential / Acme Parking Ltd / no_permit
- First failed stage: `DRAFT_ERROR`
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
  "outcome": {
    "state": "RELEASED",
    "primary_route": "RESIDENTIAL"
  },
  "appeal": {
    "must_express": [
      "parking space numbered 14"
    ],
    "must_not_express": [
      "I parked",
      "I drove"
    ],
    "exact_wording_approved": false
  }
}
```

## Actual

- state: `MANUAL_REVIEW` outcome: `PROCESSING_ERROR`
- supported grounds: `['KB-POFA-01', 'KB-POFA-04', 'KB-POFA-05', 'KB-RES-03', 'KB-REC-01']`
- findings: `[('POFA_POSTAL_LATE', 'NOT_SUPPORTED'), ('POFA_NTD_NTK_LATE', 'NOT_SUPPORTED'), ('POFA_NTD_NTK_TOO_EARLY', 'NOT_SUPPORTED'), ('POFA_NTK_INVITATION_DEFECT', 'NOT_SUPPORTED'), ('NTK_CONTENT_DEFECT', 'VERIFIED')]`
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
    "KB-POFA-01",
    "KB-POFA-04",
    "KB-POFA-05",
    "KB-RES-03",
    "KB-REC-01"
  ]
}
```

### CLAIM PLAN DIFF

```json
{
  "status": "N/A",
  "item_count": 28
}
```

### DRAFT REQUIREMENT DIFF

```json
{
  "status": "SCORED",
  "must_express_missing": [
    "parking space numbered 14"
  ],
  "must_not_express_hit": [],
  "placeholders": [],
  "driver_unsafe": false,
  "ground_coverage": 0.0,
  "material_fact_coverage": 0.0,
  "unsupported_assertion_rate": 0.0,
  "passed": false
}
```


## Validation

- passed: `False`
- issues: `[{'rule': 'VAL-POFA', 'severity': 'BLOCK', 'message': 'PoFA defect alleged without verified finding'}, {'rule': 'VAL-LEAK', 'severity': 'BLOCK', 'message': 'Internal IDs, placeholders or AI self-reference in output'}, {'rule': 'DV-LEAK', 'severity': 'BLOCK', 'message': 'Internal identifiers, placeholders, prompt or trace wording in the letter'}, {'rule': 'VAL-COVERAGE', 'severity': 'BLOCK', 'message': 'RES is approved in the Claim Plan but the letter never argues it; every approved ground needs at least one grounded sentence'}]`

## Claim Plan preservation

```json
{
  "passed": true,
  "before": [
    "KB-POFA-01",
    "KB-POFA-04",
    "KB-POFA-05",
    "KB-RES-03",
    "KB-REC-01"
  ],
  "after": [
    "KB-POFA-01",
    "KB-POFA-04",
    "KB-POFA-05",
    "KB-RES-03",
    "KB-REC-01"
  ],
  "digest_before": "21d5c93244a17674ab895ce2fdf6ef3a2833d0feec8b1f5c96d498090234f54d",
  "digest_after": "21d5c93244a17674ab895ce2fdf6ef3a2833d0feec8b1f5c96d498090234f54d",
  "master_roundtrip_ok": true
}
```

## Final outcome

- state `MANUAL_REVIEW` / outcome `PROCESSING_ERROR`
- letter empty: `True`
