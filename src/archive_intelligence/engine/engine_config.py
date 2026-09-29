"""Explicit workspace configuration for the local-first archive engine.

Single configuration model for the pipeline (MVP v0.2 Batch 1). Standard
library only. No personal username, no hardcoded machine paths, no network.

Configuration categories
------------------------
user supplied          source.export_dir, source.export_zip,
                       expectations.expected_root, paths.* overrides
derived                workspace root; staging/corpus/outputs/phase2/
                       phase3/temp directories (computed from the root)
runtime discovered     Python version/implementation/platform, feature probes
validation expectations fail-closed safety expectations: write containment,
                       optional expected-root pin, supported Python range

Configuration file discovery (later layers override earlier, key by key):
  1. built-in neutral defaults            (no user input at all)
  2. <root>/engine_config.json            optional committed neutral profile
  3. <root>/engine_config.local.json      machine/user profile (gitignored)
  4. the file named by $ENGINE_CONFIG, or config_path= (CLI/automation/tests)

Path safety contract
--------------------
* Inputs (source zip/dir) are read-only and may live anywhere the user chose;
  relative input values resolve against the workspace root.
* Every write target must pass through write_target()/contained() and must
  resolve inside the workspace root after `..`, absolute paths and symlink
  resolution are applied. Configured path overrides follow the same rule.
* Failures raise ConfigError whose message is a fixed uppercase code only.
  No path, value or exception text is ever placed in an error message.

Intended consumers: CLI scripts, desktop GUI (later) and automated tests,
via load_workspace(root=..., config_path=...).
"""
from __future__ import annotations

import copy
import json
import os
import platform
import re
import sys
from pathlib import Path

CONFIG_NAME = "engine_config.json"
LOCAL_CONFIG_NAME = "engine_config.local.json"
ENV_CONFIG = "ENGINE_CONFIG"
EXAMPLE_CONFIG_NAME = "engine_config.example.json"

DEFAULT_CONFIG = {
    "$comment": "Built-in neutral defaults: no user input, no machine paths.",
    "source": {"export_dir": None, "export_zip": None},
    "paths": {
        "staging": "staging",
        "corpus": "corpus",
        "outputs": "outputs",
        "phase2": None,
        "phase3": None,
        "temp": ".work",
    },
    "expectations": {"expected_root": None, "min_free_bytes": 0},
    "runtime": {"python_requires": ">=3.12,<3.15"},
}

_ALLOWED_KEYS = {
    "root": {"$comment", "profile", "source", "paths", "expectations", "runtime"},
    "source": {"$comment", "export_dir", "export_zip"},
    "paths": {"$comment", "staging", "corpus", "outputs", "phase2", "phase3", "temp"},
    "expectations": {"$comment", "expected_root", "min_free_bytes"},
    "runtime": {"$comment", "python_requires"},
}

_CODE_RE = re.compile(r"[A-Z][A-Z0-9_]*")
_VERSION_PART_RE = re.compile(r"^(>=|<=|==|!=|>|<)\s*(\d+(?:\.\d+)*)$")


class ConfigError(RuntimeError):
    """Configuration failure carrying a fixed uppercase code only."""

    def __init__(self, code: str):
        if not isinstance(code, str) or not _CODE_RE.fullmatch(code):
            # Defensive redaction guarantee: codes are never free text.
            raise ValueError("CONFIG_ERROR_CODE_INVALID")
        super().__init__(code)


def discover_root() -> Path:
    """Workspace root default: the repository root (the directory above ``src/``)."""
    return Path(__file__).resolve().parents[3]


def _deep_merge(base: dict, overlay: dict) -> dict:
    out = copy.deepcopy(base)
    for key, value in overlay.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = copy.deepcopy(value)
    return out


