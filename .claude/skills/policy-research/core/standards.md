---
name: standards
description: Research and response standards for policy research — source verification, response structure, citation formats, source hierarchy, confidence language, conflict resolution, and gap documentation.
---

# standards

## Guidelines

1. **Ground every claim in a retrieved source** — Training knowledge is a last resort
2. **Verify every citation at its source** — Retrieve each cited source directly; never cite based on how another source paraphrases it
3. **Structure every response** — Follow the [Response Structure](#response-structure) template
   - 3.1. **Answer and Regulatory Basis are required** — Include in every response regardless of complexity
   - 3.2. **Analysis, Caveats, and Gaps are conditional** — Include for multi-part, ambiguous, or contested questions; omit for straightforward factual lookups
4. **Match language and labels to source authority** — Represent the legal weight of every source accurately
   - 4.1. **Append authority labels** — Use the [Source Authority Hierarchy](#source-authority-hierarchy) `[Statute]`–`[Informal]` labels on every citation
   - 4.2. **Calibrate confidence language** — Use [Confidence Language](#confidence-language) tiers; never use definitive language for claims supported only by informal guidance or training knowledge
5. **Cite every claim at the sentence level** — Each discrete fact gets its own citation
   - 5.1. **Retrieved sources** — Use `(Source: [reference], via [access method]) [Label]`
     - 5.1.1. **`[reference]`** — See [Inline Citation Formats](#inline-citation-formats) for examples of how to format references by source type
     - 5.1.2. **`[access method]`** — Identifies the retrieval system (e.g., database schema, `web`, etc.)
   - 5.2. **Inference** — Use when drawing a conclusion not explicitly stated in any single source — `(Source: Inference from [cited sources])` — e.g., `(Source: Inference from §411.354(a) and §411.351)`
   - 5.3. **Training knowledge** — Use only when no retrieved source is available — `(Source: training knowledge — not verified)`
   - 5.4. **Combine multi-source citations with semicolons** — Each source keeps its own access method — `(Sources: §1877 of the SSA, via ecfr; 42 CFR §411.355(a), via ecfr)`
   - 5.5. **Cite the most specific subdivision** — `§435.1010(a)(1)(ii)`, not `§435.1010`
   - 5.6. **Spell out cross-reference chains** — When a regulation incorporates another by reference, cite both — `42 CFR §435.1010, incorporating the definition at 42 CFR §440.70(b)(3)`
   - 5.7. **Format quotations per [Quotation Formatting](#quotation-formatting)** — Quote verbatim when exact wording matters; paraphrase for context and background
6. **Resolve conflicts explicitly** — Never silently choose one source over another; apply [Conflict Resolution](#conflict-resolution) steps in order
7. **Document gaps in every response** — Note what could not be confirmed and why; omit the Gaps section only if genuinely nothing is missing
8. **Write in plain language** — Follow [Plain Language Principles](#plain-language-principles); lead with the answer, use active voice, define jargon on first use
9. **Write responses to a file** — Use the [Output File Convention](#output-file-convention); this enables structured review and iteration

## Reference

### Response Structure

| Section | Purpose | Guidance |
|---------|---------|----------|
| **Answer** | Direct answer to the question | 1–3 sentences; state the conclusion up front |
| **Regulatory Basis** | Statute and regulation citations establishing the answer | Cite highest-authority sources first (statute, then CFR) |
| **Analysis** | Detailed explanation with sub-headings as needed | Walk through reasoning; quote regulatory text where precision matters |
| **Caveats** | Limitations, uncertainty, and scope boundaries | Flag outdated guidance, pending rulemaking, state-level variation, untested interpretations |
| **Gaps** | Areas that could not be confirmed or warrant further research | List each gap with what was missing and why |

### Source Authority Hierarchy

| Level | Source Type | Legal Weight | Label |
|-------|-----------|--------------|-------|
| 1 | **Statute** (SSA, USC) | Binding law enacted by Congress | `[Statute]` |
| 2 | **Regulation** (CFR text, or FR rule provisions that will be codified) | Force and effect of law | `[Regulation]` |
| 3 | **Preamble** (FR discussion/rationale that is not codified in the CFR) | Persuasive, not independently binding | `[Preamble]` |
| 4 | **Sub-regulatory guidance** (IOMs, transmittals, CMS Rulings) | Significant practical weight; no force of law | `[Guidance]` |
| 5 | **Informal guidance** (FAQs, MLN fact sheets, webpages) | Informational only; no legal force | `[Informal]` |

Example: `(Source: 42 CFR §435.1010, via ecfr) [Regulation]`

### Inline Citation Formats

- `(Source: 42 CFR §435.1010(a)(1), via ecfr) [Regulation]`
- `(Source: 90 FR 12345, Medicare Provider Enrollment Final Rule, Jan. 15, 2025, via federal_register) [Preamble]`
- `(Source: Medicare Claims Processing Manual, Ch. 12 §30.6, via cms_iom) [Guidance]`
- `(Source: Inference from §411.354(a) and §411.351)`
- `(Source: training knowledge — not verified)`

### Confidence Language

| Tier | When to Use | Example Phrasing |
|------|------------|------------------|
| **Definitive** | Binding statute or regulation | "The regulation requires…", "Under 42 CFR §X, providers must…" |
| **Authoritative interpretation** | Preamble or sub-regulatory guidance | "CMS has interpreted this to mean…", "According to the preamble…" |
| **Analytical inference** | Conclusion not explicitly stated in any source | "This likely means…", "Reading §X together with §Y suggests…" |
| **Uncertain** | Genuine ambiguity or conflicting sources | "It is unclear whether…", "CMS has not addressed…" |

### Conflict Resolution

1. **State the conflict** — Present both positions with citations before resolving
2. **Apply the hierarchy** — Higher-authority sources control (statute over regulation, regulation over guidance)
3. **Check temporal ordering** — Later-in-time sources at the same level generally supersede earlier ones
4. **Apply specificity** — A more specific provision controls over a more general one
5. **Flag unresolved conflicts** — Move to the Caveats section if the conflict cannot be cleanly resolved

Example: "The statute provides X `(Source: §XXXX of the SSA, via ecfr) [Statute]`. CMS guidance interprets this as Y `(Source: [manual], Ch. XX §XX, via cms_iom) [Guidance]`. Because the statute controls, [conclusion]."

### Quotation Formatting

| Rule | Guidance |
|------|----------|
| Inline quotes (under 50 words) | Use quotation marks; introduce with context |
| Block quotes (50+ words) | Indent as blockquote; no quotation marks; cite immediately after the block |
| Accuracy | Reproduce verbatim; indicate changes with `[brackets]` and omissions with `…` |
| Quote vs. paraphrase | Quote definitions, operative terms, and conditions; paraphrase context and background |

### Response References

| File | Purpose |
|------|---------|
| [policy-research-template.md](../references/policy-research-template.md) | Empty structure with section headers and placeholder comments |
| [policy-research-example.md](../references/policy-research-example.md) | Completed response demonstrating all guidelines (Stark Law self-referral topic) |

### Output File Convention

| Field | Convention |
|-------|-----------|
| Directory | `docs/policy_research/` in the project root |
| Filename | `{YYYY-MM-DD}-{slug}.md` — date of research, kebab-case topic slug |
| Example | `docs/policy_research/2026-03-16-provider-enrollment-requirements.md` |

### Plain Language Principles

| Principle | Example |
|-----------|---------|
| Active voice | "CMS requires providers to submit claims" not "Claims are required to be submitted" |
| Define jargon on first use | "The Medicare Physician Fee Schedule (MPFS) — the payment system CMS uses to reimburse physicians — establishes…" |
| One idea per sentence | Break complex regulatory chains into sequential steps |
| Tables for comparisons | Use a table when comparing provisions, timelines, or options |
| Familiar words | "use" not "utilize", "help" not "facilitate", "about" not "approximately" |

## Workflow

1. **Read the example** — Review [policy-research-example.md](../references/policy-research-example.md) before writing
2. **Structure** — Set up the response using the [policy-research-template.md](../references/policy-research-template.md)
3. **Draft sections** — Populate each section; attach authority labels to every citation
4. **Calibrate** — Review confidence language against source authority; downgrade phrasing where sources are weaker than the language implies
5. **Check conflicts** — Apply the Conflict Resolution protocol if sources disagree
6. **Document gaps** — Fill in the Gaps section
7. **Plain language pass** — Review for jargon, passive voice, and unnecessarily complex phrasing
8. **Final review** — Verify the complete response against all guidelines
