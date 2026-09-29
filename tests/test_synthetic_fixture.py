"""Synthetic fixture tests (MVP v0.2 Batch 1, PART 5 and PART 7B).

Builds the fabricated ChatGPT-shaped export from
tests/fixtures/generate_fixture.py, verifies it against the committed golden
manifest, exercises the structural/parser expectations it was designed for,
and runs the wired Phase 1 intake (inspect_archive) end to end inside a
throwaway workspace to prove configuration-only initialization.

No real archive data is read. No project-tree output is touched.
"""
from __future__ import annotations

import contextlib
import io
import json
import shutil
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
PROJECT = HERE.parent
for addition in (PROJECT / "src", HERE / "fixtures"):
    if str(addition) not in sys.path:
        sys.path.insert(0, str(addition))

import archive_intelligence.engine.engine_config as ec  # noqa: E402
import generate_fixture as gf  # noqa: E402
from archive_intelligence.engine import inspect_archive  # noqa: E402
from archive_intelligence.engine.phase2_parser import graph  # noqa: E402

GOLDEN = json.loads(gf.GOLDEN_MANIFEST.read_text(encoding="utf-8"))


class FixtureCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.manifest, cls.zip_bytes = gf.build()

    def make_root(self, prefix="fixture-"):
        name = tempfile.mkdtemp(prefix=prefix)
        self.addCleanup(shutil.rmtree, name, ignore_errors=True)
        return Path(name).resolve()


class TestGoldenManifest(FixtureCase):
    def test_rebuild_matches_golden_manifest_exactly(self):
        self.assertEqual(self.manifest, GOLDEN)

    def test_rebuild_is_byte_deterministic(self):
        manifest_again, zip_again = gf.build()
        self.assertEqual(manifest_again, self.manifest)
        self.assertEqual(zip_again, self.zip_bytes)

    def test_sentinels_are_declared_fixtures(self):
        declared = GOLDEN["sentinels"]
        self.assertEqual(set(declared), {f"S{i}" for i in range(1, 8)})
        for entry in declared.values():
            self.assertTrue(entry["fixture"])


class TestZipLayout(FixtureCase):
    def open_archive(self):
        archive = zipfile.ZipFile(io.BytesIO(self.zip_bytes))
        self.addCleanup(archive.close)
        return archive

    def test_member_ordinals_match_manifest(self):
        with self.open_archive() as archive:
            names = archive.namelist()
        self.assertEqual(len(names), GOLDEN["zip"]["member_count"])
        self.assertEqual(len(names), 21)
        for ordinal, member in enumerate(GOLDEN["members"]):
            self.assertEqual(names[ordinal], member["name"])
        # conversation shards at ordinals 2-4, metadata at 1/17/18
        self.assertEqual(names[1], "user.json")
        self.assertEqual(names[2:5],
                         ["conversations-000.json", "conversations-001.json",
                          "conversations-002.json"])
        self.assertEqual(names[17], "aux_names.json")
        self.assertEqual(names[18], "file_metadata.json")

    def test_member_hashing_matches_manifest(self):
        import hashlib
        with self.open_archive() as archive:
            for member in GOLDEN["members"]:
                data = archive.read(member["name"])
                self.assertEqual(len(data), member["bytes"])
                self.assertEqual(hashlib.sha256(data).hexdigest(), member["sha256"])

    def test_archive_is_stored_and_timestamp_frozen(self):
        with self.open_archive() as archive:
            for info in archive.infolist():
                self.assertEqual(info.compress_type, zipfile.ZIP_STORED)
                self.assertEqual(info.date_time, (2026, 1, 1, 0, 0, 0))