def _validate_dict(config) -> dict:
    """Type/shape validation for one configuration layer or the merged result."""
    if not isinstance(config, dict):
        raise ConfigError("CONFIG_NOT_OBJECT")
    if set(config) - _ALLOWED_KEYS["root"]:
        raise ConfigError("CONFIG_VALUE_INVALID")
    sections = {}
    for name in ("source", "paths", "expectations", "runtime"):
        section = config.get(name)
        if section is None:
            continue
        if not isinstance(section, dict):
            raise ConfigError("CONFIG_VALUE_INVALID")
        if set(section) - _ALLOWED_KEYS[name]:
            raise ConfigError("CONFIG_VALUE_INVALID")
        sections[name] = section
    source = sections.get("source", {})
    paths = sections.get("paths", {})
    expectations = sections.get("expectations", {})
    runtime = sections.get("runtime", {})
    for key in ("export_dir", "export_zip"):
        if key in source and not (source[key] is None or isinstance(source[key], str)):
            raise ConfigError("CONFIG_VALUE_INVALID")
    for key in ("staging", "corpus", "outputs", "temp"):
        if key in paths and (not isinstance(paths[key], str) or not paths[key].strip()):
            raise ConfigError("CONFIG_VALUE_INVALID")
    for key in ("phase2", "phase3"):
        if key in paths and not (paths[key] is None or isinstance(paths[key], str)):
            raise ConfigError("CONFIG_VALUE_INVALID")
    if "expected_root" in expectations and not (
        expectations["expected_root"] is None or isinstance(expectations["expected_root"], str)
    ):
        raise ConfigError("CONFIG_VALUE_INVALID")
    if "min_free_bytes" in expectations:
        value = expectations["min_free_bytes"]
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ConfigError("CONFIG_VALUE_INVALID")
    if "python_requires" in runtime and (
        not isinstance(runtime["python_requires"], str) or not runtime["python_requires"].strip()
    ):
        raise ConfigError("CONFIG_VALUE_INVALID")
    if "profile" in config and not (
        config["profile"] is None or isinstance(config["profile"], str)
    ):
        raise ConfigError("CONFIG_VALUE_INVALID")
    return config


def _read_layer(path: Path, *, required: bool) -> dict:
    if not path.exists():
        if required:
            raise ConfigError("CONFIG_FILE_MISSING")
        return {}
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        raise ConfigError("CONFIG_LAYER_UNREADABLE") from None
    try:
        data = json.loads(text)
    except ValueError:
        raise ConfigError("CONFIG_JSON_INVALID") from None
    return _validate_dict(data)


def load_config(root=None, config_path=None, env=None) -> dict:
    """Load and merge configuration layers for a workspace root."""
    environ = os.environ if env is None else env
    base = Path(root).resolve() if root is not None else discover_root()
    merged = copy.deepcopy(DEFAULT_CONFIG)
    for name in (CONFIG_NAME, LOCAL_CONFIG_NAME):
        merged = _deep_merge(merged, _read_layer(base / name, required=False))
    explicit = config_path if config_path is not None else environ.get(ENV_CONFIG)
    if explicit:
        path = Path(explicit).expanduser()
        if not path.exists():
            raise ConfigError("CONFIG_FILE_MISSING")
        merged = _deep_merge(merged, _read_layer(path, required=True))
    return _validate_dict(merged)


def _python_satisfies(version_info, spec: str) -> bool:
    if not isinstance(spec, str) or not spec.strip():
        raise ConfigError("PYTHON_REQUIREMENTS_INVALID")
    version = tuple(int(part) for part in version_info[:3])
    parts = [part.strip() for part in spec.split(",") if part.strip()]
    if not parts:
        raise ConfigError("PYTHON_REQUIREMENTS_INVALID")
    for part in parts:
        match = _VERSION_PART_RE.match(part)
        if not match:
            raise ConfigError("PYTHON_REQUIREMENTS_INVALID")
        operator, wanted_text = match.group(1), match.group(2)
        wanted = tuple(int(part_) for part_ in wanted_text.split("."))
        width = max(len(version), len(wanted))
        left = version + (0,) * (width - len(version))
        right = wanted + (0,) * (width - len(wanted))
        ok = {
            ">=": left >= right,
            ">": left > right,
            "<=": left <= right,
            "<": left < right,
            "==": left == right,
            "!=": left != right,
        }[operator]
        if not ok:
            return False
    return True


