---
name: skill-builder
description: Build and maintain skills. Use when creating new skills or reviewing existing ones.
argument-hint: "[skill-name]"
---

# skill-builder

## Guidelines

1. **Frontmatter required** — YAML between `---` delimiters on both SKILL.md and sub-docs; see Frontmatter Fields in the Reference section for available fields and format
2. **H1 and frontmatter name must match** — All kebab-case; for SKILL.md, must also match the skill folder name; for sub-docs, filename must also match (e.g., `type-hints.md` with `name: type-hints` and `# type-hints`)
3. **Enforce folder structure** — Skill folders must match the Folder Structure in the Reference section; no other files or directories allowed at the top level
4. **Use numbered steps and substeps** — Both Guidelines and Workflow use numbered steps; substeps as `- N.N.` bullet points (e.g., `- 5.1. **Label** — explanation`); further nesting uses `- N.N.N.`
5. **Keep steps scannable** — One line per step; each item: `**Bold label** — explanation`; consolidate related items into a single step with substeps
6. **Reference is for lookup, not prose** — Use tables for structured data; brief descriptions above a table are fine, but avoid long paragraphs
7. **Examples must be concrete** — Real examples, not pseudocode or placeholders; can be inline or references to files in `references/`
8. **No duplication across sections** — Each fact lives in one place only; prefer placing structural details in other sections (Reference, Examples) to keep Guidelines focused on behavioral rules
9. **Internal consistency** — A skill must contain no contradictions — within a file (e.g., between guidelines, between a guideline and the workflow, etc.) or across files (SKILL.md, sub-docs, reference files) — and any templates or examples must conform to the guidelines
10. **No orphaned docs** — Every non-code file (sub-docs, references, templates) must be linked from SKILL.md or another sub-doc
11. **Core skills require an enforcement guideline** — If a skill has core sub-docs, its first guideline must be: `1. **Always apply all core skills** — Only skip a core skill if the user explicitly asks to ignore it`
12. **Invocable skills** — For a skill invoked as a command (`/skill-name`):
   - 12.1. **Workflow section if it runs a procedure** — Step-by-step process; use `$ARGUMENTS` to reference user-provided input; omit for pure knowledge/reference skills
   - 12.2. **`argument-hint` frontmatter if relevant** — Describe expected arguments (e.g., `[filepath]`, `[issue-number]`)
13. **Skills that direct subagent work** — choose by unit count and isolation need:
   - 13.1. **Orchestrator + workers (parallel)** — Skill fans out one worker subagent per unit (e.g., one file each), coordinating and summarizing but never working inline; the standard pattern (see the bundled `/batch` skill)
     - 13.1.1. **Config** — Normal model/user-invocable skill (no `context: fork`); the Workflow spawns workers via the Agent tool; pre-approve with `allowed-tools: Agent(worker-type)`
     - 13.1.2. **Workers** — Self-contained custom agents in `.claude/agents/`; each carries its own instructions
   - 13.2. **Single forked run** — Skill runs its own body as one isolated subagent via `context: fork` + `agent: <type>` (built-in `Explore`/`Plan`/`general-purpose`, or a custom agent); use to keep a single verbose run off the main context (no fan-out)
14. **Keep SKILL.md lean and imperative** — Target under 150 lines and 1,500 words; move detailed content to `references/` if needed; use verb-first instructions (e.g., "Parse the file"), not second person

## Reference

### Folder Structure

```
.claude/skills/
└── skill-name/
    ├── SKILL.md              # Entry point (always required)
    ├── core/                 # Sub-docs that always apply
    │   └── topic.md
    ├── optional/             # Sub-docs loaded when relevant
    │   └── topic.md
    ├── scripts/              # Reusable Python modules that project code imports
    │   └── module_name/
    │       └── module.py
    └── references/           # Non-code reference files (examples, templates, lookup data)
        └── file.ext
```

| Directory | When to Use |
|-----------|-------------|
| `core/` | Skill has multiple topics that always apply together |
| `optional/` | Skill has topics that only apply in certain contexts |
| `scripts/` | Skill provides reusable Python modules that project code imports; can have sub-modules |
| `references/` | Skill has non-code reference files (examples, templates, lookup data); can have sub-directories |

