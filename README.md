# Archive Intelligence

Local-first Python engine for inspecting and structurally indexing
ChatGPT export archives entirely on your own machine. It inventories
the archive, renders privacy-aware Phase 1 reports, verifies them, and
builds a verified local Phase 2 structural index — with fail-closed
checks at every step.

## The problem it solves

A chat export is an opaque ZIP holding thousands of files. Before doing
anything with it you need to know what is inside, which parts carry
private content, and whether your processing touched nothing else.
Archive Intelligence answers that locally: no network calls, no
third-party services, Python standard library only, and generated
reports that never echo your raw filesystem paths.

## v0.1 scope

v0.1 (frozen) includes:

- Archive intake and inventory (`inspect_archive`)
- Phase 1 report generation (`build_phase1_reports`)
- Phase 1 verification (`verify_phase1`, fails closed)
- Phase 2 selective staging (`phase2_stage`)
- Phase 2 structural indexing (`phase2_index`)
- Synthetic fixture support and the test suite (`tests/`)

Intentionally excluded: Phase 3 (remains private/deferred),
proprietary/private validation layers, and any real user data.
Full boundary: [docs/v0.1-scope.md](docs/v0.1-scope.md).

## Privacy and local-first posture

- Runs fully offline. Zero third-party runtime dependencies (Python
  standard library only; `dependencies = []` in `pyproject.toml`).
- Nothing is uploaded; there is no telemetry.
- Phase 1 reports redact the workspace root and the source archive
  path, replacing them with a `<source-archive>` alias plus a
  SHA-256 location fingerprint; sync/backup wording stays
  provider-neutral.
- Phase 2 persists privacy-audited structure only: raw message bodies
  and titles are never written to the index (digests and fixed
  enums instead).
- Fail-closed design: reconciliation, provenance-hash and privacy
  checks abort the run with a fixed error code rather than continue.
- The repository ships no real data. Tests and the demo use a
  fabricated synthetic export; `.gitignore` blocks exports, outputs,
  databases, secrets and local configs from being committed.

## Requirements

- Python 3.12–3.14 (`>=3.12,<3.15`)
- No third-party runtime packages

## Installation

```bash
git clone <repository-url>
cd archive-intelligence
python -m venv .venv
# Windows: .venv\Scripts\activate    POSIX: source .venv/bin/activate
python -m pip install -e .
```

## Synthetic demo

Runs the full v0.1 pipeline (intake → Phase 1 reports → verification →
Phase 2 staging → structural indexing) against a generated fixture
export in a temporary workspace, then removes it:

```bash
python examples/synthetic_demo.py
```

Expected final line: `PASS: full v0.1 pipeline completed on the
synthetic fixture.` Phase 2 staging checks require roughly 2 GiB of
free disk space. Nothing is written into the repository.

## Running tests

```bash
python -B -m unittest discover -s tests -v
```

All tests run against the synthetic fixture in temporary workspaces:
no real data is required and no repository files are modified.

## Using your own export (Phase 1)

1. Copy `engine_config.example.json` to `engine_config.local.json`
   at the workspace root (gitignored) and set `source.export_dir` /
   `source.export_zip` — paths are workspace-relative. For an editable
   install the workspace root is the repository checkout.
2. Run the Phase 1 chain:

```bash
python -m archive_intelligence.engine.inspect_archive
python -m archive_intelligence.engine.build_phase1_reports
python -m archive_intelligence.engine.verify_phase1
```

Outputs land in `outputs/` and `STATUS.md` (gitignored). `verify_phase1`
exits non-zero if any required report is missing or corrupted.

Phase 2 steps (`phase2_stage`, `phase2_index`) are exercised by the
demo and the test suite; `phase2_index` additionally enforces a
provenance manifest (`PARSER_TESTS.json`) — see
`examples/synthetic_demo.py` for how it is recorded.

## Configuration and environment

- `engine_config.example.json` documents every configuration key.
  Copy it to `engine_config.local.json` (gitignored) and fill in your
  values; never commit real local paths.
- No environment variables are required. The one optional variable,
  `ENGINE_CONFIG`, points at an explicit configuration file — see
  `.env.example`. The engine does not read `.env` files itself.

## Project structure

```
src/archive_intelligence/
  engine/                  # v0.1 engine components
    engine_config.py        workspace configuration (fail-closed)
    engine_discovery.py     member discovery and staging plan
    inspect_archive.py      Phase 1 intake and inventory
    build_phase1_reports.py Phase 1 report rendering
    verify_phase1.py        Phase 1 verification
    phase2_common.py        shared Phase 2 runtime
    phase2_stage.py         selective staging
    phase2_index.py         structural indexing
tests/                     # full suite (synthetic fixture)
  fixtures/                # synthetic export generator
examples/                  # synthetic_demo.py
docs/                      # v0.1-scope.md
```

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md).

## Security

See [SECURITY.md](SECURITY.md).

## Roadmap

See [ROADMAP.md](ROADMAP.md) and [docs/v0.1-scope.md](docs/v0.1-scope.md).

## License

Apache-2.0 — see [LICENSE](LICENSE).
