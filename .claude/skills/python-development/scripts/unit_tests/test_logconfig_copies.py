"""Every skill's logconfig/ copy must match the python-development original.

Skills that run Python carry their own copy of logconfig/ so each one stands
alone -- a skill can be copied into a project, or moved to another machine,
without dragging python-development along. The cost of that independence is
copies that can silently drift, so it is paid here: python-development's copy
is canonical, and any divergence fails the suite with the offending file named.

Updating logconfig means updating every copy. Run:

    python .claude/skills/python-development/scripts/unit_tests/sync_logconfig.py
"""

import hashlib
from pathlib import Path

import pytest

# The importer test asks the same question sync_logconfig.py asks when it decides
# where to write, so it borrows that answer rather than keeping a second copy of
# the rule that could disagree with the script it tells you to run.
from sync_logconfig import imports_logconfig

# unit_tests/ -> scripts/ -> python-development/ -> skills/
_SKILLS_DIR = Path(__file__).resolve().parents[3]
_CANONICAL = _SKILLS_DIR / "python-development" / "scripts" / "logconfig"


def _digest(path: Path) -> str:
    """Hash a file's bytes.

    Args:
        path: The file to hash.

    Returns:
        Hex sha256 of the file's contents.
    """
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _skill_of(script: Path) -> Path:
    """Find the skill directory a shipped script belongs to.

    Scripts sit at varying depths (`scripts/`, `scripts/data_validation/`,
    `scripts/unit_tests/fixtures/`), so climb to the `scripts/` root rather
    than counting parents.

    Args:
        script: A .py file somewhere under a skill's scripts/ folder.

    Returns:
        The skill's own directory.
    """
    scripts_dir = next(p for p in script.parents if p.name == "scripts")
    return scripts_dir.parent


def _copies() -> list[Path]:
    """Find every logconfig/ package in the skill tree except the canonical one.

    Returns:
        The copies' directories, sorted, canonical excluded.
    """
    found = (
        p.parent
        for p in _SKILLS_DIR.glob("*/scripts/logconfig/__init__.py")
        if p.parent != _CANONICAL
    )
    return sorted(found)


def test_canonical_logconfig_exists() -> None:
    """The copies are compared against something that is actually there."""
    assert (_CANONICAL / "logconfig.py").is_file(), f"missing canonical: {_CANONICAL}"


def test_every_skill_that_logs_carries_a_copy() -> None:
    """A skill importing logconfig must ship it, or it cannot stand alone.

    Guards the reverse of the drift check: a new skill that reaches across to
    python-development instead of vendoring would leave no copy to compare.
    """
    importers = {
        _skill_of(p)
        for p in _SKILLS_DIR.glob("*/scripts/**/*.py")
        if "logconfig" not in p.parts and imports_logconfig(p)
    }
    carriers = {d.parent.parent for d in _copies()} | {_skill_of(_CANONICAL)}
    missing = sorted(s.name for s in importers - carriers)
    assert not missing, f"skills import logconfig without carrying a copy: {missing}"


@pytest.mark.parametrize("copy_dir", _copies(), ids=lambda d: d.parents[1].name)
def test_copy_matches_canonical(copy_dir: Path) -> None:
    """A skill's copy is byte-identical to python-development's.

    Args:
        copy_dir: The skill's logconfig/ package directory.
    """
    canonical_files = sorted(
        p.name for p in _CANONICAL.iterdir() if p.suffix == ".py"
    )
    copy_files = sorted(p.name for p in copy_dir.iterdir() if p.suffix == ".py")
    assert copy_files == canonical_files, (
        f"{copy_dir} has a different file set than {_CANONICAL}"
    )

    for name in canonical_files:
        assert _digest(copy_dir / name) == _digest(_CANONICAL / name), (
            f"{copy_dir / name} has drifted from {_CANONICAL / name}; "
            f"re-run sync_logconfig.py"
        )
