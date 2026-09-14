"""Guards — checks that make fence and schema violations fail in CI.

Three checks, each study-agnostic and driven by a small JSON file the study
keeps in its ``fence/`` directory:

``fence/protected-files.json``::

    {
      "protected_filenames": ["raw_rows.csv"],         # fenced raw data files
      "allowed_readers": ["data_access.py"],           # study-relative paths that
                                                       # may open them (the one
                                                       # module that goes through
                                                       # studies.access)
      "restricted_content_globs": ["data/*.csv",       # must never be tracked
                                   "data/raw/**"]      # in git
    }

``fence/guard-baseline.json`` (optional) records violations that existed when
the guard was introduced so the check can be ratcheted down instead of
blocking on day one::

    {
      "direct_reads": {"scripts/legacy.py": 2},        # file -> count allowed
      "tracked_restricted": ["data/raw/"]              # path prefixes tolerated
    }

A violation not covered by the baseline fails. A baseline entry whose count
goes down should be lowered in the file so it cannot creep back up.
"""

from __future__ import annotations

import fnmatch
import json
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

PROTECTION_FILE = "fence/protected-files.json"
BASELINE_FILE = "fence/guard-baseline.json"


@dataclass(frozen=True)
class DirectRead:
    path: str  # study-relative
    line: int
    filename: str  # the protected filename referenced

    def __str__(self) -> str:
        return f"{self.path}:{self.line} references {self.filename}"


# ----------------------------------------------------------------------
# Loading
# ----------------------------------------------------------------------


def load_protection(study_root: str | Path) -> dict | None:
    """Return the study's protection config, or None if it has none."""
    path = Path(study_root) / PROTECTION_FILE
    if not path.exists():
        return None
    data = json.loads(path.read_text())
    data.setdefault("protected_filenames", [])
    data.setdefault("allowed_readers", [])
    data.setdefault("restricted_content_globs", [])
    return data


def load_baseline(study_root: str | Path) -> dict:
    path = Path(study_root) / BASELINE_FILE
    if not path.exists():
        return {"direct_reads": {}, "tracked_restricted": []}
    data = json.loads(path.read_text())
    data.setdefault("direct_reads", {})
    data.setdefault("tracked_restricted", [])
    return data


# ----------------------------------------------------------------------
# Check 1 — direct reads of fenced raw files
# ----------------------------------------------------------------------


def find_direct_reads(study_root: str | Path, protection: dict) -> list[DirectRead]:
    """Every line in the study's Python files that names a protected file.

    Naming the file is the signal: a script that mentions ``raw_rows.csv``
    is opening it, or building a path to it. Files listed in
    ``allowed_readers`` are skipped; everything else, including tests and
    one-off scripts, is scanned.
    """
    root = Path(study_root)
    filenames = [f for f in protection.get("protected_filenames", []) if f]
    if not filenames:
        return []
    allowed = {str(Path(p)) for p in protection.get("allowed_readers", [])}
    pattern = re.compile("|".join(re.escape(f) for f in filenames))

    hits: list[DirectRead] = []
    for py in sorted(root.rglob("*.py")):
        rel = str(py.relative_to(root))
        if rel in allowed or "__pycache__" in rel:
            continue
        try:
            text = py.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        for lineno, line in enumerate(text.splitlines(), start=1):
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            m = pattern.search(line)
            if m:
                hits.append(DirectRead(path=rel, line=lineno, filename=m.group(0)))
    return hits


def new_direct_reads(hits: Iterable[DirectRead], baseline: dict) -> list[DirectRead]:
    """Hits not covered by the baseline (new file, or count above baseline)."""
    allowed_counts: dict[str, int] = {
        str(Path(k)): int(v) for k, v in baseline.get("direct_reads", {}).items()
    }
    by_file: dict[str, list[DirectRead]] = {}
    for h in hits:
        by_file.setdefault(h.path, []).append(h)
    new: list[DirectRead] = []
    for path, file_hits in by_file.items():
        allowed = allowed_counts.get(path, 0)
        if len(file_hits) > allowed:
            new.extend(file_hits[allowed:] if allowed else file_hits)
    return new


# ----------------------------------------------------------------------
# Check 2 — restricted content tracked in git
# ----------------------------------------------------------------------


