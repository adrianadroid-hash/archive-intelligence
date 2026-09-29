"""Direct synthetic test for the Phase 2 selective staging component.

Runs the Phase 1 intake (inspect_archive) followed by the Phase 2 staging
entry point (phase2_stage.main) inside a throwaway temp workspace against
the fabricated fixture export produced by tests/fixtures/generate_fixture.py,
proving the staging pipeline works from the public package layout with
configuration as the only input: preflight, directory-identity reconciliation,
byte/CRC/SHA-256 member verification, the resumable ledger and the staging
pointer are all checked against the independently computed staging plan.

A red-path case proves fail-closed behavior when the source archive changes
after intake (SOURCE_STAT).

No real archive data is read. No repository-tree output is produced: every
write lands in the temp workspace.
"""
from __future__ import annotations

import contextlib
import hashlib
import io
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
PROJECT = HERE.parent
for addition in (PROJECT / "src", HERE / "fixtures"):
    if str(addition) not in sys.path:
        sys.path.insert(0, str(addition))

import archive_intelligence.engine.engine_config as ec  # noqa: E402
import generate_fixture as gf  # noqa: E402
from archive_intelligence.engine import (  # noqa: E402
    engine_discovery,
    inspect_archive,
    phase2_common,
    phase2_stage,
)

GOLDEN = json.loads(gf.GOLDEN_MANIFEST.read_text(encoding="utf-8"))


class StagingCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.manifest, cls.zip_bytes = gf.build()

    def prepare_workspace(self):
        """Temp workspace: fixture source ZIP + config-only input."""
        name = tempfile.mkdtemp(prefix="stage-")
        self.addCleanup(shutil.rmtree, name, ignore_errors=True)
        root = Path(name).resolve()
        export_dir = root / "export"
        export_dir.mkdir()
        zip_path = export_dir / gf.ZIP_FILENAME
        zip_path.write_bytes(self.zip_bytes)
        (root / ec.LOCAL_CONFIG_NAME).write_text(
            json.dumps({
                "source": {
                    "export_dir": str(export_dir),
                    "export_zip": str(zip_path),
                }
            }),
            encoding="utf-8")
        return ec.load_workspace(root=root, env={}), root, zip_path

    def run_intake(self, ws):
        with contextlib.redirect_stdout(io.StringIO()):
            inspect_archive.inventory(workspace=ws)
        inventory_path = ws.outputs_dir / "ARCHIVE_INVENTORY.json"
        return json.loads(inventory_path.read_text(encoding="utf-8"))

    def start_patches(self, ws, root, zip_path):
        """Retarget the staging module globals to the temp workspace."""
        phase2_out = root / "outputs" / "phase2"
        for target, attribute, value in (
            (phase2_common, "ROOT", root),
            (phase2_common, "OUT", phase2_out),
            (phase2_common, "EXPECTED", root),
            (phase2_common, "SOURCE", zip_path),
            (phase2_common, "_WORKSPACE", ws),
            (phase2_stage, "ROOT", root),
            (phase2_stage, "OUT", phase2_out),
        ):
            patcher = mock.patch.object(target, attribute, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def run_stage(self, ws, root, zip_path):
        self.start_patches(ws, root, zip_path)
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            phase2_stage.main()
        return buffer.getvalue()


class TestStagingOnFixture(StagingCase):
    def test_full_staging_run_in_config_only_workspace(self):
        ws, root, zip_path = self.prepare_workspace()
        inventory = self.run_intake(ws)

        plan = engine_discovery.staging_plan(inventory)
        expected_total = sum(entry["uncompressed_bytes"] for entry, _ in plan)
        conversation_count = sum(
            1 for _, rel in plan if rel.startswith("conversations/"))
        self.assertGreater(len(plan), 0)
        self.assertEqual(conversation_count,
                         len(inventory["conversation_files"]))

        output = self.run_stage(ws, root, zip_path)
        self.assertIn(
            f"PREFLIGHT_AND_STAGING_PASS files={len(plan)} "
            f"bytes={expected_total}",
            output)

        preflight = json.loads(
            (root / "outputs" / "phase2" / "preflight.json")
            .read_text(encoding="utf-8"))
        self.assertTrue(preflight["source_path_verified"])
        self.assertTrue(preflight["local_root_verified"])
        self.assertEqual(preflight["inventory_entries_verified"],
                         inventory["member_count"])
        self.assertGreaterEqual(preflight["free_bytes"], 2 * 1024 ** 3)

        source_sha = hashlib.sha256(self.zip_bytes).hexdigest()
        pointer = json.loads(
            (root / "outputs" / "phase2" / "staging_pointer.json")
            .read_text(encoding="utf-8"))
        self.assertEqual(pointer["relative_root"],
                         os.path.join("staging", source_sha))

        stage_dir = root / "staging" / source_sha
        ledger = json.loads(
            (stage_dir / "extraction_manifest.json").read_text(
                encoding="utf-8"))
        self.assertEqual(ledger["version"], phase2_common.VERSION)
        self.assertEqual(ledger["source"]["sha256"], source_sha)
        records = ledger["files"]
        self.assertEqual(set(records),
                         {entry["entry_id"] for entry, _ in plan})
        self.assertTrue(all(r["status"] == "verified" for r in records.values()))
        self.assertEqual(sum(r["bytes"] for r in records.values()),
                         expected_total)
        for entry, rel in plan:
            staged = stage_dir / rel
            self.assertTrue(staged.is_file(), rel)
            self.assertEqual(staged.stat().st_size,
                             entry["uncompressed_bytes"], rel)

        # safe outputs must not carry any declared fixture canary content
        scan_files = [root / "STATUS.md"]
        scan_files += [p for p in sorted((root / "outputs").rglob("*"))
                       if p.is_file() and p.suffix in (".json", ".md")]
        for path in scan_files:
            self.assertTrue(path.is_file())
            text = path.read_text(encoding="utf-8", errors="replace")
            for key, entry in GOLDEN["sentinels"].items():
                with self.subTest(sentinel=key, file=path.name):
                    self.assertNotIn(entry["value"], text)

        # no staging step may write anything into the repository tree
        for leaked in ("STATUS.md", "outputs", "staging", "corpus"):
            self.assertFalse((PROJECT / leaked).exists(), leaked)

    def test_source_changed_after_intake_fails_closed(self):
        ws, root, zip_path = self.prepare_workspace()
        self.run_intake(ws)
        with open(zip_path, "ab") as handle:  # source altered post-intake
            handle.write(b"X")

        self.start_patches(ws, root, zip_path)
        with contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaises(RuntimeError) as caught:
                phase2_stage.main()
        self.assertEqual(str(caught.exception), "SOURCE_STAT")


if __name__ == "__main__":
    unittest.main()
