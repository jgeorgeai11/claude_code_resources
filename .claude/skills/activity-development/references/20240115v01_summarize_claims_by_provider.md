---
name: 20240115v01_summarize_claims_by_provider
goal: Merge claims data with provider data and calculate summary statistics by provider to understand provider-level patterns in claims volume and costs.
created: 2024-01-15 10:30:23
updated: 2024-01-15 10:30:23
---

## Implementation Plan

### Phase 1 — Validate inputs

1. [pending] Create and run input claims data validation - `code/claims_analysis/data_validation/data_val_claims.py`
   - 1.1. Parameters: input_data
   - 1.2. Check schema: required columns (claim_id, provider_id, patient_id, service_date, amount)
   - 1.3. Validate date range: service_date between 2024-01-01 and 2024-12-31
   - 1.4. Validate amount > 0 for all records
   - 1.5. Run validation on data/raw/claims.csv

2. [pending] Create and run input providers data validation - `code/claims_analysis/data_validation/data_val_providers.py`
   - 2.1. Parameters: input_data
   - 2.2. Check schema: required columns (provider_id, provider_name)
   - 2.3. Validate no duplicate provider_id values
   - 2.4. Run validation on data/raw/providers.csv

### Phase 2 — Build the provider summary

3. [pending] Create implementation script - `code/claims_analysis/generate_provider_summary.py`
   - 3.1. Parameters: input_claims_data, input_provider_data, sample_size (None = full data)
   - 3.2. If sample_size is set, limit claims to the first sample_size rows
   - 3.3. Left merge providers onto claims on provider_id
   - 3.4. Save merged data to data/processed/claims_with_providers.parquet
   - 3.5. Group by provider_id, provider_name and calculate: total_claims = count(claim_id), total_amount = sum(amount), avg_amount = mean(amount)
   - 3.6. Sort results by total_amount descending and save to data/output/provider_summary.csv

### Phase 3 — Test, run, and validate output

4. [pending] Create and run unit tests - `code/claims_analysis/unit_tests/test_generate_provider_summary.py`
   - 4.1. Test merge logic correct
   - 4.2. Test aggregation calculations accurate
   - 4.3. Run tests with pytest
   - 4.4. Verify all tests pass

5. [pending] Run implementation script on a sample - `code/claims_analysis/generate_provider_summary.py`
   - 5.1. Run generate_provider_summary with input_claims_data=data/raw/claims.csv, input_provider_data=data/raw/providers.csv, sample_size=1000
   - 5.2. Verify output file created at data/output/provider_summary.csv

6. [pending] Create and run output validation on the sample output - `code/claims_analysis/data_validation/data_val_summary.py`
   - 6.1. Parameters: input_data
   - 6.2. Check schema: required columns (provider_id, provider_name, total_claims, total_amount, avg_amount)
   - 6.3. Verify row count > 0
   - 6.4. Check no null values in any column
   - 6.5. Validate total_claims > 0 for all providers
   - 6.6. Validate total_amount >= avg_amount for all providers
   - 6.7. Validate avg_amount > 0 for all providers
   - 6.8. Run validation on data/output/provider_summary.csv
   - 6.9. If issues found, debug and iterate

## Key Data Decisions and Considerations

1. Left merge to retain all claims — unmatched provider_ids will have null provider_name
2. Summarize at provider_id level (not provider_name) to handle potential duplicate names
3. Ensure raw data files exist in data/raw/ before starting
4. Verify on a 1,000-row claims sample first; run full data manually (sample_size=None) once the sample output validates
