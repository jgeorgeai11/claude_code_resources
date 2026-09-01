-- models/claims_analysis/intermediate/int_claims_analysis__categorized.sql
{% set p = macro_claims_analysis__params() %}

with

claims as (
    select * from {{ ref('stg_claims_analysis__medical') }}
),

-- Derive cost_tier from the claim payment amount. Level = claim_id
categorized as (
    select
        claim_id,
        bene_id,
        clm_from_dt,
        clm_pmt_amt,
        case
            when clm_pmt_amt >= {{ p.high_cost_min }} then 'HIGH'
            when clm_pmt_amt >= {{ p.med_cost_min }} then 'MEDIUM'
            when clm_pmt_amt > 0 then 'LOW'
            else 'ZERO'
        end as cost_tier
    from claims
)

select * from categorized
