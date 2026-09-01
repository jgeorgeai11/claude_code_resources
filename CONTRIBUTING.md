# Contributing

This document explains how to contribute to this project, from setting up your environment to submitting a pull request.

## Prerequisites

Before contributing, make sure you have:

- A **GitHub account** with write access to the project
- **Git** installed
- **VS Code** installed
- **Claude Code** installed
- **gh** (GitHub CLI) installed — used to create pull requests from the terminal (optional; you can also use the GitHub web UI)

## Development Strategy

This project uses [GitHub Flow](https://docs.github.com/en/get-started/using-github/github-flow) as its branching model. All work happens on feature branches that merge directly into `main` via reviewed pull requests. Every merge to `main` is a release — end users always pull the latest from `main`.

No one pushes directly to `main` — a branch ruleset blocks it, so every change goes through a pull request. Review is expected before merging, but GitHub does not require a recorded approval, since that would leave a sole author unable to merge their own work.

| Branch | Purpose | Merge Target |
|--------|---------|--------------|
| `main` | Stable, reviewed resources used by end users | -- |
| `feature/*` | New features or enhancements | `main` |
| `bugfix/*` | Bug fixes | `main` |

## Creating a Branch

A branch is a separate copy of the codebase where you can make changes without affecting anyone else's work. You always create a new branch for each change you're working on.

### Step 1: Set up authentication (one-time setup)

Git authenticates to GitHub through [Git Credential Manager](https://github.com/git-ecosystem/git-credential-manager) (GCM), which is installed with Git for Windows and enabled by default — there is nothing to install or configure. You sign in through your browser and GCM stores the credential securely, so you never create, copy, or paste a token.

The first time you clone (Step 2), a browser window opens asking you to sign in to GitHub and authorize Git Credential Manager. Approve it once — the credential is saved, and every clone, pull, and push after that runs without a prompt.

### Step 2: Clone

Clone the repository to the directory you want to work out of. You only need to do this once:

```powershell
git clone https://github.com/jgeorgeai11/claude_code_resources.git $HOME\projects\development\claude_code_resources
cd $HOME\projects\development\claude_code_resources
```

Confirm it worked. You should see `origin` pointing to the GitHub repo.

```powershell
git remote -v
```

### Step 3: Pull latest `main`

You always start from `main` and pull the latest changes so you're not working on outdated code:

```powershell
git checkout main
git pull origin main
```

### Step 4: Create a new branch

This gives you an isolated space to make changes without affecting `main` or anyone else's work. Give it a short, descriptive name prefixed with `feature/` (or `bugfix/` for fixes):

```powershell
git checkout -b feature/short-description
```

If there is a related GitHub issue, include the issue number in the branch name so the branch is easy to trace:

```powershell
git checkout -b feature/42-add-new-resource
```

> **Note:** The branch name alone does not link the branch to the issue. To create the link — and close the issue automatically when the pull request merges — write `Closes #42` in the pull request description.

Confirm you're on your new branch. The current branch will have a `*` next to it.

```powershell
git branch
```

### Step 5: Stage and commit

After making your edits, you need to tell git which files to include (`git add`) and save a snapshot of your work with a message describing what you changed (`git commit`). Commit regularly as you work rather than saving everything for one large commit at the end — small, frequent commits make it easier to track what changed, simplify code review, and let you undo specific changes if something goes wrong.

First, review what changed:

```powershell
git status
```

Then stage the specific files you want to include:

```powershell
git add file1.md file2.md
git commit -m "Brief description of what you changed"
```

Confirm your changes are committed. You should see "nothing to commit, working tree clean".

```powershell
git status
```

### Step 6: Pull latest `main` and resolve conflicts

Before pushing, make sure your branch is compatible with the latest `main`. While you've been working on your branch, others may have merged changes into `main` that touch the same files. Pulling now lets you resolve any conflicts locally rather than during review. If your branch lives for a while, do this periodically — not just before pushing — to avoid large conflicts building up:

```powershell
git pull origin main
```

Conflicts only happen in files where both you and someone else changed the same lines. If `main` has new changes to files you didn't touch, git merges those automatically — you don't need to do anything with them. If there are conflicts, open the conflicting files in your editor and resolve them.

Then stage the resolved files — this tells git you've handled the conflicts — and commit:

```powershell
git add file1.md file2.md
git commit -m "Resolve merge conflicts with main"
```

If there are no conflicts, you're ready for the next step.

### Step 7: Update the Changelog

Update `CHANGELOG.md` with an entry describing your change under the next version number. Follow the [Keep a Changelog](https://keepachangelog.com/) format:

```markdown
## [vYYYY.MM.DD.N] - YYYY-MM-DD

### Added
- New resources or features

### Changed
- Updates to existing resources

### Fixed
- Bug fixes or corrections
```

This project uses [Calendar Versioning](https://calver.org/) in `YYYY.MM.DD.N` format, where the fourth segment is an incrementing number starting at `0` (e.g., `v2026.02.26.0`, `v2026.02.26.1`).

> **Note:** If another PR merges before yours with the same version, pull the latest `main`, increment the fourth segment, and push again.

### Step 8: Review and test

Whether or not there were conflicts, review and test your changes after pulling to make sure everything still works as expected.

Skills that ship runnable Python each carry their own copy of `logconfig/`, so a skill stands alone when it is copied into a project or moved to another machine. `python-development`'s copy is the canonical one. If you changed it, push it out to the others — the test suite fails on any copy that has drifted:

```powershell
python .claude/skills/python-development/scripts/unit_tests/sync_logconfig.py
python -m pytest .claude/skills
```

### Step 9: Push

This uploads your branch so others can see your work and you can open a pull request. Your changes only exist on your machine until you push:

```powershell
git push -u origin feature/short-description
```

Confirm the push succeeded. You should see "Your branch is up to date with 'origin/feature/...'".

```powershell
git status
```

## Pull Request Process

A pull request (PR) is how you propose your changes to be included in the project. It lets others review your work before it's accepted.

1. After pushing your branch, go to the project on GitHub. You'll see a prompt to **Compare & pull request** — click it, or go to **Pull requests > New pull request**. From the terminal, you can instead run `gh pr create`
2. Set the **base branch** to `main` and the **compare branch** to your feature branch
3. Add a title and description explaining what you changed and why
4. Submit the pull request and wait for review

Once submitted:

- Your pull request will be reviewed, and you may be asked to make changes before it is merged. Anyone with Read access can review and comment; merging requires Write, Maintain, or Admin access

Once merged:

- Your changes are now on `main` and immediately available to end users
- **Squash merging** is the only merge method enabled — all commits in your branch are combined into one commit on `main`, titled with your pull request title
- Your feature branch is **automatically deleted from GitHub**
- **Clean up your local branch** — the remote branch is deleted automatically, but the local copy remains on your machine. Delete it to keep things tidy:

   ```powershell
   git checkout main
   git pull origin main
   git branch -D feature/short-description
   ```

   > **Why `-D` and not `-d`?** Squash merging replaces your branch's commits with a single new commit on `main`, so git can't see your branch as merged and the safer `-d` fails with "the branch is not fully merged." That error is expected here and does not mean anything went wrong. If you want to confirm your work really did land before force-deleting, `git diff feature/short-description main` should come back empty.

## Roles

GitHub's built-in repository roles, and how this project uses them:

| Role | Permissions |
|------|-------------|
| **Admin** | Full access, including repository settings, collaborator management, rulesets, and deletion |
| **Maintain** | Manage the repository — description, topics, merge settings — without sensitive or destructive actions (see [MAINTAINING.md](MAINTAINING.md)) |
| **Write** | Push branches, open and merge pull requests, create releases and tags |
| **Triage** | Manage issues and pull requests — labels, assignment, closing — without push access |
| **Read** | View and clone the repository, open issues, comment on pull requests |
