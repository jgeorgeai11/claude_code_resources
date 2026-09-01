# Maintaining

This document outlines the responsibilities of those with Maintain or Admin access to this project. See [CONTRIBUTING.md](CONTRIBUTING.md) for the contributor workflow and [README.md](README.md) for end-user setup.

## Pull Request Review

- Review incoming pull requests in a timely manner
- Ensure changes follow project conventions and don't break existing resources
- Verify the CHANGELOG has been updated with the correct version
- Provide clear, constructive feedback when requesting changes
- Merge pull requests into `main`

## Tagging a Release

After a pull request is merged into `main`, tag the new version. The version and CHANGELOG entry should already be included in the merged changes. The tag must match the version in the CHANGELOG. This project uses [Calendar Versioning](https://calver.org/) in `YYYY.MM.DD.N` format, where the fourth segment is an incrementing number starting at `0` (e.g., `v2026.02.26.0`, `v2026.02.26.1`).

```powershell
git checkout main
git pull origin main
git tag -a v<version> -m "v<version> - Brief description of release"
git push origin v<version>
```

Confirm the tag was created. You should see your new version in the list.

```powershell
git tag
```

## Branch Management

- Merged branches are deleted automatically; monitor and clean up any stale branches that remain
- The `main` ruleset also blocks force pushes and deletion of `main`, and applies to everyone — Admins included, since it has no bypass actors. Changing that is done in **Settings > Rules > Rulesets**

## Project Health

- Triage incoming issues and assign appropriate labels
- Keep documentation (README, CONTRIBUTING, CHANGELOG) accurate and up to date
- Manage collaborators, permissions, and rulesets on GitHub (requires Admin, not Maintain)
