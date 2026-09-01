-- macros/claims_analysis/macro_claims_analysis__params.sql
-- Area parameters for the claims_analysis area.
-- All values must be passed at runtime via --vars; no defaults.
-- Reference in models: {% set p = macro_claims_analysis__params() %}
{% macro macro_claims_analysis__params() %}
    {%- do return({
        'start_date':    macro_common__require_var('claims_analysis_start_date'),
        'high_cost_min': macro_common__require_var('claims_analysis_high_cost_min'),
        'med_cost_min':  macro_common__require_var('claims_analysis_med_cost_min')
    }) -%}
{% endmacro %}
