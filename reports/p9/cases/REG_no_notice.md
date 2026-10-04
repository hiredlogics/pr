# REG_no_notice

- Result: **PASS**
- Dataset split: `VALIDATION`
- Completeness: `COMPLETE`
- Family: not_a_pcn / unknown / none
- First failed stage: `NONE`
- Extraction method: `INJECTED_EMPTY`

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
    "not_pcn": true
  },
  "extraction_evaluate": false,
  "findings": null,
  "supported_grounds": null,
  "outcome": {
    "letter_must_be_empty": true,
    "letter_excludes": [
      "KB-",
      "VAL-"
    ]
  },
  "appeal": {
    "must_express": [],
    "must_not_express": [
      "KB-",
      "VAL-"
    ],
    "exact_wording_approved": false
  }
}
```

## Actual

- state: `NO_APPEAL_RIGHT` outcome: `None`
- supported grounds: `[]`
- findings: `[]`
- asked: `[]`
- primary route: `None`

## Diffs

### FACT DIFF

```json
{
  "status": "N/A"
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
  "selected": []
}
```

### CLAIM PLAN DIFF

```json
{
  "status": "N/A",
  "item_count": 0
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
  "ground_coverage": "N/A",
  "material_fact_coverage": 1.0,
  "unsupported_assertion_rate": 0.0,
  "passed": true
}
```


## Validation

- passed: `None`
- issues: `[]`

## Claim Plan preservation

```json
{
  "status": "N/A"
}
```

## Final outcome

- state `NO_APPEAL_RIGHT` / outcome `None`
- letter empty: `True`
