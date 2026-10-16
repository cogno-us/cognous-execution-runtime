# Repository Guidelines

Repository: https://github.com/cogno-us/cognous-execution-runtime (Cognous Execution Runtime, retained Python package name Moltbot Safe). `CLAUDE.md` is a symlink to this file.

## Scope
- The supported code is the Python execution boundary under `engine/`. See [README.md](README.md) and [docs/execution-boundary.md](docs/execution-boundary.md).
- The upstream Moltbot TypeScript/native application is quarantined at tag `upstream-moltbot-quarantine-20261016` and is not on `main`. Do not restore, build or extend it here. See [docs/quarantine-upstream-moltbot-20261016.md](docs/quarantine-upstream-moltbot-20261016.md).
- The destination is synthetic SQLite only. Do not add real payment, network, subprocess or production-credential effects to the default path.

## Layout
- `engine/`: the Python safety layer (Apache-2.0; see `engine/LICENSE`).
- `tests/`: component-local pytest suites.
- `examples/`: legacy demos and the optional OpenShell worker image (`examples/openshell/`).
- `docs/`: Cognous boundary, profile and workstream records. Treat `docs/workstreams/` evidence files as historical records: add new text instead of rewriting them.
- `.github/workflows/`: Python CI plus thin wrappers that call pinned reusable workflows in `cogno-us/cognous-stack-qualification`. Keep pinned SHAs unchanged unless a change explicitly requalifies them.

## Tests
```bash
python -m pip install pytest==8.3.5 pydantic==2.11.9
PYTHONPATH=".:src" python -m pytest -q tests
```
Cross-component suites run through the qualification workflows; see [CROSS_COMPONENT_TESTS.md](CROSS_COMPONENT_TESTS.md).

## Change rules
- Keep changes minimal, explicit and auditable; add or update a test for every behavior change.
- Never add implicit, default or wildcard permissions.
- Preserve LICENSE, LICENSE-APACHE-2.0, NOTICE and third-party notices.
- Preserve governance boundary wording (non-authorizing, HOLD, no production). Do not claim security clearance, certification or production readiness.
- Changes to this candidate component require F3 requalification, refreeze and re-review before acceptance.