### Frontmatter Fields

| Field | Required | Purpose |
|-------|----------|---------|
| **name** | No | Display name and `/slash-command`; defaults to directory name. Lowercase letters, numbers, hyphens only (max 64 chars) |
| **description** | Recommended | What the skill does and when to use it. Claude uses this to decide when to load automatically |
| **when_to_use** | No | Extra trigger context appended to `description` in skill listings |
| **argument-hint** | No | Hint shown during autocomplete (e.g., `[issue-number]`, `[filename] [format]`) |
| **arguments** | No | Named positional arguments for `$name` substitution in the skill body |
| **disable-model-invocation** | No | `true` to prevent Claude from auto-loading (user must invoke with `/name`) and to block preloading into subagents. Default: `false` |
| **user-invocable** | No | `false` to hide from `/` menu; for background knowledge Claude loads automatically. Default: `true` |
| **paths** | No | Glob patterns that limit when the skill auto-activates |
| **allowed-tools** | No | Tools Claude can use without permission when skill is active |
| **disallowed-tools** | No | Tools removed from Claude's available pool while the skill is active |
| **model** | No | Model to use when skill is active |
| **effort** | No | Reasoning effort while skill is active: `low`, `medium`, `high`, `xhigh`, or `max` |
| **context** | No | `fork` to run in an isolated subagent context |
| **agent** | No | Subagent type when `context: fork` is set (e.g., `Explore`, `Plan`, or a custom agent name) |
| **shell** | No | Shell for `!command` execution: `bash` (default) or `powershell` |
| **hooks** | No | Hooks scoped to this skill's lifecycle |

### Skill Doc Layout

Both SKILL.md and individual skill docs (e.g., `core/logging.md`) follow this structure:

| Section | Required | Purpose |
|---------|----------|---------|
| **Guidelines** | Yes | Numbered rules for the skill |
| **Reference** | No | Lookup tables, argument docs, option lists |
| **Workflow** | No | Step-by-step process for multi-step procedures |
| **Examples** | No | Concrete examples of correct usage |
| **Anti-patterns** | No | "Don't do this" examples with explanation of why |

For SKILL.md with sub-docs, list them in the Reference section:

| Sub-section | Purpose |
|-------------|---------|
| **Core Skills** | Links to sub-docs that always apply. Prefix: "Read and apply:" |
| **Optional Skills** | Links to sub-docs loaded only when relevant. Prefix: "Load when relevant:" |

## Workflow

1. **If creating a new skill**
   - 1.1. **Create folder and SKILL.md** — Set up frontmatter, H1, and Guidelines section
   - 1.2. **Add optional sections** — Reference, Workflow, Examples, Anti-patterns; omit any that don't apply
   - 1.3. **Organize sub-docs** — If the skill needs sub-docs, place in `core/` or `optional/` and list in SKILL.md Reference section
2. **If reviewing an existing skill**
   - 2.1. **Read the target** — Read `$ARGUMENTS` (skill folder or individual sub-doc); identify all files and sections present
   - 2.2. **Review** — Check against applicable Guidelines (all apply to a skill folder; skip folder-level rules for a single sub-doc)
   - 2.3. **Present findings** — Show a compliance table per file (see Compliance Table example) and a summary of proposed changes; wait for user approval before proceeding
   - 2.4. **Apply fixes** — Restructure and rewrite to resolve all approved issues
3. **Validate** — Walk through all applicable Guidelines; fix any violations

## Examples

### Compliance Table

| # | Guideline | Status | Notes |
|---|-----------|--------|-------|
| 1 | Frontmatter required | Pass | |
| 2 | H1 and frontmatter name must match | Pass | |
| 3 | Enforce folder structure | Fail | Extra `utils/` directory at top level |
| 4 | Use numbered steps and substeps | Pass | |
| 5 | Keep steps scannable | Fail | Guideline 3 has multi-line explanation |
| 6 | Reference is for lookup, not prose | N/A | No Reference section present |
| ... | ... | ... | ... |