class Workspace:
    """Resolved configuration for one workspace root."""

    def __init__(self, root: Path, config: dict):
        self.root = Path(root).resolve()
        self.config = config

    # --- user-supplied inputs -----------------------------------------
    def _input(self, value):
        if value is None:
            return None
        path = Path(value).expanduser()
        if not path.is_absolute():
            path = self.root / path
        return path

    @property
    def source_export_dir(self):
        return self._input(self.config["source"]["export_dir"])

    @property
    def source_export_zip(self):
        return self._input(self.config["source"]["export_zip"])

    def require_source_dir(self) -> Path:
        path = self.source_export_dir
        if path is None:
            raise ConfigError("SOURCE_DIR_NOT_CONFIGURED")
        if not path.exists():
            raise ConfigError("SOURCE_DIR_MISSING")
        if not path.is_dir():
            raise ConfigError("SOURCE_DIR_NOT_DIRECTORY")
        return path

    def require_source_zip(self) -> Path:
        path = self.source_export_zip
        if path is None:
            raise ConfigError("SOURCE_ZIP_NOT_CONFIGURED")
        if not path.exists():
            raise ConfigError("SOURCE_ZIP_MISSING")
        if not path.is_file() or path.suffix.lower() != ".zip":
            raise ConfigError("SOURCE_ZIP_NOT_FILE")
        return path

    # --- derived workspace paths --------------------------------------
    def _contained(self, value, fallback: str) -> Path:
        path = Path(value) if value else Path(fallback)
        if not path.is_absolute():
            path = self.root / path
        resolved = path.resolve()
        if not resolved.is_relative_to(self.root):
            raise ConfigError("CONFIG_PATH_OUTSIDE_WORKSPACE")
        return resolved

    @property
    def staging_dir(self) -> Path:
        return self._contained(self.config["paths"]["staging"], "staging")

    @property
    def corpus_dir(self) -> Path:
        return self._contained(self.config["paths"]["corpus"], "corpus")

    @property
    def outputs_dir(self) -> Path:
        return self._contained(self.config["paths"]["outputs"], "outputs")

    @property
    def phase2_dir(self) -> Path:
        override = self.config["paths"]["phase2"]
        if override:
            return self._contained(override, "outputs/phase2")
        return self.outputs_dir / "phase2"

    @property
    def phase3_dir(self) -> Path:
        override = self.config["paths"]["phase3"]
        if override:
            return self._contained(override, "outputs/phase3")
        return self.outputs_dir / "phase3"

    @property
    def temp_dir(self) -> Path:
        return self._contained(self.config["paths"]["temp"], ".work")

    # --- validation / safety expectations -----------------------------
    @property
    def expected_root(self):
        value = self.config["expectations"]["expected_root"]
        if value is None:
            return None
        path = Path(value).expanduser()
        if not path.is_absolute():
            path = self.root / path
        return path.resolve()

    @property
    def min_free_bytes(self) -> int:
        return self.config["expectations"]["min_free_bytes"]

    @property
    def python_requires(self) -> str:
        return self.config["runtime"]["python_requires"]

    def matches_expected_root(self, path) -> bool:
        pin = self.expected_root
        if pin is None:
            return True
        return Path(path).resolve() == pin

    def require_supported_python(self, version_info=None) -> None:
        if not _python_satisfies(
            sys.version_info if version_info is None else version_info,
            self.python_requires,
        ):
            raise ConfigError("PYTHON_VERSION_UNSUPPORTED")

    def write_target(self, *parts) -> Path:
        """Resolve a write path; refuse anything outside the workspace root."""
        candidate = Path(*parts) if parts else Path()
        if not candidate.is_absolute():
            candidate = self.root / candidate
        resolved = candidate.resolve()
        if not resolved.is_relative_to(self.root):
            raise ConfigError("WRITE_OUTSIDE_WORKSPACE")
        return resolved

    def contained(self, path) -> bool:
        try:
            return Path(path).resolve().is_relative_to(self.root)
        except (OSError, ValueError):
            return False

    def summary(self, include_paths: bool = False) -> dict:
        """Diagnostic summary. Redacted (booleans/runtime only) by default."""
        data = {
            "runtime": {
                "python_version": ".".join(str(part) for part in sys.version_info[:3]),
                "python_implementation": platform.python_implementation(),
                "platform": sys.platform,
                "python_requires": self.python_requires,
            },
            "expectations": {
                "expected_root_pinned": self.expected_root is not None,
                "min_free_bytes": self.min_free_bytes,
            },
            "inputs": {
                "source_dir_configured": self.config["source"]["export_dir"] is not None,
                "source_zip_configured": self.config["source"]["export_zip"] is not None,
            },
            "write_containment": "workspace_root",
        }
        if include_paths:
            data["paths"] = {
                "root": str(self.root),
                "outputs": str(self.outputs_dir),
                "staging": str(self.staging_dir),
                "corpus": str(self.corpus_dir),
                "temp": str(self.temp_dir),
                "source_dir": str(self.source_export_dir) if self.source_export_dir else None,
                "source_zip": str(self.source_export_zip) if self.source_export_zip else None,
                "expected_root": str(self.expected_root) if self.expected_root else None,
            }
        return data


def load_workspace(root=None, config_path=None, env=None) -> Workspace:
    """Resolve the configuration for a workspace and check it structurally."""
    base = Path(root).resolve() if root is not None else discover_root()
    if not base.exists():
        raise ConfigError("WORKSPACE_MISSING")
    if not base.is_dir():
        raise ConfigError("WORKSPACE_NOT_DIRECTORY")
    config = load_config(root=base, config_path=config_path, env=env)
    return Workspace(base, config)
