-- macros/common/macro_common__require_var.sql
-- Read a required dbt var; raise a compiler error if missing.
-- Use from area params macros to keep them to one line per parameter.
{% macro macro_common__require_var(name) %}
    {%- set val = var(name, none) -%}
    {%- if execute and val is none -%}
        {{ exceptions.raise_compiler_error("Pass --vars '{" ~ name ~ ": <value>}'") }}
    {%- endif -%}
    {%- do return(val) -%}
{% endmacro %}
