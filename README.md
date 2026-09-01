# Claude Resources

A shared library of Claude Code resources.

## Prerequisites

Before using these resources, make sure you have:

- A **GitHub account** with access to the project
- **Git** installed
- **VS Code** installed
- **Claude Code** installed

## Getting Started

### Step 1: Set up authentication (one-time setup)

Git authenticates to GitHub through [Git Credential Manager](https://github.com/git-ecosystem/git-credential-manager) (GCM), which is installed with Git for Windows and enabled by default — there is nothing to install or configure. You sign in through your browser and GCM stores the credential securely, so you never create, copy, or paste a token.

The first time you clone (Step 2), a browser window opens asking you to sign in to GitHub and authorize Git Credential Manager. Approve it once — the credential is saved, and every clone, pull, and push after that runs without a prompt.

### Step 2: Clone

Clone this repo to any location on your machine that is separate from your project (e.g., your home directory).

```powershell
git clone https://github.com/jgeorgeai11/claude_code_resources.git $HOME\claude_code_resources
```

Confirm it worked. You should see `origin` pointing to the GitHub repo.

```powershell
git remote -v
```

### Step 3: Copy

Copy the contents of the `.claude/` folder from your cloned `claude_code_resources` repo into your project's root directory.
> **Note:** This merges the shared resources into your existing `.claude/` folder. It will overwrite files with the same name, but won't delete any files that only exist in your project. To avoid conflicts, give your project-specific resources unique names that don't match the shared ones.

```powershell
Copy-Item -Recurse -Force $HOME\claude_code_resources\.claude\* your-project\.claude\
```

Once copied, the resources are immediately available in your Claude Code sessions. You can add your own project-specific resources alongside the shared ones in `.claude/`.

> **Don't import `logconfig` from `.claude/`.** It is the one piece of these resources that your project's own code would import at runtime, and `.claude/` is gitignored in most projects — so CI has nothing to import, and Step 4 overwrites it in place without a diff in your repo. Copy `.claude/skills/python-development/scripts/logconfig/` into your tracked source tree and commit it. See [logging.md](.claude/skills/python-development/core/logging.md).

### Step 4: Update

When new versions are released, pull the latest changes into your cloned `claude_code_resources` repo and re-copy into your project. Check the [CHANGELOG.md](CHANGELOG.md) to see what changed, or pull a specific version tag (e.g., `git checkout v2026.02.26.0` after `git pull origin main`) if you don't want the latest.

Pull the latest version:

```powershell
cd $HOME\claude_code_resources
git pull origin main
```

Confirm the pull succeeded. You should see the latest commits.

```powershell
git log --oneline -5
```

Then re-copy into your project by following **Step 3** above.

## Reporting Issues

Use the GitHub issue tracker to report bugs or request features. Go to **Issues > New issue** in the GitHub project and describe the problem or request, naming which resource is affected (e.g., the `python-development` skill, the README) so it can be triaged.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for development guidelines and [MAINTAINING.md](MAINTAINING.md) for maintainer responsibilities.
