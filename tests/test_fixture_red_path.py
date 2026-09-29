"""Red-path tests (MVP v0.2 Batch 1, PART 5 and PART 6).

Malformed fixtures at parser level: duplicate keys, NaN, truncated and
over-deep arrays, oversized elements, invalid UTF-8, stray null bytes,
out-of-range timestamps, graph anomalies (missing parent, cycles, invalid
current node), conversation/node/message schema violations, and Phase 1 ZIP
safety flags (unsafe extraction path, sensitive name patterns).

Encrypted-ZIP detection stays covered by the untouched intake code: the
standard library cannot author encrypted members, so that specific red case
is exercised only against real archives and is documented as untestable with
synthetic generation.
"""
from __future__ import annotations

import contextlib
import io
import json
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
from archive_intelligence.engine import inspect_archive  # noqa: E402
import archive_intelligence.engine.phase2_parser as parser  # noqa: E402
from archive_intelligence.engine.phase2_parser import graph  # noqa: E402


def frame_error(data: bytes) -> str:
    try:
        list(parser.array_items(io.BytesIO(data)))
    except Exception as error:  # RuntimeError or ValueError carries the code
        return str(error)
    raise AssertionError("expected framing to fail")


def load_error(data: bytes) -> str:
    try:
        parser.strict_load(data)
    except Exception as error:
        return str(error)
    raise AssertionError("expected decoding to fail")


class TestMalformedJson(unittest.TestCase):
    def test_duplicate_key(self):
        self.assertEqual(load_error(b'[{"a": 1, "a": 2}]'), "JSON_DUPLICATE_KEY")

    def test_nan_constant(self):
        self.assertEqual(load_error(b"[NaN]"), "JSON_NONFINITE")
        self.assertEqual(load_error(b'[{"a": Infinity}]'), "JSON_NONFINITE")

    def test_truncated_array(self):
        self.assertEqual(frame_error(b"[1,"), "JSON_INCOMPLETE")
        self.assertEqual(frame_error(b"[1"), "JSON_INCOMPLETE")

    def test_invalid_utf8(self):
        self.assertEqual(load_error(b'["\xff\xfe"]'), "JSON_INVALID")

    def test_stray_null_byte(self):
        self.assertEqual(frame_error(b"[\x00]"), "JSON_INVALID")

    def test_not_an_array(self):
        self.assertEqual(frame_error(b'{"a": 1}'), "JSON_ARRAY_REQUIRED")

    def test_trailing_data(self):
        self.assertEqual(frame_error(b"[1] junk"), "JSON_TRAILING")

    def test_depth_limit(self):
        self.assertEqual(frame_error(b"[" * 300), "JSON_DEPTH_LIMIT")

    def test_element_limit(self):
        with mock.patch.object(parser, "MAX_ELEMENT", 8):
            self.assertEqual(frame_error(b'["0123456789"]'), "JSON_ELEMENT_LIMIT")

    def test_valid_document_passes(self):
        self.assertEqual(list(parser.array_items(io.BytesIO(b'["a", 2]'))), ["a", 2])


class TestTimestamps(unittest.TestCase):
    def test_out_of_range(self):
        with self.assertRaises(RuntimeError) as caught:
            parser.timestamp(1e30)
        self.assertEqual(str(caught.exception), "TIMESTAMP_FORMAT")

    def test_wrong_type(self):
        with self.assertRaises(RuntimeError) as caught:
            parser.timestamp("2023-01-01T00:00:00Z")
        self.assertEqual(str(caught.exception), "TIMESTAMP_FORMAT")

    def test_in_range_and_null_pass(self):
        self.assertIsNone(parser.timestamp(None))
        self.assertEqual(parser.timestamp(157766400.0), 157766400.0)
        self.assertEqual(parser.timestamp(2051222400.5), 2051222400.5)


