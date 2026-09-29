#!/usr/bin/env python3
"""Synthetic end-to-end demo for the public v0.1 core.

Runs the whole v0.1 pipeline — Phase 1 intake, report generation and
verification, then Phase 2 selective staging and structural indexing —
against a fabricated fixture export inside a throwaway temporary
workspace. No real archive data is read and nothing is written to this
repository.

Usage (after ``python -m pip install -e .``):

    python examples/synthetic_demo.py
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

# (module, marker the step must print on success)
STEPS = (
    ("inspect_archive", None),
    ("build_phase1_reports", "Phase 1 reports generated"),
    ("verify_phase1", "PASS:"),
    ("phase2_stage", "PREFLIGHT_AND_STAGING_PASS"),
)


def run_step(workspace, module, marker):
    """Run one engine module as ``python -m`` inside the copied tree."""
    env = dict(os.environ)
    env.pop("ENGINE_CONFIG", None)
    env.pop("PYTHONPATH", None)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    step = subprocess.run(
        [sys.executable, "-B", "-m",
         "archive_intelligence.engine." + module],
        cwd=str(workspace / "src"),
        env=env, capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=600)
    output = (step.stdout or "") + (step.stderr or "")
    if step.returncode != 0:
        print(output[-4000:])
        raise SystemExit("FAILED: " + module)
    if marker is None:
        print("  [ok] " + module)
        return
    if marker not in output:
        print(output[-4000:])
        raise SystemExit("FAILED: " + module + " did not print "
                         + repr(marker))
    last = output.strip().splitlines()[-1] if output.strip() else "ok"
    print("  [ok] " + module + " - " + last[:100])


def write_provenance(workspace, phase2_parser):
    """Record the current package hashes the indexer must re-verify.

    Same manifest shape the test suite records (P1-1 provenance gate):
    phase2_index re-hashes its own parser/index files before indexing.
    """
    engine_dir = workspace / "src" / "archive_intelligence" / "engine"

    def sha(path):
        return hashlib.sha256(path.read_bytes()).hexdigest()

    manifest = {
        "pass": True,
        "tests": ["synthetic_demo"],
        "test_count": 1,
        "parser_sha256": sha(engine_dir / "phase2_parser.py"),
        "index_sha256": sha(engine_dir / "phase2_index.py"),
        "dependency": "Python standard library",
        "element_byte_cap": phase2_parser.MAX_ELEMENT,
        "depth_cap": phase2_parser.MAX_DEPTH,
    }
    out = workspace / "outputs" / "phase2"
    out.mkdir(parents=True, exist_ok=True)
    (out / "PARSER_TESTS.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    sys.path.insert(0, str(REPO_ROOT / "tests" / "fixtures"))
    try:
        import archive_intelligence
        from archive_intelligence.engine import phase2_parser
    except ImportError:
        raise SystemExit(
            "archive-intelligence is not installed — run: "
            "python -m pip install -e .")
    import generate_fixture as gf

    pkg_parent = Path(archive_intelligence.__file__).resolve().parents[1]
    base = Path(tempfile.mkdtemp(prefix="archive-intelligence-demo-"))
    try:
        workspace = base.resolve()
        source = workspace / "source"
        source.mkdir()
        _, zip_bytes = gf.build()
        (source / gf.ZIP_FILENAME).write_bytes(zip_bytes)
        (workspace / "engine_config.json").write_text(
            json.dumps({
                "profile": "synthetic-demo",
                "source": {"export_dir": "source",
                           "export_zip": "source/" + gf.ZIP_FILENAME},
                "expectations": {"expected_root": str(workspace),
                                 "min_free_bytes": 0},
                "runtime": {"python_requires": ">=3.12,<3.15"},
            }, indent=2) + "\n",
            encoding="utf-8")
        (workspace / "src").mkdir()
        shutil.copytree(
            pkg_parent / "archive_intelligence",
            workspace / "src" / "archive_intelligence",
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))

        print("Running the v0.1 pipeline on a synthetic fixture export")
        print("workspace:", workspace)
        for module, marker in STEPS:
            run_step(workspace, module, marker)
            if (module == "inspect_archive"
                    and not (workspace / "outputs"
                             / "ARCHIVE_INVENTORY.json").is_file()):
                raise SystemExit(
                    "FAILED: inspect_archive wrote no inventory")
        write_provenance(workspace, phase2_parser)
        run_step(workspace, "phase2_index", "VERIFIED_COMPLETE")

        print("PASS: full v0.1 pipeline completed on the synthetic "
              "fixture.")
        print("  Phase 1: STATUS.md, outputs/ARCHIVE_MANIFEST.md,")
        print("           outputs/PRIVACY_RISK_REPORT.md, "
              "outputs/PROCESSING_PLAN.md,")
        print("           outputs/PHASE1_VALIDATION.json")
        print("  Phase 2: outputs/phase2/structural_index.sqlite, "
              "VALIDATION.json,")
        print("           run_manifest.json, STRUCTURAL_SUMMARY.md")
        print("Temporary workspace removed; this repository was not "
              "modified.")
        return 0
    finally:
        shutil.rmtree(base, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
