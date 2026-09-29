"""Direct synthetic tests for the Phase 2 structural indexing component.

Runs the full chain — Phase 1 intake, Phase 2 selective staging and the
Phase 2 indexing entry point (phase2_index.main) — inside a throwaway temp
workspace against the fabricated fixture export produced by
tests/fixtures/generate_fixture.py, proving indexing works from the public
package layout with configuration as the only input: the provenance gate,
staging-ledger re-verification, per-conversation checkpoints, structural
reconciliation, SQLite integrity, the privacy audit and the generated
reports are all checked.

P1-1 provenance enforcement (A62) is proved both ways: a tampered recorded
parser/index hash fails closed with the fixed codes PARSER_TESTS_PARSER_HASH
and PARSER_TESTS_INDEX_HASH before any indexing work, and a correct manifest
indexes to VERIFIED_COMPLETE.

Transactional coverage (checkpoint, rollback, resume without double count,
private-text exclusion, attachment linkage) is adapted from the private
component test; parser-only cases are out of scope for this component.

No real archive data is read. No repository-tree output is produced: every
write lands in the temp workspace.
"""
from __future__ import annotations

import contextlib
import io
import json
import shutil
import sqlite3
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
    phase2_index,
    phase2_parser,
    phase2_stage,
)

GOLDEN = json.loads(gf.GOLDEN_MANIFEST.read_text(encoding="utf-8"))
PUBLIC_PHASE2_SCRIPTS = {
    "phase2_common.py",
    "phase2_index.py",
    "phase2_parser.py",
    "phase2_stage.py",
}


class IndexingCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.manifest, cls.zip_bytes = gf.build()

    def prepare_workspace(self):
        """Temp workspace: fixture source ZIP + config-only input."""
        name = tempfile.mkdtemp(prefix="index-")
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
        """Retarget the engine module globals to the temp workspace."""
        phase2_out = root / "outputs" / "phase2"
        for target, attribute, value in (
            (phase2_common, "ROOT", root),
            (phase2_common, "OUT", phase2_out),
            (phase2_common, "EXPECTED", root),
            (phase2_common, "SOURCE", zip_path),
            (phase2_common, "_WORKSPACE", ws),
            (phase2_stage, "ROOT", root),
            (phase2_stage, "OUT", phase2_out),
            (phase2_index, "ROOT", root),
            (phase2_index, "OUT", phase2_out),
        ):
            patcher = mock.patch.object(target, attribute, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def run_stage(self, ws, root, zip_path):
        self.start_patches(ws, root, zip_path)
        with contextlib.redirect_stdout(io.StringIO()):
            phase2_stage.main()

    def write_provenance(self, root, tests):
        """Record the public-package hashes the indexer must re-verify.

        Mirrors the private component test's PARSER_TESTS.json shape; the
        recorded hashes name the real package files beside phase2_index.
        """
        manifest = {
            "pass": True,
            "tests": tests,
            "test_count": len(tests),
            "parser_sha256": phase2_common.sha(
                phase2_index.ENGINE / "phase2_parser.py"),
            "index_sha256": phase2_common.sha(
                phase2_index.ENGINE / "phase2_index.py"),
            "dependency": "Python standard library",
            "element_byte_cap": phase2_parser.MAX_ELEMENT,
            "depth_cap": phase2_parser.MAX_DEPTH,
        }
        phase2_common.write(phase2_common.OUT / "PARSER_TESTS.json", manifest)
        return manifest

    def run_index(self, root):
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            phase2_index.main()
        return buffer.getvalue()

    def assert_no_repository_output(self):
        for leaked in ("STATUS.md", "outputs", "staging", "corpus"):
            self.assertFalse((PROJECT / leaked).exists(), leaked)


class TestIndexingOnFixture(IndexingCase):
    def test_intake_staging_indexing_end_to_end(self):
        ws, root, zip_path = self.prepare_workspace()
        inventory = self.run_intake(ws)
        self.run_stage(ws, root, zip_path)
        self.write_provenance(root, ["intake_staging_indexing"])

        output = self.run_index(root)
        self.assertIn("VERIFIED_COMPLETE", output)

        out_dir = root / "outputs" / "phase2"
        validation = json.loads(
            (out_dir / "VALIDATION.json").read_text(encoding="utf-8"))
        self.assertTrue(validation["pass"])
        self.assertTrue(validation["privacy"]["pass"])
        self.assertTrue(validation["no_media_extracted"])
        self.assertTrue(validation["all_generated_paths_local"])
        self.assertEqual(validation["sqlite_integrity"], "ok")
        self.assertEqual(validation["foreign_keys"], "ok")
        expected_conversations = self.manifest["totals"]["conversations"]
        # inventory counts conversation shard files; totals count conversations
        self.assertEqual(len(inventory["conversation_files"]),
                         len(self.manifest["shards"]))
        self.assertEqual(validation["counts"]["conversations"],
                         expected_conversations)
        self.assertGreater(validation["staged_hashes_revalidated"], 0)

        # success path: the recorded provenance hashes match the package files
        recorded = validation["parser_tests"]
        self.assertTrue(recorded["pass"])
        self.assertEqual(
            recorded["parser_sha256"],
            phase2_common.sha(phase2_index.ENGINE / "phase2_parser.py"))
        self.assertEqual(
            recorded["index_sha256"],
            phase2_common.sha(phase2_index.ENGINE / "phase2_index.py"))

        run_manifest = json.loads(
            (out_dir / "run_manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(run_manifest["status"], "verified_complete")
        self.assertEqual(set(run_manifest["script_sha256"]),
                         PUBLIC_PHASE2_SCRIPTS)

        summary_file = out_dir / "STRUCTURAL_SUMMARY.md"
        self.assertTrue(summary_file.is_file())
        self.assertIn("Phase 2 structural summary",
                      summary_file.read_text(encoding="utf-8"))

        db = sqlite3.connect(out_dir / "structural_index.sqlite")
        try:
            self.assertEqual(
                db.execute("PRAGMA integrity_check").fetchall(), [("ok",)])
            self.assertEqual(
                db.execute("SELECT count(*) FROM conversations").fetchone()[0],
                expected_conversations)
            self.assertEqual(
                db.execute(
                    "SELECT count(*) FROM nodes").fetchone()[0],
                validation["counts"]["nodes"])
        finally:
            db.close()

        # safe outputs must not carry any declared fixture canary content
        scan_files = [root / "STATUS.md"]
        scan_files += [p for p in sorted(out_dir.rglob("*"))
                       if p.is_file() and p.suffix in (".json", ".md")]
        for path in scan_files:
            self.assertTrue(path.is_file())
            text = path.read_text(encoding="utf-8", errors="replace")
            for key, entry in GOLDEN["sentinels"].items():
                with self.subTest(sentinel=key, file=path.name):
                    self.assertNotIn(entry["value"], text)

        self.assert_no_repository_output()

    def test_provenance_hash_tamper_fails_closed(self):
        ws, root, zip_path = self.prepare_workspace()
        self.run_intake(ws)
        self.run_stage(ws, root, zip_path)
        manifest = self.write_provenance(root, ["provenance_hash_enforcement"])
        record = phase2_common.OUT / "PARSER_TESTS.json"

        for key, code in (("parser_sha256", "PARSER_TESTS_PARSER_HASH"),
                          ("index_sha256", "PARSER_TESTS_INDEX_HASH")):
            broken = dict(manifest)
            broken[key] = "0" * 64
            phase2_common.write(record, broken)
            with contextlib.redirect_stdout(io.StringIO()):
                with self.assertRaises(RuntimeError) as caught:
                    phase2_index.main()
            self.assertEqual(str(caught.exception), code)
            # fail-closed: the gate fires before any index database exists
            self.assertFalse(
                (phase2_common.OUT / "structural_index.sqlite").exists())

        # restore the untampered record: indexing succeeds end to end
        phase2_common.write(record, manifest)
        output = self.run_index(root)
        self.assertIn("VERIFIED_COMPLETE", output)
        validation = json.loads(
            (phase2_common.OUT / "VALIDATION.json").read_text(
                encoding="utf-8"))
        self.assertTrue(validation["pass"])
        self.assert_no_repository_output()


class TestIndexingTransactions(IndexingCase):
    """In-memory checkpoint/resume, privacy and linkage units (no workspace)."""

    def test_transactional_checkpoint_rollback_resume_and_privacy(self):
        class EmptyResolver:
            def resolve(self, value):
                return "unresolved", set(), "none"

        def node(k, p, msg=None):
            return {"id": k, "parent": p, "message": msg}

        msg = {
            "id": "synthetic-message",
            "author": {"role": "user"},
            "content": {"content_type": "text",
                        "parts": ["SYNTHETIC_PRIVATE_BODY_CANARY"]},
            "create_time": 1,
        }
        c = {
            "id": "synthetic-conversation",
            "title": "SYNTHETIC_PRIVATE_TITLE_CANARY",
            "create_time": 1,
            "update_time": 2,
            "current_node": "b",
            "mapping": {"root": node("root", None),
                        "a": node("a", "root", msg),
                        "b": node("b", "root", msg)},
        }

        db = phase2_index.database(":memory:")
        objects = [c, {**c, "id": "second"}, {**c, "id": "third"}]
        raw = json.dumps(objects).encode()
        for ordinal, obj in enumerate(phase2_parser.array_items(
                io.BytesIO(raw), 2)):
            phase2_index.insert_conversation(db, 0, ordinal, obj,
                                             EmptyResolver())
            if ordinal == 0:
                break
        checkpoint = db.execute("SELECT committed FROM shards").fetchone()[0]
        self.assertEqual(checkpoint, 1)

        # an uncommitted transaction must disappear without moving the checkpoint
        db.execute("UPDATE shards SET committed=999")
        db.rollback()
        self.assertEqual(
            db.execute("SELECT committed FROM shards").fetchone()[0], 1)

        for ordinal, obj in enumerate(phase2_parser.array_items(
                io.BytesIO(raw), 3)):
            if ordinal >= checkpoint:
                phase2_index.insert_conversation(db, 0, ordinal, obj,
                                                 EmptyResolver())
        self.assertEqual(
            db.execute("SELECT count(*) FROM conversations").fetchone()[0], 3)
        self.assertEqual(
            db.execute("SELECT count(*) FROM nodes").fetchone()[0], 9)

        summary = phase2_index.summary(db)
        self.assertEqual(summary["counts"]["conversations"], 3)
        self.assertTrue(phase2_index.privacy_audit(db)["pass"])

        serialized = "\n".join(db.iterdump())
        for canary in ("SYNTHETIC_PRIVATE_BODY_CANARY",
                       "SYNTHETIC_PRIVATE_TITLE_CANARY",
                       "synthetic-conversation"):
            with self.subTest(canary=canary):
                self.assertNotIn(canary, serialized)
        db.close()

    def test_attachment_linkage_resolution_and_reference_extraction(self):
        r = phase2_index.Resolver.__new__(phase2_index.Resolver)
        r.alias = {"asset": {10}, "multi": {10, 11}}
        r.methods = {"asset": "explicit_manifest_mapping",
                     "multi": "explicit_manifest_mapping"}
        r.payload = {10, 11}
        r.candidates = {"file-id": {10}}
        self.assertEqual(r.resolve("sediment://asset")[0], "resolved")
        self.assertEqual(r.resolve("multi")[0], "ambiguous")
        self.assertEqual(r.resolve("file-id")[0], "candidate_filename_join")
        self.assertEqual(r.resolve("absent")[0], "unresolved")

        found = list(phase2_index.refs({
            "content": {"parts": ["ignore prose", {"asset_pointer": "asset"}]},
            "metadata": {"attachments": [{"id": "file-id"}]},
        }))
        self.assertEqual(len(found), 2)


if __name__ == "__main__":
    unittest.main()
