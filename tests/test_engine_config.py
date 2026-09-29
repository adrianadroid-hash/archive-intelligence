"""Configuration layer tests (MVP v0.2 Batch 1, PART 6).

Covers: neutral workspaces, spaces/Unicode in paths, nonexistent input,
wrong file type, workspace containment, path traversal, Windows/relative
paths, redacted error output, config precedence, malformed configs, runtime
Python expectations, and the absence of the current account username from
generated neutral artifacts.

All tests run in throwaway temp workspaces. They never read private archive
data and never write into the project tree.
"""
from __future__ import annotations

import getpass
import json
import re
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
PROJECT = HERE.parent
SRC = PROJECT / "src"
ENGINE = SRC / "archive_intelligence" / "engine"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from archive_intelligence.engine import engine_config as ec  # noqa: E402

USERNAME = getpass.getuser()          # environment account handle
PROFILE_NAME = Path.home().name       # profile directory name
# Both privacy-relevant identity handles, lower-cased and de-duplicated.
IDENTITIES = sorted({USERNAME.casefold(), PROFILE_NAME.casefold()})
CODE_RE = re.compile(r"^[A-Z][A-Z0-9_]*$")


def find_identities(text):
    """Return identities present in text; short handles use word boundaries
    so incidental substrings (e.g. 'next') do not read as account leaks."""
    lowered = text.casefold()
    found = []
    for identity in IDENTITIES:
        if len(identity) >= 4:
            if identity in lowered:
                found.append(identity)
        elif re.search(r"(?<![a-z0-9])" + re.escape(identity) + r"(?![a-z0-9])", lowered):
            found.append(identity)
    return found


class ConfigCase(unittest.TestCase):
    def make_root(self, prefix="cfg-"):
        name = tempfile.mkdtemp(prefix=prefix)
        self.addCleanup(shutil.rmtree, name, ignore_errors=True)
        return Path(name).resolve()

    def write_layer(self, root, data, name=ec.LOCAL_CONFIG_NAME):
        path = root / name
        path.write_text(json.dumps(data, ensure_ascii=True, indent=2),
                        encoding="utf-8")
        return path

    def load(self, root, **kwargs):
        kwargs.setdefault("env", {})
        return ec.load_workspace(root=root, **kwargs)

    def error_code(self, callback, *args, **kwargs):
        with self.assertRaises(ec.ConfigError) as caught:
            callback(*args, **kwargs)
        return str(caught.exception)


class TestDiscoveryAndDefaults(ConfigCase):
    def test_neutral_defaults_load_in_empty_workspace(self):
        root = self.make_root()
        ws = self.load(root)
        self.assertEqual(ws.root, root)
        self.assertIsNone(ws.source_export_dir)
        self.assertIsNone(ws.expected_root)
        self.assertEqual(ws.outputs_dir, root / "outputs")
        self.assertEqual(ws.staging_dir, root / "staging")
        self.assertEqual(ws.corpus_dir, root / "corpus")
        self.assertEqual(ws.phase2_dir, root / "outputs" / "phase2")
        self.assertEqual(ws.phase3_dir, root / "outputs" / "phase3")
        self.assertEqual(ws.temp_dir, root / ".work")
        for path in (ws.outputs_dir, ws.staging_dir, ws.corpus_dir,
                     ws.phase2_dir, ws.phase3_dir, ws.temp_dir):
            self.assertTrue(path.is_relative_to(root))

    def test_discover_root_is_project_root(self):
        self.assertEqual(ec.discover_root(), PROJECT)

    def test_missing_workspace_fails_closed(self):
        absent = Path(tempfile.mkdtemp()) / "never-created"
        shutil.rmtree(Path(tempfile.mkdtemp()), ignore_errors=True)
        self.assertEqual(self.error_code(self.load, absent), "WORKSPACE_MISSING")


