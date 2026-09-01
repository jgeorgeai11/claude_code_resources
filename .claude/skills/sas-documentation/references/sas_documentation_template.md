---
name: "doc_{yyyymmddv##}_{sas_file_name}"
process_name: {process_name}
source_sas_file: {path to SAS file}
created: {timestamp}
updated: {timestamp}
---

## Documentation

1. {Open code block description}
   - 1.1. {SAS statement and description}
   - 1.N. {SAS statement and description}

2. %{MACRO_NAME}

   - 2.1. Parameters:
     - 2.1.1. {param} = {default or "required"}
     - 2.1.N. {param} = {default or "required"}

   - 2.2. {Brief description of what this DATA/PROC step does}
     - 2.2.1. Input(s):
       - 2.2.1.1. {LIB.DATASET}
       - 2.2.1.N. {LIB.DATASET}
     - 2.2.2. Output(s):
       - 2.2.2.1. {LIB.DATASET}
       - 2.2.2.N. {LIB.DATASET}
     - 2.2.3. Logic:
       - 2.2.3.1. {operation}
       - 2.2.3.N. {operation}

   - 2.N. {macro-level statement and description}

## Key Data Decisions and Considerations

1. {External dependency or cross-step risk} — {what must hold} *(omit the section entirely if none)*