def git_tracked_files(repo_root: str | Path, subdir: str | Path) -> list[str] | None:
    """Paths tracked by git under ``subdir``, relative to ``subdir``.

    Returns None when ``repo_root`` is not a git checkout (e.g. an exported
    tarball), so callers can skip rather than fail.
    """
    repo_root = Path(repo_root).resolve()
    sub = Path(subdir)
    if sub.is_absolute():
        try:
            sub = sub.resolve().relative_to(repo_root)
        except ValueError:
            return None
    try:
        out = subprocess.run(
            ["git", "-C", str(repo_root), "ls-files", "--", sub.as_posix()],
            capture_output=True,
            text=True,
            check=True,
        ).stdout
    except (FileNotFoundError, subprocess.CalledProcessError):
        return None
    prefix = sub.as_posix().rstrip("/") + "/"
    rels: list[str] = []
    for line in out.splitlines():
        rels.append(line[len(prefix):] if line.startswith(prefix) else line)
    return rels


def find_tracked_restricted(tracked: Iterable[str], protection: dict) -> list[str]:
    """Tracked paths (study-relative) matching any restricted-content glob."""
    globs = protection.get("restricted_content_globs", [])
    out: list[str] = []
    for rel in tracked:
        if Path(rel).name == ".gitignore":  # the ignore file that keeps data out is not data
            continue
        for g in globs:
            if fnmatch.fnmatch(rel, g) or fnmatch.fnmatch(rel, g.replace("**", "*")):
                out.append(rel)
                break
    return sorted(out)


def new_tracked_restricted(paths: Iterable[str], baseline: dict) -> list[str]:
    prefixes = [str(p) for p in baseline.get("tracked_restricted", [])]
    return [p for p in paths if not any(p.startswith(pre) for pre in prefixes)]


# ----------------------------------------------------------------------
# Check 3 — results file against its frozen schema
# ----------------------------------------------------------------------


def validate_results(results: dict | str | Path, schema: dict | str | Path) -> list[str]:
    """Return a list of validation messages; empty means the file conforms.

    Uses ``jsonschema`` (Draft 2020-12). The runner should call this before
    writing its results file and refuse to write a non-conforming one.
    """
    try:
        import jsonschema
    except ImportError as e:  # pragma: no cover - environment
        raise RuntimeError("validate_results needs the 'jsonschema' package") from e

    if not isinstance(results, dict):
        results = json.loads(Path(results).read_text())
    if not isinstance(schema, dict):
        schema = json.loads(Path(schema).read_text())

    validator = jsonschema.Draft202012Validator(schema)
    errors = sorted(validator.iter_errors(results), key=lambda e: list(e.path))
    return [f"{'/'.join(str(p) for p in e.path) or '<root>'}: {e.message}" for e in errors]


# ----------------------------------------------------------------------
# Whole-study report
# ----------------------------------------------------------------------


def run_guards(repo_root: str | Path, study_root: str | Path) -> dict:
    """Run every check for one study and return a report dict.

    ``report["ok"]`` is True when nothing new (beyond the baseline) was found.
    """
    study_root = Path(study_root)
    protection = load_protection(study_root)
    if protection is None:
        return {"study": study_root.name, "skipped": "no fence/protected-files.json", "ok": True}
    baseline = load_baseline(study_root)

    hits = find_direct_reads(study_root, protection)
    new_reads = new_direct_reads(hits, baseline)

    tracked = git_tracked_files(repo_root, study_root)
    if tracked is None:
        restricted, new_restricted = [], []
        git_note = "not a git checkout; restricted-content check skipped"
    else:
        restricted = find_tracked_restricted(tracked, protection)
        new_restricted = new_tracked_restricted(restricted, baseline)
        git_note = None

    return {
        "study": study_root.name,
        "direct_reads_total": len(hits),
        "direct_reads_new": [str(h) for h in new_reads],
        "tracked_restricted_total": len(restricted),
        "tracked_restricted_new": new_restricted,
        "tracked_restricted_baselined": [
            p for p in restricted if p not in new_restricted
        ],
        "git_note": git_note,
        "ok": not new_reads and not new_restricted,
    }