class TestPathShapes(ConfigCase):
    def test_workspace_path_with_spaces(self):
        root = self.make_root(prefix="ws with spaces -")
        ws = self.load(root)
        self.assertIn("ws with spaces", str(ws.outputs_dir))
        self.assertTrue(ws.outputs_dir.is_relative_to(root))
        self.assertEqual(ws.write_target("outputs", "a.json"),
                         root / "outputs" / "a.json")

    def test_workspace_path_with_unicode(self):
        root = self.make_root(prefix="wörkspäce-测试-")
        ws = self.load(root)
        self.assertTrue(ws.outputs_dir.is_relative_to(root))
        self.assertIn("测试", str(ws.corpus_dir))

    def test_relative_source_resolves_against_root(self):
        root = self.make_root()
        (root / "local-export").mkdir()
        self.write_layer(root, {"source": {"export_dir": "local-export"}})
        ws = self.load(root)
        self.assertEqual(ws.require_source_dir(), root / "local-export")

    def test_absolute_source_input_outside_workspace_allowed(self):
        # Inputs are read-only user choices and may live anywhere.
        root = self.make_root()
        outside = self.make_root(prefix="outside-export-")
        self.write_layer(root, {"source": {"export_dir": str(outside)}})
        ws = self.load(root)
        self.assertEqual(ws.require_source_dir(), outside)


class TestInputValidation(ConfigCase):
    def test_source_not_configured(self):
        root = self.make_root()
        self.assertEqual(self.error_code(self.load(root).require_source_dir),
                         "SOURCE_DIR_NOT_CONFIGURED")

    def test_nonexistent_source_dir(self):
        root = self.make_root()
        self.write_layer(root, {"source": {"export_dir": "does-not-exist"}})
        self.assertEqual(self.error_code(self.load(root).require_source_dir),
                         "SOURCE_DIR_MISSING")

    def test_source_dir_pointing_at_file(self):
        root = self.make_root()
        (root / "not-a-dir.zip").write_bytes(b"not really a zip")
        self.write_layer(root, {"source": {"export_dir": "not-a-dir.zip"}})
        self.assertEqual(self.error_code(self.load(root).require_source_dir),
                         "SOURCE_DIR_NOT_DIRECTORY")

    def test_wrong_file_type_for_zip(self):
        root = self.make_root()
        (root / "notes.txt").write_text("plain text, not a zip", encoding="utf-8")
        self.write_layer(root, {"source": {"export_zip": "notes.txt"}})
        self.assertEqual(self.error_code(self.load(root).require_source_zip),
                         "SOURCE_ZIP_NOT_FILE")

    def test_nonexistent_zip(self):
        root = self.make_root()
        self.write_layer(root, {"source": {"export_zip": "missing.zip"}})
        self.assertEqual(self.error_code(self.load(root).require_source_zip),
                         "SOURCE_ZIP_MISSING")


class TestContainment(ConfigCase):
    def test_write_target_inside_workspace(self):
        root = self.make_root()
        ws = self.load(root)
        self.assertEqual(ws.write_target("outputs", "x.json"),
                         root / "outputs" / "x.json")
        self.assertEqual(ws.write_target(root / "outputs" / "y.json"),
                         root / "outputs" / "y.json")
        self.assertTrue(ws.contained(root / "outputs"))
        self.assertTrue(ws.contained(root / "outputs" / ".." / "outputs" / "z"))

    def test_write_target_traversal_rejected(self):
        root = self.make_root()
        ws = self.load(root)
        self.assertEqual(self.error_code(ws.write_target, "..", "escape.json"),
                         "WRITE_OUTSIDE_WORKSPACE")
        outside = Path(tempfile.gettempdir()).resolve()
        if outside == root:
            self.skipTest("tempdir equals workspace root")
        self.assertEqual(self.error_code(ws.write_target, outside / "escape.json"),
                         "WRITE_OUTSIDE_WORKSPACE")
        self.assertFalse(ws.contained(outside / "escape.json"))

    def test_configured_path_override_outside_workspace_rejected(self):
        root = self.make_root()
        self.write_layer(root, {"paths": {"outputs": "../../escape"}})
        ws = self.load(root)
        self.assertEqual(self.error_code(lambda: ws.outputs_dir),
                         "CONFIG_PATH_OUTSIDE_WORKSPACE")

    def test_configured_absolute_path_override_outside_rejected(self):
        root = self.make_root()
        outside = Path(tempfile.gettempdir()).resolve() / "outside-outputs"
        self.write_layer(root, {"paths": {"outputs": str(outside)}})
        ws = self.load(root)
        self.assertEqual(self.error_code(lambda: ws.outputs_dir),
                         "CONFIG_PATH_OUTSIDE_WORKSPACE")

    def test_traversal_in_layer_values_rejected(self):
        root = self.make_root()
        self.write_layer(root, {"paths": {"staging": str(Path("..") / ".." / "staging")}})
        ws = self.load(root)
        self.assertEqual(self.error_code(lambda: ws.staging_dir),
                         "CONFIG_PATH_OUTSIDE_WORKSPACE")


