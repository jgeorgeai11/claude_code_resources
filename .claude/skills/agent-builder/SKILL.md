---
name: agent-builder
description: Build and maintain agents. Use when creating new agents or reviewing existing ones.
argument-hint: "[agent-name]"
---

# agent-builder

## Guidelines

1. **Frontmatter required** — YAML between `---` delimiters; see Frontmatter Fields in the Reference section for required fields and format
2. **Naming conventions** — H1 uses kebab-case matching the `name` field; filename must also match (e.g., `code-review-agent.md` with `name: code-review-agent` and `# code-review-agent`); agent name must end with `-agent`
3. **Build style** — Provide the agent's workflow one of two ways:
   - 3.1. **Self-contained** — The agent carries its own workflow in its body, running the same whether Claude delegates to it directly or an orchestrator skill spawns one per unit
   - 3.2. **Thin + directing skill** — The agent body is only a Role; it preloads a directing skill (`skills` field) that provides the workflow
4. **Body sections by build style**
   - 4.1. **Self-contained** — A `## Role` section, then build the rest like a skill: `## Guidelines`, `## Reference`, and `## Workflow` per the skill-builder skill's conventions
   - 4.2. **Thin** — Only a `## Role` section: an identity statement and an optional numbered expertise list

## Reference

### File Location

```
.claude/agents/
└── agent-name.md
```

### Frontmatter Fields

| Field | Required | Purpose | Example |
|-------|----------|---------|---------|
| **name** | Yes | Agent identifier (kebab-case, matches filename) | `code-implementation-agent` |
| **description** | Yes | Brief role + delegation cue: "Use when [trigger]" for a directly-delegated agent, or "not for direct use — invoke the `<skill>` skill" for a worker | `Coding specialist. Use when executing activity plans or ad-hoc Python/SQL tasks.` |
| **tools** | No | Comma-separated allowlist; inherits all if omitted | `Read, Write, Edit, Grep, Glob, Bash` |
| **disallowedTools** | No | Tools to deny, removed from inherited or specified list | `Write, Edit` |
| **model** | No | `sonnet`, `opus`, `haiku`, `fable`, a full model ID, or `inherit` (default: `inherit`) | `sonnet` |
| **permissionMode** | No | `default`, `acceptEdits`, `auto`, `dontAsk`, `bypassPermissions`, `plan`, `manual` | `default` |
| **maxTurns** | No | Maximum agentic turns before stopping | `20` |
| **skills** | No | Skills to preload at startup (full content injected); each must be model-invocable (a `disable-model-invocation: true` skill is silently skipped) | `api-conventions` |
| **mcpServers** | No | MCP servers available to this agent | `slack` |
| **hooks** | No | Lifecycle hooks scoped to this agent | See docs |
| **memory** | No | Persistent memory scope: `user`, `project`, or `local` | `project` |
| **effort** | No | Reasoning effort while active: `low`, `medium`, `high`, `xhigh`, `max` | `high` |
| **background** | No | Always run as background task (default: `false`) | `true` |
| **isolation** | No | Set to `worktree` for isolated git worktree | `worktree` |
| **color** | No | Display color: `red`, `blue`, `green`, `yellow`, `purple`, `orange`, `pink`, `cyan` | `blue` |
| **initialPrompt** | No | Auto-submitted first turn when run as a main session (via `--agent`) | `Start the review` |

### Common Tools

| Tool | Purpose |
|------|---------|
| `Read` | Read files |
| `Write` | Create new files |
| `Edit` | Modify existing files |
| `Grep` | Search file contents |
| `Glob` | Find files by pattern |
| `Bash` | Run shell commands |
| `mcp__*` | All MCP server tools |
| `Agent(name)` | Restrict which subagents can be spawned — applies only to a main-thread agent; a subagent that lists `Agent` spawns nested agents but ignores the name list |

## Workflow

1. **Read relevant files** — For a self-contained agent, read the [skill-builder](../skill-builder/SKILL.md) skill, whose conventions the agent's body follows (per guideline 4.1)
2. **If creating a new agent**
   - 2.1. **Create agent file** — `.claude/agents/{agent-name}.md`; set up frontmatter, H1, and Role section with an identity statement and optional expertise list
   - 2.2. **Provide the workflow** — Self-contained: build the body per guideline 4.1. Thin: ensure the directing skill it preloads (`skills` field) exists; if not, have the user create it with `/skill-builder`
3. **If reviewing an existing agent**
   - 3.1. **Read the target** — Read `$ARGUMENTS` agent file; identify all sections present
   - 3.2. **Review** — Check against all Guidelines
   - 3.3. **Present findings** — Show a compliance table (see Compliance Table example) and a summary of proposed changes; wait for user approval before proceeding
   - 3.4. **Apply fixes** — Restructure and rewrite to resolve all approved issues
4. **Validate** — Walk through all Guidelines; fix any violations

## Examples

### Compliance Table

| # | Guideline | Status | Notes |
|---|-----------|--------|-------|
| 1 | Frontmatter required | Pass | |
| 2 | Naming conventions | Fail | Agent name `reviewer.md` does not end with `-agent` |
| 3 | Build style | Fail | Neither self-contained (no workflow in body) nor thin+directing (no directing skill) |
| 4 | Body sections by build style | Fail | Self-contained agent but missing a `## Workflow` section |

### Role Section

```markdown
## Role

You are a code review specialist. You review a single file (the path provided in your prompt) against project coding standards — one file per invocation.
```