class TestGraphAnomalies(unittest.TestCase):
    def anomalies(self, mapping, current_node="__set__"):
        conversation = {"mapping": mapping}
        if current_node not in ("__set__", "__omit__"):
            conversation["current_node"] = current_node
        return graph(conversation)["anomalies"]

    def node(self, node_id, parent="__set__", message=None, **extra):
        data = {"id": node_id, "message": message}
        if parent != "__set__":
            data["parent"] = parent
        data.update(extra)
        return data

    def test_missing_parent_field(self):
        anomalies = self.anomalies({"a": self.node("a")}, current_node="a")
        self.assertEqual(anomalies.get("missing_parent_field"), 1)

    def test_missing_parent(self):
        anomalies = self.anomalies(
            {"a": self.node("a", parent="ghost")}, current_node="a")
        self.assertEqual(anomalies.get("missing_parent"), 1)

    def test_invalid_parent_type(self):
        anomalies = self.anomalies(
            {"a": self.node("a", parent=42)}, current_node="a")
        self.assertEqual(anomalies.get("invalid_parent_type"), 1)

    def test_multiple_roots(self):
        anomalies = self.anomalies(
            {"a": self.node("a", parent=None),
             "b": self.node("b", parent=None)}, current_node="a")
        self.assertEqual(anomalies.get("multiple_roots"), 1)

    def test_no_root_and_cycles(self):
        anomalies = self.anomalies(
            {"a": self.node("a", parent="b"),
             "b": self.node("b", parent="a")}, current_node="a")
        self.assertEqual(anomalies.get("no_root"), 1)
        self.assertEqual(anomalies.get("cycles"), 1)

    def test_invalid_current_node(self):
        anomalies = self.anomalies(
            {"root": self.node("root", parent=None)}, current_node="ghost")
        self.assertEqual(anomalies.get("invalid_current_node"), 1)

    def test_missing_current_node(self):
        anomalies = self.anomalies(
            {"root": self.node("root", parent=None)}, current_node="__omit__")
        self.assertEqual(anomalies.get("missing_current_node"), 1)

    def test_children_mismatch(self):
        anomalies = self.anomalies(
            {"root": self.node("root", parent=None, children=["ghost"]),
             "a": self.node("a", parent="root")}, current_node="a")
        self.assertEqual(anomalies.get("children_mismatch"), 1)

    def test_invalid_children(self):
        anomalies = self.anomalies(
            {"root": self.node("root", parent=None, children="not-a-list")},
            current_node="root")
        self.assertEqual(anomalies.get("invalid_children"), 1)

    def test_node_id_mismatch(self):
        anomalies = self.anomalies(
            {"renamed": self.node("a", parent=None)}, current_node="renamed")
        self.assertEqual(anomalies.get("node_id_mismatch"), 1)


class TestSchemaErrors(unittest.TestCase):
    def test_conversation_schema(self):
        for value in ({}, [], None, {"mapping": []}):
            with self.subTest(value=value):
                with self.assertRaises(RuntimeError) as caught:
                    graph(value)
                self.assertEqual(str(caught.exception), "CONVERSATION_SCHEMA")

    def test_node_schema(self):
        with self.assertRaises(RuntimeError) as caught:
            graph({"mapping": {"a": 5}, "current_node": "a"})
        self.assertEqual(str(caught.exception), "NODE_SCHEMA")

    def test_message_schema(self):
        with self.assertRaises(RuntimeError) as caught:
            graph({"mapping": {"a": {"id": "a", "parent": None,
                                     "message": "not-a-dict"}},
                   "current_node": "a"})
        self.assertEqual(str(caught.exception), "MESSAGE_SCHEMA")


class TestPhase1ZipSafety(unittest.TestCase):
    """Red ZIP: unsafe member paths and sensitive names must be flagged."""

    def make_bad_zip_bytes(self):
        buffer = io.BytesIO()
        import zipfile
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_STORED) as archive:
            archive.writestr("../escape.txt", b"traversal attempt")
            archive.writestr("/absolutepath.json", b"[]")
            archive.writestr("C:/drivepath.json", b"[]")
            archive.writestr("backup-code.json", b"{}")
            archive.writestr("conversations-000.json", b"[]")
        return buffer.getvalue()

    def test_unsafe_paths_and_sensitive_names_flagged(self):
        root = Path(tempfile.mkdtemp(prefix="badzip-"))
        self.addCleanup(shutil.rmtree, root, ignore_errors=True)
        export_dir = root / "export"
        export_dir.mkdir()
        (export_dir / "bad.zip").write_bytes(self.make_bad_zip_bytes())
        (root / ec.LOCAL_CONFIG_NAME).write_text(
            json.dumps({"source": {"export_dir": str(export_dir)}}),
            encoding="utf-8")
        ws = ec.load_workspace(root=root, env={})
        with contextlib.redirect_stdout(io.StringIO()):
            inspect_archive.inventory(workspace=ws)
        inventory = json.loads(
            (ws.outputs_dir / "ARCHIVE_INVENTORY.json").read_text(encoding="utf-8"))
        flags = {
            entry["safe_name"]: entry["risk_flags"]
            for entry in inventory["entries"]
        }
        unsafe = [name for name, risk in flags.items()
                  if "unsafe-extraction-path" in risk]
        self.assertGreaterEqual(len(unsafe), 3)
        sensitive = [name for name, risk in flags.items()
                     if "sensitive-name-pattern" in risk]
        self.assertTrue(sensitive, "backup-code member must be flagged")


if __name__ == "__main__":
    unittest.main()
