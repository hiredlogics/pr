# P9 dataset manifest

Dataset: `p9_v1`

## Split

- DEVELOPMENT: REG_late_ntk, REG_late_ntk_anpr, REG_overstay_skip_all, REG_overstay_yes_all, REG_overstay_dont_know, REG_notice_plus_payment, REF_stn1947529, REF_88811908015, REF_00347261120013, REF_00347261100014, REF_70377302
- VALIDATION: REG_payment_keying, REG_no_notice, REF_lu3440789, REF_lu3489734
- BLIND_HOLDOUT: REG_breakdown, REG_residential, REF_new1936102153896, REF_sp62712518

## COMPLETE goldens

- `REG_late_ntk`
- `REG_late_ntk_anpr`
- `REG_overstay_skip_all`
- `REG_overstay_yes_all`
- `REG_overstay_dont_know`
- `REG_notice_plus_payment`
- `REG_payment_keying`
- `REG_no_notice`
- `REG_breakdown`
- `REG_residential`

## NOTICE_ONLY reference cases

- `REF_stn1947529`
- `REF_lu3440789`
- `REF_lu3489734`
- `REF_new1936102153896`
- `REF_88811908015`
- `REF_00347261120013`
- `REF_00347261100014`
- `REF_70377302`
- `REF_sp62712518`

## Leakage controls

- Near-duplicate Acme overstay notices stay together in DEVELOPMENT.
- LATE / LATE+ANPR additive pair both in DEVELOPMENT.
- CP Plus NTK + reminder stay together in DEVELOPMENT.
- APCOA Luton pair stay together in VALIDATION.
- BLIND_HOLDOUT uses different operators, allegations, and evidence types.
