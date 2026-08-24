# Development

All tasks run via `make` or `uv run python devtools/dev.py [task]` (cross-platform equivalent).

## Tasks

| Task | What it does |
|------|-------------|
| *(default)* | `install`, `lint`, `test`|
| `install` | `uv sync --all-extras` — create/sync `.venv` with all optional deps |
| `lint` | `codespell` → `ruff check --fix` → `ruff format` → `basedpyright` |
| `test` | Run pytest with coverage |
| `coverage` | Run pytest and open HTML coverage report in browser |
| `upgrade` | `uv sync --upgrade --all-extras --dev` — bump all deps to latest |
| `build` | `uv build` — produce sdist + wheel in `dist/` |
| `docs` | `zensical build` — generate static site in `site/`; appends HTML coverage report at `site/coverage/` |
| `docs-serve` | `zensical serve` — live preview at `http://127.0.0.1:8000` |
| `clean` | Remove `dist/`, `site/`, `htmlcov/`, `.venv/`, `.mypy_cache/`, `__pycache__/`, `*.egg-info/` |

## Lint steps

Run in order by `devtools/lint.py`:

1. **codespell** — spell-check source and `README.md`, auto-fix in place
2. **ruff check --fix** — lint with auto-fixes
3. **ruff format** — format
4. **basedpyright** — type-check `src/` and `devtools/`

## Integration tests

`tests/test_integration.py` exercises the driver against a real CoBrite unit (all
tests are read-only and do not modify laser state). They're skipped by default and
only run when a device address is supplied:

```bash
uv run pytest tests/test_integration.py --cobrite-address 192.168.1.99
# or
COBRITE_ADDRESS=192.168.1.99 uv run pytest tests/test_integration.py
```

These tests use the real PyVISA transport (`open()` without an injected
`_transport`), which needs a VISA backend to open a `TCPIP::<ip>::2000::SOCKET`
resource. `pyvisa-py` is included in the `dev` dependency group for this purpose,
so `uv sync --dev` (or the default `install` task) is sufficient — no NI-VISA
runtime install is required.

Coverage failures are expected when running only `test_integration.py` in
isolation (the 80% gate is calibrated for the full unit-test suite); pass
`--no-cov` or run alongside `tests/test_unit.py` to avoid the spurious failure.

## Notes

- `clean` does not remove `uv.lock` — run `uv sync --upgrade` to update it.