class TestExpectedRootPin(ConfigCase):
    def test_pin_matches_and_mismatches(self):
        root = self.make_root()
        self.write_layer(root, {"expectations": {"expected_root": str(root)}})
        ws = self.load(root)
        self.assertTrue(ws.matches_expected_root(root))
        self.assertFalse(ws.matches_expected_root(root / "other"))

    def test_no_pin_accepts_any_root(self):
        root = self.make_root()
        ws = self.load(root)
        self.assertTrue(ws.matches_expected_root(root / "anywhere"))


class TestErrorRedaction(ConfigCase):
    def collect_codes(self, root):
        codes = []
        probes = [
            lambda: self.load(root).require_source_dir(),
            lambda: self.load(root).write_target("..", "x"),
            lambda: self.load(root).require_supported_python((3, 11, 0)),
            lambda: ec.load_workspace(root=root / "absent-child"),
            lambda: ec.ConfigError("free text C:\\Users\\someone"),
        ]
        for probe in probes:
            try:
                probe()
            except (ec.ConfigError, ValueError) as error:
                codes.append(str(error))
        return codes

    def test_error_output_is_uppercase_code_only(self):
        root = self.make_root()
        codes = self.collect_codes(root)
        for code in codes:
            self.assertRegex(code, CODE_RE)
            self.assertEqual(find_identities(code), [])
            self.assertNotIn(str(root), code)

    def test_config_error_rejects_free_text(self):
        with self.assertRaises(ValueError):
            ec.ConfigError("path C:\\Users\\someone\\secret")

    def test_summary_is_redacted_by_default(self):
        root = self.make_root()
        ws = self.load(root)
        rendered = json.dumps(ws.summary())
        escaped_root = str(root).replace("\\", "\\\\")
        self.assertNotIn(str(root), rendered)
        self.assertNotIn(escaped_root, rendered)
        self.assertEqual(find_identities(rendered), [])
        self.assertNotIn("\\\\", rendered)
        with_paths = json.dumps(ws.summary(include_paths=True))
        self.assertIn(escaped_root, with_paths)


class TestMalformedConfigs(ConfigCase):
    def test_invalid_json_fails_closed(self):
        root = self.make_root()
        (root / ec.LOCAL_CONFIG_NAME).write_text("{not json", encoding="utf-8")
        self.assertEqual(self.error_code(self.load, root), "CONFIG_JSON_INVALID")

    def test_empty_config_file_fails_closed(self):
        root = self.make_root()
        (root / ec.LOCAL_CONFIG_NAME).write_text("", encoding="utf-8")
        self.assertEqual(self.error_code(self.load, root), "CONFIG_JSON_INVALID")

    def test_config_not_an_object_fails_closed(self):
        root = self.make_root()
        (root / ec.LOCAL_CONFIG_NAME).write_text("[1,2,3]", encoding="utf-8")
        self.assertEqual(self.error_code(self.load, root), "CONFIG_NOT_OBJECT")

    def test_unknown_key_fails_closed(self):
        root = self.make_root()
        self.write_layer(root, {"surprise": 1})
        self.assertEqual(self.error_code(self.load, root), "CONFIG_VALUE_INVALID")

    def test_wrong_value_type_fails_closed(self):
        root = self.make_root()
        self.write_layer(root, {"expectations": {"min_free_bytes": "lots"}})
        self.assertEqual(self.error_code(self.load, root), "CONFIG_VALUE_INVALID")
        self.write_layer(root, {"paths": {"outputs": 7}})
        self.assertEqual(self.error_code(self.load, root), "CONFIG_VALUE_INVALID")

    def test_explicit_config_file_missing_fails_closed(self):
        root = self.make_root()
        self.assertEqual(
            self.error_code(self.load, root,
                            config_path=str(root / "nope.json")),
            "CONFIG_FILE_MISSING")

    def test_malformed_explicit_config_file_fails_closed(self):
        root = self.make_root()
        bad = root / "custom.json"
        bad.write_text("{", encoding="utf-8")
        self.assertEqual(self.error_code(self.load, root, config_path=str(bad)),
                         "CONFIG_JSON_INVALID")


