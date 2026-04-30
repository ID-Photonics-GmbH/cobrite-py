# Development

All tasks run via `make` or `uv run python devtools/dev.py [task]` (cross-platform equivalent).

## Tasks

| Task | What it does |
|------|-------------|
| *(default)* | `install` then `lint` |
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

## Notes

- `clean` does not remove `uv.lock` — run `uv sync --upgrade` to update it.
