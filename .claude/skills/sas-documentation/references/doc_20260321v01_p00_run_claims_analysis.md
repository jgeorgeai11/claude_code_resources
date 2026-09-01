---
name: doc_20260321v01_p00_run_claims_analysis
process_name: claims_analysis
source_sas_file: code/claims_analysis/p00_run_claims_analysis.sas
created: 2026-03-21 16:23:41
updated: 2026-03-21 16:23:41
---

## Documentation

1. Non-macro setup code
   - 1.1. OPTIONS mstored sasmstore=MACLIB — enables stored compiled macros
   - 1.2. LIBNAME RAW "/data/raw/claims", REF "/data/reference", FMT "/data/formats", CONFIG "/data/config", OUT "/output/results", MACLIB "&BASE_PATH./macros"
   - 1.3. FILENAME EXTFILE "&INPUT_PATH./claims_extract.csv"
   - 1.4. FILENAME DIRLIST PIPE "ls -1 &INPUT_PATH./*.csv" — dynamically lists available CSVs via shell pipe
   - 1.5. %INCLUDE 15 macro definition files from &BASE_PATH./macros/ (p01 through p15)

2. %P00_RUN_CLAIMS_ANALYSIS

   - 2.1. Parameters:
     - 2.1.1. REPORT_YEAR = 2024
     - 2.1.2. REPORT_MONTH = 12
     - 2.1.3. RUN_LOAD_RAW = Y
     - 2.1.4. RUN_HASH_LOOKUPS = Y
     - 2.1.5. RUN_FILE_IO = Y
     - 2.1.6. RUN_DATA_TRANSFORMS = Y
     - 2.1.7. RUN_DYNAMIC_CODE = Y
     - 2.1.8. RUN_SORT_DEDUP = Y
     - 2.1.9. RUN_FREQ_MEANS = Y
     - 2.1.10. RUN_TRANSPOSE = Y
     - 2.1.11. RUN_FORMATS = Y
     - 2.1.12. RUN_SQL_QUERIES = Y
     - 2.1.13. RUN_SQL_ADVANCED = Y
     - 2.1.14. RUN_IMPORT_EXPORT = Y
     - 2.1.15. RUN_ODS_REPORTS = Y
     - 2.1.16. RUN_UTILITY_PROCS = Y
     - 2.1.17. RUN_STATISTICS = Y
     - 2.1.18. INPUT_PATH = /data/raw/claims
     - 2.1.19. OUTPUT_PATH = /output/results

   - 2.2. %LET REPORT_START = first day of &REPORT_MONTH./&REPORT_YEAR., formatted as DATE9 via %SYSFUNC(MDY())
   - 2.3. %LET REPORT_END = last day of &REPORT_MONTH./&REPORT_YEAR., via %SYSFUNC(INTNX(MONTH, ..., 0, E))
   - 2.4. %LET REPORT_QUARTER = quarter number via %SYSFUNC(QTR())
   - 2.5. %LET RUN_TIMESTAMP = current datetime via %SYSFUNC(DATETIME(), DATETIME20.)
   - 2.6. %LET FILE_EXISTS = 1 if &INPUT_PATH./claims_extract.csv exists, 0 otherwise, via %SYSFUNC(FILEEXIST())
   - 2.7. %IF &RUN_LOAD_RAW. = Y → call %P01_LOAD_RAW_DATA(YEAR=&REPORT_YEAR., MONTH=&REPORT_MONTH.)
   - 2.8. %IF &RUN_HASH_LOOKUPS. = Y → call %P02_HASH_LOOKUPS(INPUT_DS=WORK.CLAIMS_COMBINED, OUTPUT_DS=WORK.CLAIMS_ENRICHED)
   - 2.9. %IF &RUN_FILE_IO. = Y AND &FILE_EXISTS. = 1 → call %P03_FILE_IO(INPUT_PATH=&INPUT_PATH., OUTPUT_PATH=&OUTPUT_PATH.)
   - 2.10. %IF &RUN_DATA_TRANSFORMS. = Y → call %P04_DATA_TRANSFORMS(INPUT_DS=WORK.CLAIMS_ENRICHED)
   - 2.11. %IF &RUN_DYNAMIC_CODE. = Y → call %P05_DYNAMIC_CODE(CONFIG_DS=CONFIG.RUN_PARAMETERS)
   - 2.12. %IF &RUN_SORT_DEDUP. = Y → call %P06_PROC_SORT_DEDUP(INPUT_DS=WORK.CLAIMS_COMBINED)
   - 2.13. %IF &RUN_FORMATS. = Y → call %P09_PROC_FORMAT
   - 2.14. %IF &RUN_FREQ_MEANS. = Y → call %P07_PROC_FREQ_MEANS(INPUT_DS=WORK.CLAIMS_ENRICHED)
   - 2.15. %IF &RUN_TRANSPOSE. = Y → call %P08_PROC_TRANSPOSE(INPUT_DS=WORK.FREQ_BY_DX)
   - 2.16. %IF &RUN_SQL_QUERIES. = Y → call %P10_PROC_SQL_QUERIES(YEAR=&REPORT_YEAR.)
   - 2.17. %IF &RUN_SQL_ADVANCED. = Y → call %P11_PROC_SQL_ADVANCED
   - 2.18. %IF &RUN_STATISTICS. = Y → call %P15_PROC_STATISTICS(INPUT_DS=WORK.CLAIMS_ENRICHED)
   - 2.19. %IF &RUN_IMPORT_EXPORT. = Y → call %P12_PROC_EXPORT_IMPORT(INPUT_PATH=&INPUT_PATH., OUTPUT_PATH=&OUTPUT_PATH.)
   - 2.20. %IF &RUN_ODS_REPORTS. = Y → call %P13_ODS_REPORTING(OUTPUT_PATH=&OUTPUT_PATH., REPORT_YEAR=&REPORT_YEAR.)
   - 2.21. %IF &RUN_UTILITY_PROCS. = Y → call %P14_UTILITY_PROCS
   - 2.22. %PUT NOTE: Claims analysis pipeline complete. Run timestamp: &RUN_TIMESTAMP.

3. Macro invocation (open code after macro definition)
   - 3.1. %P00_RUN_CLAIMS_ANALYSIS — invoke the orchestration macro with all default parameters

## Key Data Decisions and Considerations

1. External dependencies — &BASE_PATH. and &INPUT_PATH. must exist as session-level macro variables before this file is submitted: the step 1 LIBNAME, FILENAME, and %INCLUDE statements reference them, and the macro's INPUT_PATH parameter default applies only inside %P00_RUN_CLAIMS_ANALYSIS
2. The RUN_* switches are not independent — disabling a stage silently breaks the stages that read its outputs: WORK.CLAIMS_COMBINED (p01) feeds the p02 and p06 calls, WORK.CLAIMS_ENRICHED (p02) feeds p04, p07, and p15, and WORK.FREQ_BY_DX (p07) feeds p08