class TestConversationShards(FixtureCase):
    def load_shards(self):
        shards = []
        with zipfile.ZipFile(io.BytesIO(self.zip_bytes)) as archive:
            for name in ("conversations-000.json", "conversations-001.json",
                         "conversations-002.json"):
                shards.append(json.loads(archive.read(name).decode("utf-8")))
        return shards

    def test_shard_counts_and_anomaly_free_graphs(self):
        shards = self.load_shards()
        self.assertEqual([len(shard) for shard in shards], [6, 4, 2])
        conversations = [conversation for shard in shards for conversation in shard]
        self.assertEqual(len(conversations), 12)
        for conversation in conversations:
            check = graph(conversation)
            with self.subTest(title=conversation.get("title")):
                self.assertEqual(check["anomalies"], {})
                self.assertTrue(check["active_valid"])

    def test_expected_totals_and_shape_markers(self):
        shards = self.load_shards()
        conversations = [conversation for shard in shards for conversation in shard]
        titles = [conversation.get("title") for conversation in conversations]
        self.assertIn(GOLDEN["sentinels"]["S1"]["value"], titles)  # title canary
        self.assertIn("", titles)                                  # empty title edge
        roles = set()
        content_types = set()
        has_multimodal_part = has_null_time = False
        for conversation in conversations:
            for node in conversation["mapping"].values():
                if node.get("create_time") is None:
                    has_null_time = True  # structural nodes keep null timestamps
                message = node.get("message")
                if message is None:
                    continue
                roles.add(message["author"]["role"])
                content_types.add(message["content"]["content_type"])
                parts = message["content"].get("parts", [])
                if any(isinstance(part, dict) for part in parts):
                    has_multimodal_part = True
        self.assertIn("system", roles)
        self.assertIn("tool", roles)
        self.assertIn("synthetic_moderator", roles)   # unknown role value
        self.assertIn("multimodal_text", content_types)
        self.assertIn("thoughts", content_types)
        self.assertIn("reasoning_recap", content_types)
        self.assertIn("code", content_types)
        self.assertIn("synthetic_widget", content_types)  # unknown content type
        self.assertTrue(has_multimodal_part)
        self.assertTrue(has_null_time)

    def test_encoding_variants(self):
        with zipfile.ZipFile(io.BytesIO(self.zip_bytes)) as archive:
            shard0 = archive.read("conversations-000.json")
            shard2 = archive.read("conversations-002.json")
        self.assertIn("café".encode("utf-8"), shard0)   # raw UTF-8 in shard 0
        self.assertTrue(all(byte < 128 for byte in shard2))  # ascii-escaped shard 2
        self.assertIn(b"\\u", shard2)

    def test_sentinel_values_exist_in_fixture_only(self):
        with zipfile.ZipFile(io.BytesIO(self.zip_bytes)) as archive:
            member_names = "\n".join(archive.namelist())
            blob = b"".join(archive.read(name) for name in archive.namelist())
        text = blob.decode("utf-8", errors="replace")
        for key, entry in GOLDEN["sentinels"].items():
            with self.subTest(sentinel=key):
                value = entry["value"]
                self.assertTrue(value in text or value in member_names,
                                f"sentinel {key} missing from fixture")


class TestInspectArchiveOnFixture(FixtureCase):
    def prepare_workspace(self, export_members=None):
        root = self.make_root()
        export_dir = root / "export"
        export_dir.mkdir()
        if export_members is None:
            (export_dir / gf.ZIP_FILENAME).write_bytes(self.zip_bytes)
        else:
            for name, data in export_members:
                (export_dir / name).write_bytes(data)
        (root / ec.LOCAL_CONFIG_NAME).write_text(
            json.dumps({"source": {"export_dir": str(export_dir)}}),
            encoding="utf-8")
        return ec.load_workspace(root=root, env={}), root

    def test_full_intake_run_in_config_only_workspace(self):
        ws, root = self.prepare_workspace()
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            inspect_archive.inventory(workspace=ws)
        inventory_path = ws.outputs_dir / "ARCHIVE_INVENTORY.json"
        self.assertTrue(inventory_path.is_file())
        rendered = inventory_path.read_text(encoding="utf-8")
        inventory = json.loads(rendered)

        self.assertEqual(inventory["member_count"], 21)
        self.assertEqual(inventory["source_container_count"], 1)
        self.assertEqual(len(inventory["conversation_files"]), 3)
        self.assertEqual(len(inventory["schema_samples"]), 3)
        # unsafe member name must appear only as its alias
        self.assertNotIn("private-notes-draft", rendered)
        self.assertIn(GOLDEN["expected_alias"]["private-notes-draft.png"], rendered)
        # no conversation content (sentinels) may appear in the report
        for key, entry in GOLDEN["sentinels"].items():
            with self.subTest(sentinel=key):
                self.assertNotIn(entry["value"], rendered)
        # all writes stayed inside the configured workspace
        for produced in ws.outputs_dir.rglob("*"):
            self.assertTrue(produced.is_relative_to(root))

    def test_ambiguous_source_directory_fails_closed(self):
        ws, _ = self.prepare_workspace(export_members=[
            ("a.zip", self.zip_bytes), ("b.zip", self.zip_bytes)])
        with self.assertRaises(RuntimeError) as caught:
            inspect_archive.inventory(workspace=ws)
        self.assertEqual(str(caught.exception),
                         "Source ambiguity or type changed; stop and ask user.")

    def test_wrong_file_type_in_source_directory_fails_closed(self):
        ws, _ = self.prepare_workspace(export_members=[
            ("notes.txt", b"plain text, not a zip")])
        with self.assertRaises(RuntimeError) as caught:
            inspect_archive.inventory(workspace=ws)
        self.assertEqual(str(caught.exception),
                         "Source ambiguity or type changed; stop and ask user.")


if __name__ == "__main__":
    unittest.main()
