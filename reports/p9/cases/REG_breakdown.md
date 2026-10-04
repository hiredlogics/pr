# REG_breakdown

- Result: **FAIL**
- Dataset split: `BLIND_HOLDOUT`
- Completeness: `COMPLETE`
- Family: breakdown / Acme Parking Ltd / overstay
- First failed stage: `NARRATIVE_FACT_ERROR`
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
    "primary_route": "BREAKDOWN"
  },
  "appeal": {
    "must_express": [
      "mechanically immobilised"
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

- state: `RELEASED` outcome: `None`
- supported grounds: `['KB-POFA-01', 'KB-POFA-04', 'KB-POFA-05']`
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
    "KB-POFA-05"
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
  "must_express_missing": [
    "mechanically immobilised"
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

- passed: `True`
- issues: `[]`

## Claim Plan preservation

```json
{
  "passed": true,
  "before": [
    "KB-POFA-01",
    "KB-POFA-04",
    "KB-POFA-05"
  ],
  "after": [
    "KB-POFA-01",
    "KB-POFA-04",
    "KB-POFA-05"
  ],
  "digest_before": "1ffa8cdfee4e736b17f104c655d6f6a7b99767111436ba22380b82513c47158e",
  "digest_after": "1ffa8cdfee4e736b17f104c655d6f6a7b99767111436ba22380b82513c47158e",
  "master_roundtrip_ok": true
}
```

## Final outcome

- state `RELEASED` / outcome `None`
- letter empty: `False`