class TestLayerPrecedence(ConfigCase):
    def test_local_layer_overrides_committed_layer(self):
        root = self.make_root()
        self.write_layer(root, {"expectations": {"min_free_bytes": 100}},
                         name=ec.CONFIG_NAME)
        self.write_layer(root, {"expectations": {"min_free_bytes": 200}})
        ws = self.load(root)
        self.assertEqual(ws.min_free_bytes, 200)

    def test_layers_merge_disjoint_keys(self):
        root = self.make_root()
        (root / "local-export").mkdir()
        self.write_layer(root, {"source": {"export_dir": "local-export"}},
                         name=ec.CONFIG_NAME)
        self.write_layer(root, {"expectations": {"expected_root": str(root)}})
        ws = self.load(root)
        self.assertEqual(ws.require_source_dir(), root / "local-export")
        self.assertTrue(ws.matches_expected_root(root))

    def test_env_layer_overrides_files(self):
        root = self.make_root()
        self.write_layer(root, {"expectations": {"min_free_bytes": 200}})
        env_file = root / "env-profile.json"
        self.write_layer(root, {"expectations": {"min_free_bytes": 300}},
                         name=env_file.name)
        ws = self.load(root, env={"ENGINE_CONFIG": str(env_file)})
        self.assertEqual(ws.min_free_bytes, 300)


class TestRuntimeExpectations(ConfigCase):
    def test_current_interpreter_passes_default_spec(self):
        ws = self.load(self.make_root())
        ws.require_supported_python()  # must not raise

    def test_python_floor_enforced(self):
        ws = self.load(self.make_root())
        self.assertEqual(
            self.error_code(ws.require_supported_python, (3, 11, 0)),
            "PYTHON_VERSION_UNSUPPORTED")

    def test_unsatisfiable_spec_fails(self):
        root = self.make_root()
        self.write_layer(root, {"runtime": {"python_requires": ">=99.0"}})
        ws = self.load(root)
        self.assertEqual(self.error_code(ws.require_supported_python),
                         "PYTHON_VERSION_UNSUPPORTED")

    def test_invalid_spec_fails_closed(self):
        root = self.make_root()
        self.write_layer(root, {"runtime": {"python_requires": "~=3.12"}})
        ws = self.load(root)
        self.assertEqual(self.error_code(ws.require_supported_python),
                         "PYTHON_REQUIREMENTS_INVALID")

    def test_spec_with_compatible_range_passes(self):
        ws = self.load(self.make_root())
        ws.require_supported_python((3, 12, 0))
        ws.require_supported_python((3, 14, 7))


class TestNoMachinePaths(ConfigCase):
    ARTIFACTS = [
        PROJECT / "src" / "archive_intelligence" / "engine" / "engine_config.py",
        PROJECT / "engine_config.example.json",
        PROJECT / "tests" / "fixtures" / "fixture_manifest.json",
    ]
    OPTIONAL = [
        PROJECT / "pyproject.toml",
        PROJECT / "requirements.txt",
        PROJECT / "README.md",
    ]

    def test_module_source_has_no_machine_paths(self):
        text = (ENGINE / "engine_config.py").read_text(encoding="utf-8")
        self.assertEqual(find_identities(text), [])
        self.assertNotIn("C:\\Users", text)
        self.assertNotIn("getpass", text)

    def test_committed_artifacts_have_no_username(self):
        for path in self.ARTIFACTS + self.OPTIONAL:
            if not path.exists():
                continue
            with self.subTest(artifact=path.name):
                text = path.read_text(encoding="utf-8", errors="replace")
                self.assertEqual(find_identities(text), [],
                                 f"{path.name} contains an account identity")

    def test_example_config_is_neutral(self):
        data = json.loads((PROJECT / "engine_config.example.json").read_text(encoding="utf-8"))
        rendered = json.dumps(data)
        self.assertEqual(find_identities(rendered), [])
        self.assertIsNone(data["source"]["export_dir"])
        self.assertIsNone(data["source"]["export_zip"])
        self.assertIsNone(data["expectations"]["expected_root"])

    def test_module_import_needs_no_environment(self):
        # No ENGINE_CONFIG, no local files, no username: neutral defaults only.
        root = self.make_root()
        ws = ec.load_workspace(root=root, env={})
        self.assertEqual(ws.python_requires, ec.DEFAULT_CONFIG["runtime"]["python_requires"])


if __name__ == "__main__":
    unittest.main()
