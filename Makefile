# Makefile for easy development workflows.
# See docs/development.md for docs.
# Note GitHub Actions call uv directly, not this Makefile.

.DEFAULT_GOAL := default

.PHONY: default install lint test coverage upgrade build docs docs-serve clean

default: install lint coverage

install:
	uv sync --all-extras

lint:
	uv run python devtools/lint.py

test:
	uv run python -m pytest

coverage:
	uv run python -m pytest --cov-report=html

upgrade:
	uv sync --upgrade --all-extras --dev

build:
	uv build

docs:
	uv run zensical build && uv run python -m pytest --cov-report=html:site/coverage --no-cov-on-fail

docs-serve:
	uv run python -m pytest --cov-report=html:docs/coverage --no-cov-on-fail && uv run zensical serve

clean:
	-rm -rf dist/
	-rm -rf *.egg-info/
	-rm -rf .mypy_cache/
	-rm -rf .venv/
	-rm -rf site/
	-rm -rf htmlcov/
	-rm -rf docs/coverage/
	-find . -type d -name "__pycache__" -exec rm -rf {} +
