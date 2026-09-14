"""Registration facts — the part of a pre-registration that is derived, not written.

Hand-written registration text drifts: it names models that were retired,
measure versions that moved on, bounds that were recomputed. This module
compiles the checkable facts of a study from the repository into
``registration/facts.md`` and can verify that the committed copy is current.
The prose registration cites the facts file instead of restating numbers.

Sources, all relative to the study root:

* ``fence/*-manifest.json`` and ``fence/state.json`` — partition hashes,
  row counts, seed, and the time the fence was created (lock state and
  access-log tallies change during the study and live on the gate board).
* ``config/*.json`` — every file's SHA-256, plus the generation config
  (model, parameters, conditions, template hash) when
  ``config/generation.json`` exists.
* ``measures/*.py`` — each measure's ``name``/``version`` class attributes
  and the file's SHA-256.
* ``config/results-schema.json`` — title, required keys, SHA-256.
* ``registration/facts-sources.json`` (optional) — extra JSON outputs to
  render as tables, e.g. reference spreads and power tables::

      {
        "tables": [
          {"file": "output/reference-table-v12.json", "title": "Reference spread",
           "path": "measures", "columns": ["n", "sd_rr", "sd_ref"]}
        ]
      }

The output is deterministic: it depends only on file contents, never on the
clock or the current commit, so ``check`` can run in CI.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

FACTS_FILE = "registration/facts.md"
SOURCES_FILE = "registration/facts-sources.json"

_NAME_RE = re.compile(r'^\s*name\s*=\s*"([^"]+)"', re.M)
_VERSION_RE = re.compile(r'^\s*version\s*=\s*"([^"]+)"', re.M)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _load_json(path: Path) -> dict | list | None:
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


# ----------------------------------------------------------------------
# Collection
# ----------------------------------------------------------------------


def collect_facts(study_root: str | Path) -> dict:
    root = Path(study_root)
    facts: dict = {"study_id": root.name}

    # Fence
    fence_dir = root / "fence"
    manifests = []
    for name in ("exploratory", "confirmatory"):
        m = _load_json(fence_dir / f"{name}-manifest.json")
        if isinstance(m, dict):
            manifests.append(
                {
                    "partition": name,
                    "row_count": m.get("row_count"),
                    "manifest_hash": m.get("manifest_hash"),
                    "seed": m.get("seed"),
                    "created_at": m.get("created_at"),
                }
            )
    state = _load_json(fence_dir / "state.json")
    fence_state = None
    if isinstance(state, dict):
        # Only what is fixed at the split. Lock state and access-log tallies
        # change during the study and belong to the gate board, not here.
        fence_state = {"locked_at": state.get("locked_at")}
    facts["fence"] = {"manifests": manifests, "state": fence_state}

    # Config files and generation config
    config_dir = root / "config"
    config_hashes = {}
    if config_dir.exists():
        for p in sorted(config_dir.iterdir()):
            if p.is_file() and not p.name.startswith("."):
                config_hashes[p.name] = _sha256(p)
    facts["config_hashes"] = config_hashes

    gen = _load_json(config_dir / "generation.json")
    generation = None
    if isinstance(gen, dict):
        keys = (
            "generator",
            "model",
            "api_parameters",
            "conditions",
            "archetype_version",
            "seed",
            "confirmatory_volume",
            "request_template_hash",
            "model_retirement",
        )
        generation = {k: gen[k] for k in keys if k in gen}
    facts["generation"] = generation

    # Measures
    measures = []
    mdir = root / "measures"
    if mdir.exists():
        for p in sorted(mdir.glob("*.py")):
            if p.name.startswith("__"):
                continue
            text = p.read_text(encoding="utf-8")
            names = _NAME_RE.findall(text)
            versions = _VERSION_RE.findall(text)
            if not names:  # base classes and helpers are not measures
                continue
            measures.append(
                {
                    "file": p.name,
                    "name": names[0] if names else None,
                    "version": versions[-1] if versions else None,
                    "sha256": _sha256(p),
                }
            )
    facts["measures"] = measures

    # Results schema
    schema_path = config_dir / "results-schema.json"
    schema = _load_json(schema_path)
    facts["results_schema"] = (
        {
            "title": schema.get("title") if isinstance(schema, dict) else None,
            "required": schema.get("required") if isinstance(schema, dict) else None,
            "sha256": _sha256(schema_path),
        }
        if schema is not None
        else None
    )

    # Extra tables
    tables = []
    sources = _load_json(root / SOURCES_FILE)
    if isinstance(sources, dict):
        for spec in sources.get("tables", []):
            fpath = root / spec["file"]
            data = _load_json(fpath)
            if data is None:
                tables.append({"title": spec.get("title", spec["file"]), "file": spec["file"], "missing": True})
                continue
            node = data
            for part in (spec.get("path") or "").split("/"):
                if part:
                    node = node[part]
            rows = _rows_from(node, spec.get("columns"))
            tables.append(
                {
                    "title": spec.get("title", spec["file"]),
                    "file": spec["file"],
                    "sha256": _sha256(fpath),
                    "columns": ["key"] + list(spec.get("columns") or _infer_columns(rows)),
                    "rows": rows,
                }
            )
    facts["tables"] = tables
    return facts


def _rows_from(node, columns):
    """Normalize a dict-of-dicts or list-of-dicts into [(key, {col: val})]."""
    rows = []
    if isinstance(node, dict):
        for k, v in node.items():
            if isinstance(v, dict):
                rows.append((str(k), v))
    elif isinstance(node, list):
        for i, v in enumerate(node):
            if isinstance(v, dict):
                key = str(v.get("measure") or v.get("name") or v.get("id") or i)
                rows.append((key, v))
    if columns:
        rows = [(k, {c: v.get(c) for c in columns}) for k, v in rows]
    return rows


def _infer_columns(rows):
    cols: list[str] = []
    for _, v in rows:
        for c in v:
            if c not in cols:
                cols.append(c)
    return cols


# ----------------------------------------------------------------------
# Rendering
# ----------------------------------------------------------------------


def _fmt(v) -> str:
    if v is None:
        return "—"
    if isinstance(v, float):
        return f"{v:.6g}"
    if isinstance(v, (dict, list)):
        return "`" + json.dumps(v, sort_keys=True, separators=(",", ":")) + "`"
    return str(v)


def render_facts(facts: dict) -> str:
    out: list[str] = []
    out.append(f"# {facts['study_id']} — registration facts (generated; do not edit)")
    out.append("")
    out.append(
        "Built by `studies.registration` from the study's files. Regenerate with "
        f"`python -m studies.registration studies/{facts['study_id']}`; CI checks that this "
        "file matches the repository."
    )

    out.append("")
    out.append("## Fence")
    out.append("")
    if facts["fence"]["manifests"]:
        out.append("| partition | rows | manifest hash | seed | created |")
        out.append("|---|---|---|---|---|")
        for m in facts["fence"]["manifests"]:
            out.append(
                f"| {m['partition']} | {_fmt(m['row_count'])} | `{m['manifest_hash']}` | "
                f"{_fmt(m['seed'])} | {_fmt(m['created_at'])} |"
            )
    else:
        out.append("No fence manifests.")
    st = facts["fence"]["state"]
    if st:
        out.append("")
        out.append(f"Fence created (confirmatory locked) at {_fmt(st['locked_at'])}. "
                   "Current lock state and access-log tallies are on the gate board (`yardstick status`).")

    out.append("")
    out.append("## Generation")
    out.append("")
    if facts["generation"]:
        for k, v in facts["generation"].items():
            out.append(f"- {k}: {_fmt(v)}")
    else:
        out.append("No `config/generation.json`.")

    out.append("")
    out.append("## Measures (by code)")
    out.append("")
    if facts["measures"]:
        out.append("| name | version | file | sha256 |")
        out.append("|---|---|---|---|")
        for m in facts["measures"]:
            out.append(f"| {_fmt(m['name'])} | {_fmt(m['version'])} | {m['file']} | `{m['sha256']}` |")
    else:
        out.append("No `measures/` directory.")

    out.append("")
    out.append("## Config files")
    out.append("")
    if facts["config_hashes"]:
        out.append("| file | sha256 |")
        out.append("|---|---|")
        for name, h in facts["config_hashes"].items():
            out.append(f"| {name} | `{h}` |")
    else:
        out.append("No `config/` directory.")

    rs = facts["results_schema"]
    out.append("")
    out.append("## Results schema")
    out.append("")
    if rs:
        out.append(f"- title: {_fmt(rs['title'])}")
        out.append(f"- required: {_fmt(rs['required'])}")
        out.append(f"- sha256: `{rs['sha256']}`")
    else:
        out.append("No `config/results-schema.json`.")

    for t in facts["tables"]:
        out.append("")
        out.append(f"## {t['title']}")
        out.append("")
        if t.get("missing"):
            out.append(f"Source `{t['file']}` is missing.")
            continue
        out.append(f"Source `{t['file']}` (sha256 `{t['sha256']}`).")
        out.append("")
        cols = t["columns"]
        out.append("| " + " | ".join(cols) + " |")
        out.append("|" + "---|" * len(cols))
        for key, vals in t["rows"]:
            cells = [key] + [_fmt(vals.get(c)) for c in cols[1:]]
            out.append("| " + " | ".join(cells) + " |")

    out.append("")
    return "\n".join(out)


# ----------------------------------------------------------------------
# Build / check
# ----------------------------------------------------------------------


def build(study_root: str | Path, write: bool = True) -> str:
    text = render_facts(collect_facts(study_root))
    if write:
        path = Path(study_root) / FACTS_FILE
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return text


def check(study_root: str | Path) -> tuple[bool, str]:
    """(up_to_date, message). Regenerates and compares with the committed file."""
    path = Path(study_root) / FACTS_FILE
    fresh = build(study_root, write=False)
    if not path.exists():
        return False, f"{path} does not exist; run the builder"
    committed = path.read_text(encoding="utf-8")
    if committed == fresh:
        return True, "facts.md is current"
    return False, f"{path} is stale; regenerate and commit"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Build or check a study's registration facts.")
    ap.add_argument("study_root")
    ap.add_argument("--check", action="store_true", help="verify the committed facts.md is current")
    args = ap.parse_args(argv)
    if args.check:
        ok, msg = check(args.study_root)
        print(msg)
        return 0 if ok else 1
    build(args.study_root)
    print(f"wrote {Path(args.study_root) / FACTS_FILE}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
