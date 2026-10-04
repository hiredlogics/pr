# REG_payment_keying

- Result: **FAIL**
- Dataset split: `VALIDATION`
- Completeness: `COMPLETE`
- Family: payment_keying / Acme Parking Ltd / no_valid_payment
- First failed stage: `GROUND_SELECTION_ERROR`
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
  "supported_grounds": [
    "KB-PAY-01",
    "KB-KEY-01"
  ],
  "outcome": {
    "state": "RELEASED",
    "primary_route": "PAYMENT"
  },
  "appeal": {
    "must_express": [
      "registration-entry error"
    ],
    "must_not_express": [
      "I paid",
      "I parked",
      "I drove"
    ],
    "exact_wording_approved": false
  }
}
```

## Actual

- state: `MANUAL_REVIEW` outcome: `PROCESSING_ERROR`
- supported grounds: `['KB-POFA-01', 'KB-POFA-04', 'KB-POFA-05', 'KB-PAY-01', 'KB-KEY-01']`
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
  "status": "SCORED",
  "selected": [
    "KB-POFA-01",
    "KB-POFA-04",
    "KB-POFA-05",
    "KB-PAY-01",
    "KB-KEY-01"
  ],
  "expected": [
    "KB-PAY-01",
    "KB-KEY-01"
  ],
  "tp": [
    "KB-PAY-01",
    "KB-KEY-01"
  ],
  "fp": [
    "KB-POFA-01",
    "KB-POFA-04",
    "KB-POFA-05"
  ],
  "fn": [],
  "precision": 0.4,
  "recall": 1.0,
  "by_origin": {
    "narrative": {
      "expected": [
        "KB-PAY-01",
        "KB-KEY-01"
      ],
      "hit": [
        "KB-PAY-01",
        "KB-KEY-01"
      ]
    }
  },
  "passed": false
}
```

### CLAIM PLAN DIFF

```json
{
  "status": "SCORED",
  "missing": [],
  "bundles": [
    {
      "module_id": "KB-PAY-01",
      "exists": true,
      "supported": true,
      "has_facts": true,
      "has_reason": true
    },
    {
      "module_id": "KB-KEY-01",
      "exists": true,
      "supported": true,
      "has_facts": true,
      "has_reason": true
    }
  ],
  "coverage": 1.0,
  "support_bundle_completeness": 1.0,
  "passed": true
}
```

### DRAFT REQUIREMENT DIFF

```json
{
  "status": "SCORED",
  "must_express_missing": [
    "registration-entry error"
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
- issues: `[{'rule': 'VAL-COVERAGE', 'severity': 'BLOCK', 'message': 'POFA is approved in the Claim Plan but the letter never argues it; every approved ground needs at least one grounded sentence'}]`

## Claim Plan preservation

```json
{
  "passed": true,
  "before": [
    "KB-POFA-01",
    "KB-POFA-04",
    "KB-POFA-05",
    "KB-PAY-01",
    "KB-KEY-01"
  ],
  "after": [
    "KB-POFA-01",
    "KB-POFA-04",
    "KB-POFA-05",
    "KB-PAY-01",
    "KB-KEY-01"
  ],
  "digest_before": "ca11bd88c557dde3f337e74a9c0e70d0729049894849bd32b4b2ba0531f7ba65",
  "digest_after": "ca11bd88c557dde3f337e74a9c0e70d0729049894849bd32b4b2ba0531f7ba65",
  "master_roundtrip_ok": true
}
```

## Final outcome

- state `MANUAL_REVIEW` / outcome `PROCESSING_ERROR`
- letter empty: `True`
