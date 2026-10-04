# REG_late_ntk

- Result: **FAIL**
- Dataset split: `DEVELOPMENT`
- Completeness: `COMPLETE`
- Family: late_postal_ntk / Acme Parking Ltd / overstay
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
  "findings": [
    {
      "finding_type": "POFA_POSTAL_LATE",
      "status": "VERIFIED",
      "requires_lineage": true
    }
  ],
  "supported_grounds": [
    "KB-POFA-02"
  ],
  "outcome": {
    "state": "RELEASED",
    "letter_must_not_be_empty": true
  },
  "appeal": {
    "must_express": [
      "not delivered within"
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
- supported grounds: `['KB-POFA-01', 'KB-POFA-04', 'KB-POFA-05', 'KB-POFA-02']`
- findings: `[('POFA_POSTAL_LATE', 'VERIFIED'), ('POFA_NTD_NTK_LATE', 'NOT_SUPPORTED'), ('POFA_NTD_NTK_TOO_EARLY', 'NOT_SUPPORTED'), ('POFA_NTK_INVITATION_DEFECT', 'NOT_SUPPORTED'), ('NTK_CONTENT_DEFECT', 'VERIFIED')]`
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
  "status": "SCORED",
  "rows": [
    {
      "finding_type": "POFA_POSTAL_LATE",
      "expected": "VERIFIED",
      "actual": "VERIFIED",
      "lineage": true,
      "match": true
    }
  ],
  "passed": true,
  "accuracy": 1.0
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
    "KB-POFA-02"
  ],
  "expected": [
    "KB-POFA-02"
  ],
  "tp": [
    "KB-POFA-02"
  ],
  "fp": [
    "KB-POFA-01",
    "KB-POFA-04",
    "KB-POFA-05"
  ],
  "fn": [],
  "precision": 0.25,
  "recall": 1.0,
  "by_origin": {
    "verified_finding": {
      "expected": [
        "KB-POFA-02"
      ],
      "hit": [
        "KB-POFA-02"
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
      "module_id": "KB-POFA-02",
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
  "must_express_missing": [],
  "must_not_express_hit": [],
  "placeholders": [],
  "driver_unsafe": false,
  "ground_coverage": 0.0,
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
    "KB-POFA-01",
    "KB-POFA-04",
    "KB-POFA-05",
    "KB-POFA-02"
  ],
  "after": [
    "KB-POFA-01",
    "KB-POFA-04",
    "KB-POFA-05",
    "KB-POFA-02"
  ],
  "digest_before": "2b8d1cb7516134528c0d638a1ceaf3b14caf448ddf7bd2828911d920be77eb87",
  "digest_after": "2b8d1cb7516134528c0d638a1ceaf3b14caf448ddf7bd2828911d920be77eb87",
  "master_roundtrip_ok": true
}
```

## Final outcome

- state `RELEASED` / outcome `None`
- letter empty: `False`
