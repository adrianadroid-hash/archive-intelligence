"""Phase 1 report generation tests (path redaction, neutral sync, LF output).

Runs a direct synthetic intake → Phase 1 report-generation flow inside a
throwaway temp workspace: the public package tree is copied to
``<workspace>/src``, a fabricated fixture export plus config-only input is
placed in the workspace, then ``python -m archive_intelligence.engine.
inspect_archive`` and ``python -m archive_intelligence.engine.
build_phase1_reports`` run as subprocesses from that tree (the module's ROOT
resolves to the workspace root, mirroring the source-layout script posture).

Proves the migrated P2-6/P1-5 generalization:

* reports never render the workspace root or the configured source path
  (M8: ``<source-archive>`` alias plus owner-verifiable location fingerprint);
* no provider/environment-specific sync brand wording (M10: one neutral
  sync/backup caveat);
* all four generated reports are LF-terminated (M23);
* the public status title is ``Archive Intelligence``.

No real archive data is read; every write lands in the temp workspace.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
PROJECT = HERE.parent
for addition in (PROJECT / "src", HERE / "fixtures"):
    if str(addition) not in sys.path:
        sys.path.insert(0, str(addition))

import generate_fixture as gf  # noqa: E402

REPORTS = (
    "outputs/ARCHIVE_MANIFEST.md",
    "outputs/PRIVACY_RISK_REPORT.md",
    "outputs/PROCESSING_PLAN.md",
    "STATUS.md",
)


class TestPhase1ReportGeneration(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.manifest, cls.zip_bytes = gf.build()
        cls.base = Path(tempfile.mkdtemp(prefix="phase1-reports-"))
        cls.workspace = cls.base.resolve()
        source = cls.workspace / "source"
        source.mkdir()
        (source / gf.ZIP_FILENAME).write_bytes(cls.zip_bytes)
        (cls.workspace / "engine_config.json").write_text(
            json.dumps({
                "profile": "report-tests",
                "source": {"export_dir": "source",
                           "export_zip": "source/" + gf.ZIP_FILENAME},
                "expectations": {"expected_root": str(cls.workspace),
                                 "min_free_bytes": 0},
                "runtime": {"python_requires": ">=3.12,<3.15"},
            }, indent=2) + "\n",
            encoding="utf-8")
        (cls.workspace / "src").mkdir()
        shutil.copytree(
            PROJECT / "src" / "archive_intelligence",
            cls.workspace / "src" / "archive_intelligence",
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        cls.run_module("inspect_archive")
        cls.reports_output = cls.run_module(
            "build_phase1_reports", marker="Phase 1 reports generated")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.base, ignore_errors=True)

    @classmethod
    def run_module(cls, module, marker=None):
        """Run one engine module as ``python -m`` inside the copied tree."""
        env = dict(os.environ)
        env.pop("ENGINE_CONFIG", None)
        env.pop("PYTHONPATH", None)
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        procedure = subprocess.run(
            [sys.executable, "-B", "-m",
             "archive_intelligence.engine." + module],
            cwd=str(cls.workspace / "src"),
            env=env, capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=600)
        output = (procedure.stdout or "") + (procedure.stderr or "")
        if procedure.returncode != 0:
            raise AssertionError(
                "%s exited %d\n%s"
                % (module, procedure.returncode, output[-4000:]))
        if marker is not None and marker not in output:
            raise AssertionError(
                "%s produced no marker %r\n%s"
                % (module, marker, output[-4000:]))
        return output

    @classmethod
    def read(cls, relative):
        return (cls.workspace / relative).read_text(encoding="utf-8")

    def test_intake_then_reports_redact_paths(self):
        inventory = json.loads(
            (self.workspace / "outputs" / "ARCHIVE_INVENTORY.json")
            .read_text(encoding="utf-8"))
        recorded_source = inventory["source_path"]
        root_abs = str(self.workspace)
        for relative in REPORTS:
            text = self.read(relative)
            with self.subTest(report=relative):
                self.assertNotIn(root_abs, text)
                self.assertNotIn(recorded_source, text)

        manifest = self.read("outputs/ARCHIVE_MANIFEST.md")
        self.assertIn("Exact source: `<source-archive>`", manifest)
        self.assertIn("Location SHA-256 `", manifest)
        status = self.read("STATUS.md")
        self.assertIn("the project root containing this file", status)
        self.assertIn("`<source-archive>`", status)

    def test_reports_use_provider_neutral_sync_caveat(self):
        # needle assembled from parts so this test file itself stays scan-clean
        brand = "one" + "drive"
        for relative in REPORTS:
            text = self.read(relative).lower()
            with self.subTest(report=relative):
                self.assertNotIn(brand, text)

        privacy = self.read("outputs/PRIVACY_RISK_REPORT.md")
        self.assertIn("Cloud-sync or backup agents may apply", privacy)
        plan = self.read("outputs/PROCESSING_PLAN.md")
        self.assertIn(
            "Cloud-sync/backup status of the source and project paths "
            "was not verified",
            plan)

    def test_reports_lf_only_and_public_title(self):
        for relative in REPORTS:
            data = (self.workspace / relative).read_bytes()
            with self.subTest(report=relative):
                self.assertNotIn(b"\r\n", data)
                self.assertTrue(data.endswith(b"\n"))
        first_line = self.read("STATUS.md").splitlines()[0]
        self.assertEqual(first_line, "# Status — Archive Intelligence")


if __name__ == "__main__":
    unittest.main()
