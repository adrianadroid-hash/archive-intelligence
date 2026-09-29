# Contributing

## Local setup

```bash
git clone <repository-url>
cd archive-intelligence
python -m venv .venv
# Windows: .venv\Scripts\activate    POSIX: source .venv/bin/activate
python -m pip install -e .
python -B -m unittest discover -s tests   # sanity check
```

Python 3.12–3.14; no third-party dependencies are required.

## Running tests

```bash
# full suite
python -B -m unittest discover -s tests -v

# a single test module
python -B -m unittest discover -s tests -p test_phase2_index.py -v

# end-to-end synthetic demo
python examples/synthetic_demo.py
```

Tests and the demo operate only on the generated fixture inside
temporary workspaces. They must never read or write real archive data,
and must not leave files in the repository tree.

## Expectations

- Keep changes focused and covered by tests; the full suite must pass.
- Preserve the privacy posture: no network calls, no new runtime
  dependencies, no real or personal data, no secrets. Keep generated
  outputs out of the repository (see the defensive `.gitignore`).
- Phase 3 and the proprietary/private validation layers are out of
  scope for this repository — see [docs/v0.1-scope.md](docs/v0.1-scope.md).
- Update `README.md` / `CHANGELOG.md` for behavior changes.
- By contributing you agree to release your work under Apache-2.0.
