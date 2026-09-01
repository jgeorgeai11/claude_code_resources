"""Copy python-development's logconfig/ over every other skill's copy.

Skills that run Python carry their own logconfig/ so each one stands alone.
python-development's copy is canonical; this script pushes it out to the rest
and is the fix for a failing test_logconfig_copies.py. Run it after changing
logconfig, from anywhere:

    python .claude/skills/python-development/scripts/unit_tests/sync_logconfig.py

A skill that imports logconfig without carrying a copy gets one created.
"""

import re
import shutil
import sys
from pathlib import Path

# _HERE is unit_tests/; parents climb scripts/ -> python-development/ -> skills/
_HERE = Path(__file__).resolve().parent
_SKILLS_DIR = _HERE.parents[2]
_REPO_ROOT = _SKILLS_DIR.parents[1]
_CANONICAL = _SKILLS_DIR / "python-development" / "scripts" / "logconfig"

sys.path.insert(0, str(_HERE.parent))
from logconfig import setup_logging, get_logger

logger = get_logger(__name__)

# A script depends on the package by importing it: `from logconfig import ...`,
# `from logconfig.logconfig import ...`, or a plain `import logconfig`. Matching
# the shape of an import statement rather than the bare word keeps a mention in
# prose -- a docstring, a comment, a path in a message -- from counting.
_IMPORT_PATTERN = re.compile(
    r"^\s*(?:from\s+logconfig(?:\.\w+)*\s+import\b|import\s+logconfig\b)",
    re.MULTILINE,
)


def imports_logconfig(script: Path) -> bool:
    """Report whether a script imports the logconfig package.

    Shared with `test_logconfig_copies.py`, which asks the same question of the
    same files: the sync and the check that guards it cannot disagree on what
    counts as an importer.

    Args:
        script: The .py file to inspect.

    Returns:
        True if any line of the file is a logconfig import statement.
    """
    return bool(_IMPORT_PATTERN.search(script.read_text(encoding="utf-8")))


def find_targets() -> list[Path]:
    """Find every skill that should carry a logconfig/ copy.

    A skill qualifies by already having a copy, or by importing logconfig from
    any script it ships. python-development itself is excluded — it is the
    source, not a target.

    Returns:
        The logconfig/ directories to write, sorted, whether or not they exist.
    """
    targets: set[Path] = set()

    for init in _SKILLS_DIR.glob("*/scripts/logconfig/__init__.py"):
        targets.add(init.parent)

    for script in _SKILLS_DIR.glob("*/scripts/**/*.py"):
        if "logconfig" in script.parts:
            continue
        if imports_logconfig(script):
            # scripts/ is the skill's package root, wherever the importer sits.
            skill_scripts = next(
                p for p in script.parents if p.name == "scripts"
            )
            targets.add(skill_scripts / "logconfig")

    targets.discard(_CANONICAL)
    return sorted(targets)


def sync(target: Path) -> bool:
    """Make one skill's copy identical to the canonical package.

    Args:
        target: The skill's logconfig/ directory, existing or not.

    Returns:
        True if anything was written, False if the copy already matched.
    """
    source_files = sorted(p for p in _CANONICAL.iterdir() if p.suffix == ".py")
    changed = False

    target.mkdir(parents=True, exist_ok=True)
    for source in source_files:
        destination = target / source.name
        if (
            destination.is_file()
            and destination.read_bytes() == source.read_bytes()
        ):
            continue
        shutil.copyfile(source, destination)
        logger.info(f"Wrote {destination}")
        changed = True

    # A file the canonical package no longer has would fail the file-set check.
    expected = {p.name for p in source_files}
    for stale in target.iterdir():
        if stale.suffix == ".py" and stale.name not in expected:
            stale.unlink()
            logger.info(f"Removed stale {stale}")
            changed = True

    return changed


def main() -> None:
    """Sync every skill's logconfig/ copy against the canonical one."""
    # This script maintains one fixed tree, and the docstring promises it runs
    # from anywhere, so its log goes to that tree's logs/ rather than to a
    # cwd-relative path that would scatter a logs/ folder wherever it was called.
    setup_logging(
        log_dir=_REPO_ROOT / "logs" / "python_development" / "unit_tests",
        overwrite=True,
    )
    logger.info("=" * 60)

    if not (_CANONICAL / "logconfig.py").is_file():
        logger.error(f"Canonical package not found: {_CANONICAL}")
        sys.exit(1)

    targets = find_targets()
    logger.info(f"Canonical: {_CANONICAL}")
    logger.info(f"Found {len(targets)} skill(s) to sync")

    updated = [target for target in targets if sync(target)]

    if updated:
        logger.info(f"Updated {len(updated)} of {len(targets)} copies")
    else:
        logger.info(f"All {len(targets)} copies already matched")
    logger.info("=" * 60)


if __name__ == "__main__":
    main()
