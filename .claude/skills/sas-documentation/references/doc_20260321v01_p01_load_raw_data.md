---
name: doc_20260321v01_p01_load_raw_data
process_name: claims_analysis
source_sas_file: code/claims_analysis/p01_load_raw_data.sas
created: 2026-03-21 14:07:12
updated: 2026-03-21 14:07:12
---

## Documentation

1. %P01_LOAD_RAW_DATA

   - 1.1. Parameters:
     - 1.1.1. YEAR = required
     - 1.1.2. MONTH = required

   - 1.2. Stack three claim types into unified dataset
     - 1.2.1. Input(s):
       - 1.2.1.1. RAW.MEDICAL_CLAIMS
       - 1.2.1.2. RAW.PHARMACY_CLAIMS
       - 1.2.1.3. RAW.DENTAL_CLAIMS
     - 1.2.2. Output(s):
       - 1.2.2.1. WORK.CLAIMS_COMBINED
     - 1.2.3. Logic:
       - 1.2.3.1. Read from each source with WHERE year=&YEAR., keeping only member_id, claim_id, amount, dx_code, proc_code, service_date, provider_id (plus ndc_code for pharmacy, tooth_code for dental)
       - 1.2.3.2. Rename amount→paid_amount on all three; rename fill_date→service_date on pharmacy
       - 1.2.3.3. Stack all three into a single dataset using IN= flags to track origin
       - 1.2.3.4. Set claim_source = 'MEDICAL' | 'PHARMACY' | 'DENTAL' based on which IN= flag is true
       - 1.2.3.5. Apply formats: service_date as MMDDYY10, paid_amount as DOLLAR12.2

   - 1.3. Sort and interleave claims into chronological order
     - 1.3.1. Input(s):
       - 1.3.1.1. WORK.CLAIMS_COMBINED
     - 1.3.2. Output(s):
       - 1.3.2.1. WORK.CLAIMS_COMBINED_SORTED
       - 1.3.2.2. WORK.CLAIMS_TIMELINE
     - 1.3.3. Logic:
       - 1.3.3.1. Sort WORK.CLAIMS_COMBINED by member_id, service_date into WORK.CLAIMS_COMBINED_SORTED
       - 1.3.3.2. Read WORK.CLAIMS_COMBINED_SORTED twice: once with WHERE=(claim_source='MEDICAL'), once with WHERE=(claim_source='PHARMACY')
       - 1.3.3.3. Interleave both subsets BY member_id, service_date into WORK.CLAIMS_TIMELINE (rows merge in chronological order per member)

   - 1.4. Draw random sample
     - 1.4.1. Input(s):
       - 1.4.1.1. WORK.CLAIMS_COMBINED
     - 1.4.2. Output(s):
       - 1.4.2.1. WORK.CLAIMS_SAMPLE
     - 1.4.3. Logic:
       - 1.4.3.1. Initialize seed=12345
       - 1.4.3.2. Loop i=1 to 1000: generate random_obs = CEIL(RANUNI(seed) * total_obs) where total_obs comes from NOBS= on WORK.CLAIMS_COMBINED
       - 1.4.3.3. Read the row at position random_obs using POINT= direct access
       - 1.4.3.4. Output each sampled row to WORK.CLAIMS_SAMPLE. STOP after 1000 iterations.

   - 1.5. Join claims to member enrollment
     - 1.5.1. Input(s):
       - 1.5.1.1. WORK.CLAIMS_COMBINED
       - 1.5.1.2. RAW.MEMBER_ENROLLMENT
     - 1.5.2. Output(s):
       - 1.5.2.1. WORK.CLAIMS_WITH_MEMBERS
       - 1.5.2.2. WORK.CLAIMS_UNMATCHED
     - 1.5.3. Logic:
       - 1.5.3.1. Sort WORK.CLAIMS_COMBINED by member_id, service_date
       - 1.5.3.2. Merge with RAW.MEMBER_ENROLLMENT (keeping member_id, plan_code, age, gender, county_code, enrollment_start, enrollment_end) on member_id, filtering enrollment to enrollment_start <= MDY(&MONTH., 1, &YEAR.)
       - 1.5.3.3. On FIRST.member_id, reset claim_count=0 and total_paid=0
       - 1.5.3.4. Increment claim_count by 1 for each row; accumulate total_paid += paid_amount
       - 1.5.3.5. On LAST.member_id, compute avg_paid = total_paid / claim_count
       - 1.5.3.6. If row matched both claims and enrollment → output to WORK.CLAIMS_WITH_MEMBERS
       - 1.5.3.7. If row matched claims but not enrollment → output to WORK.CLAIMS_UNMATCHED

   - 1.6. Pivot claims into monthly spend per member
     - 1.6.1. Input(s):
       - 1.6.1.1. WORK.CLAIMS_WITH_MEMBERS
     - 1.6.2. Output(s):
       - 1.6.2.1. WORK.MEMBER_MONTHLY_SPEND
     - 1.6.3. Logic:
       - 1.6.3.1. Read WORK.CLAIMS_WITH_MEMBERS sorted by member_id
       - 1.6.3.2. Define two arrays: months{12} (month1–month12) for amounts, spend_flags{12} (flag1–flag12) for activity tracking. RETAIN both arrays and annual_total across rows.
       - 1.6.3.3. On FIRST.member_id, reset annual_total=0 and all array elements to 0
       - 1.6.3.4. For each row, extract month_idx = MONTH(service_date). Accumulate months{month_idx} += paid_amount, set spend_flags{month_idx}=1, accumulate annual_total += paid_amount
       - 1.6.3.5. On LAST.member_id, count active_months = number of spend_flags = 1. Compute PMPM = annual_total / active_months (0 if no active months). Output one row.

   - 1.7. Enrich claims with provider demographics
     - 1.7.1. Input(s):
       - 1.7.1.1. WORK.CLAIMS_WITH_MEMBERS
       - 1.7.1.2. RAW.PROVIDER_ROSTER
     - 1.7.2. Output(s):
       - 1.7.2.1. WORK.CLAIMS_WITH_PROVIDERS
     - 1.7.3. Logic:
       - 1.7.3.1. MERGE WORK.CLAIMS_WITH_MEMBERS (dropping plan_code) with RAW.PROVIDER_ROSTER (keeping provider_id, provider_name, specialty, npi) BY provider_id
       - 1.7.3.2. Rename specialty→provider_specialty on the RAW.PROVIDER_ROSTER input to avoid collision with any existing specialty column
       - 1.7.3.3. Keep only rows where in_claims=true (left join behavior — all claims retained, unmatched providers get missing values)

   - 1.8. Compute days between claims and flag readmissions
     - 1.8.1. Input(s):
       - 1.8.1.1. WORK.CLAIMS_WITH_MEMBERS
     - 1.8.2. Output(s):
       - 1.8.2.1. WORK.CLAIMS_WITH_GAPS
     - 1.8.3. Logic:
       - 1.8.3.1. Read WORK.CLAIMS_WITH_MEMBERS sorted by member_id, service_date
       - 1.8.3.2. Call LAG(service_date) and LAG(member_id) unconditionally before any conditional logic (required for correct queue behavior)
       - 1.8.3.3. Call DIF(paid_amount) to get change from prior row
       - 1.8.3.4. On FIRST.member_id, set days_since_last=missing and paid_change=missing (no prior row for this member)
       - 1.8.3.5. Otherwise, compute days_since_last = service_date - prev_service_date
       - 1.8.3.6. Set readmit_flag=1 if not FIRST.member_id and days_since_last <= 30, else 0

   - 1.9. Keyed lookup against enrollment
     - 1.9.1. Input(s):
       - 1.9.1.1. WORK.CLAIMS_COMBINED
       - 1.9.1.2. RAW.MEMBER_ENROLLMENT
     - 1.9.2. Output(s):
       - 1.9.2.1. WORK.CLAIMS_WITH_INDEXED_LOOKUP
     - 1.9.3. Logic:
       - 1.9.3.1. Read WORK.CLAIMS_COMBINED row by row via SET
       - 1.9.3.2. For each row, perform keyed read of RAW.MEMBER_ENROLLMENT using SET KEY=member_id / UNIQUE
       - 1.9.3.3. Check _IORC_ return code: if _IORC_ = %SYSRC(_SOK), set matched=1
       - 1.9.3.4. Otherwise set matched=0 and reset _ERROR_=0 to suppress log notes

   - 1.10. Double-pass daily and member-level grouping
     - 1.10.1. Input(s):
       - 1.10.1.1. WORK.CLAIMS_WITH_MEMBERS
     - 1.10.2. Output(s):
       - 1.10.2.1. WORK.MEMBER_DAILY_SUMMARY
     - 1.10.3. Logic:
       - 1.10.3.1. At top of each implicit DATA step iteration: reset member_visit_days=0, member_total_paid=0 (these reset per member)
       - 1.10.3.2. Enter outer DO UNTIL(LAST.member_id) loop
       - 1.10.3.3. Reset daily_claim_count=0, daily_total_paid=0 (these reset per date)
       - 1.10.3.4. Enter inner DO UNTIL(LAST.service_date) loop; read one row per iteration via SET/BY member_id service_date
       - 1.10.3.5. Accumulate daily_claim_count += 1, daily_total_paid += paid_amount
       - 1.10.3.6. When inner loop exits (LAST.service_date): compute daily_avg_paid = daily_total_paid / daily_claim_count, increment member_visit_days += 1, accumulate member_total_paid += daily_total_paid, output one row
       - 1.10.3.7. Repeat outer loop until LAST.member_id

   - 1.11. Guard against empty input and validate amounts
     - 1.11.1. Input(s):
       - 1.11.1.1. WORK.CLAIMS_WITH_MEMBERS
     - 1.11.2. Output(s):
       - 1.11.2.1. WORK.CLAIMS_VALIDATED
     - 1.11.3. Logic:
       - 1.11.3.1. Use NOBS=total on SET WORK.CLAIMS_WITH_MEMBERS to get row count at compile time
       - 1.11.3.2. If total=0, PUT warning message to log and STOP (no output dataset created)
       - 1.11.3.3. Otherwise read each row and set validation_flag based on paid_amount: 'NEG_PAY' if paid_amount < 0, 'ZERO_PAY' if paid_amount = 0, 'HIGH_PAY' if paid_amount > 100000, 'OK' otherwise
       - 1.11.3.4. Output each row with validation_flag to WORK.CLAIMS_VALIDATED

   - 1.12. Group claims into episodes
     - 1.12.1. Input(s):
       - 1.12.1.1. WORK.CLAIMS_WITH_MEMBERS
     - 1.12.2. Output(s):
       - 1.12.2.1. WORK.CLAIM_EPISODES
     - 1.12.3. Logic:
       - 1.12.3.1. Sort WORK.CLAIMS_WITH_MEMBERS by member_id, service_date
       - 1.12.3.2. Read with BY member_id. RETAIN episode_id, episode_start, episode_end, episode_claims, episode_total_paid, prev_date across rows.
       - 1.12.3.3. On FIRST.member_id: reset episode_id=1, episode_start=service_date, episode_end=service_date, episode_claims=0, episode_total_paid=0, prev_date=missing
       - 1.12.3.4. If prev_date is not missing and service_date - prev_date > 30: output the completed episode, then increment episode_id, reset episode_start/end/claims/total_paid for the new episode
       - 1.12.3.5. Update episode_end=service_date, episode_claims += 1, episode_total_paid += paid_amount, prev_date=service_date
       - 1.12.3.6. On LAST.member_id: output the final episode for this member

## Key Data Decisions and Considerations

1. External dependencies — the RAW libref and its datasets (MEDICAL_CLAIMS, PHARMACY_CLAIMS, DENTAL_CLAIMS, MEMBER_ENROLLMENT, PROVIDER_ROSTER) must exist before the macro is invoked

