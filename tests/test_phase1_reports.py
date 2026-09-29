"""Phase 1 report generation and verification tests.

Runs the full Phase 1 chain — inventory, report generation, Phase 1
verification — as subprocesses inside a throwaway temp workspace (public
package tree copied to ``<workspace>/src``, fabricated fixture export,
config-only input) and proves:

* reports never render the workspace root or the configured source path
  (M8: ``<source-archive>`` alias plus owner-verifiable location fingerprint);
* no provider/environment-specific sync brand wording (M10: one neutral
  sync/backup caveat);
* all four generated reports are LF-terminated (M23);
* the public status title is ``Archive Intelligence``;
* ``verify_phase1`` passes on correct outputs (PASS marker +
  ``PHASE1_VALIDATION.json``);
* ``verify_phase1`` fails closed on a missing or corrupted required
  Phase 1 report (non-zero exit, no PASS).

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
        cls.verify_output = cls.run_module("verify_phase1", marker="PASS:")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.base, ignore_errors=True)

    @classmethod
    def run_module(cls, module, marker=None, expect_failure=False):
        """Run one engine module as ``python -m`` inside the copied tree.

        With ``expect_failure`` the run must exit non-zero and must not
        emit ``marker`` (fail-closed proof); output is returned either way.
        """
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
        if expect_failure:
            if procedure.returncode == 0:
                raise AssertionError(
                    "%s unexpectedly succeeded\n%s" % (module, output[-4000:]))
            if marker is not None and marker in output:
                raise AssertionError(
                    "%s failed but emitted marker %r\n%s"
                    % (module, marker, output[-4000:]))
            return output
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

    def test_verify_phase1_passes_on_correct_outputs(self):
        validation = json.loads(
            (self.workspace / "outputs" / "PHASE1_VALIDATION.json")
            .read_text(encoding="utf-8"))
        self.assertEqual(validation["status"], "PASS")
        self.assertTrue(validation["checks"])
        self.assertTrue(all(c["passed"] for c in validation["checks"]))
        self.assertIn("PASS:", self.verify_output)

    def test_verify_phase1_fails_closed_on_missing_report(self):
        target = self.workspace / "outputs" / "PROCESSING_PLAN.md"
        original = target.read_bytes()
        target.unlink()
        try:
            output = self.run_module("verify_phase1", marker="PASS:",
                                     expect_failure=True)
        finally:
            target.write_bytes(original)
        self.assertIn("PROCESSING_PLAN.md", output)

    def test_verify_phase1_fails_closed_on_corrupted_report(self):
        manifest = self.workspace / "outputs" / "ARCHIVE_MANIFEST.md"
        inventory_path = self.workspace / "outputs" / "ARCHIVE_INVENTORY.json"
        original_manifest = manifest.read_bytes()
        original_inventory = inventory_path.read_bytes()
        # corrupted (emptied) required report -> size check fails closed
        manifest.write_bytes(b"")
        try:
            output = self.run_module("verify_phase1", marker="PASS:",
                                     expect_failure=True)
            self.assertIn("required output: ARCHIVE_MANIFEST.md", output)
        finally:
            manifest.write_bytes(original_manifest)
        # corrupted inventory (broken reconciliation) -> assertion fails closed
        tampered = json.loads(original_inventory.decode("utf-8"))
        tampered["member_count"] += 1
        inventory_path.write_text(
            json.dumps(tampered, ensure_ascii=False), encoding="utf-8")
        try:
            output = self.run_module("verify_phase1", marker="PASS:",
                                     expect_failure=True)
            self.assertIn("member count reconciles", output)
        finally:
            inventory_path.write_bytes(original_inventory)


if __name__ == "__main__":
    unittest.main()
