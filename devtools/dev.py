"""Development task runner — replaces `make` on Windows.

Usage:
    uv run python devtools/dev.py [task]

Tasks:
    install      uv sync --all-extras
    lint         codespell + ruff + basedpyright
    test         pytest with coverage
    coverage     pytest + open HTML coverage report in browser
    upgrade      uv sync --upgrade --all-extras --dev
    build        uv build
    docs         mkdocs build  (outputs to site/)
    docs-serve   mkdocs serve  (live preview at http://127.0.0.1:8000)
    clean        remove build/cache artefacts
    default      install + lint (no argument)
"""

import shutil
import subprocess
import sys
from pathlib import Path

from funlog import log_calls
from rich import get_console, reconfigure
from rich import print as rprint

ROOT = Path(__file__).parent.parent
SRC_PATHS = ["src", "devtools"]
DOC_PATHS = ["README.md"]

reconfigure(emoji=not get_console().options.legacy_windows)


def main() -> int:
    task = sys.argv[1] if len(sys.argv) > 1 else "default"

    tasks: dict[str, list[str] | None] = {
        "install": None,
        "lint": None,
        "test": None,
        "coverage": None,
        "upgrade": None,
        "build": None,
        "docs": None,
        "docs-serve": None,
        "clean": None,
        "default": None,
    }

    if task not in tasks:
        rprint(f"[bold red]Unknown task: {task!r}[/bold red]")
        rprint(f"Available tasks: {', '.join(tasks)}")
        return 1

    rprint()

    if task in ("default", "install"):
        rc = run(["uv", "sync", "--all-extras"])
        if task == "install" or rc:
            return rc

    if task in ("default", "lint"):
        rc = run(["uv", "run", "python", "devtools/lint.py"])
        if task == "lint" or rc:
            return rc

    if task in ("default", "test"):
        return run(["uv", "run", "python", "-m", "pytest"])

    if task in ("coverage"):
        rc = run(["uv", "run", "python", "-m", "pytest", "--cov-report=html"])
        if rc:
            return rc

    if task == "upgrade":
        return run(["uv", "sync", "--upgrade", "--all-extras", "--dev"])

    if task == "build":
        return run(["uv", "build"])

    if task == "docs":
        rc = run(["uv", "run", "zensical", "build"])
        if rc:
            return rc

    if task == "docs-serve":
        return run(["uv", "run", "zensical", "serve"])

    if task == "clean":
        return _clean()

    return 0


@log_calls(level="warning", show_timing_only=True)
def run(cmd: list[str]) -> int:
    rprint()
    rprint(f"[bold green]>> {' '.join(cmd)}[/bold green]")
    try:
        subprocess.run(cmd, text=True, check=True)
        return 0
    except KeyboardInterrupt:
        rprint("[yellow]Keyboard interrupt — cancelled[/yellow]")
        return 1
    except subprocess.CalledProcessError as e:
        rprint(f"[bold red]Error: {e}[/bold red]")
        return 1


def _clean() -> int:
    targets = [
        ROOT / "dist",
        ROOT / "site",
        ROOT / "htmlcov",
        ROOT / "docs" / "coverage",
        ROOT / ".mypy_cache",
        ROOT / ".venv",
        *ROOT.rglob("__pycache__"),
        *ROOT.rglob("*.egg-info"),
    ]
    for path in targets:
        if path.exists():
            rprint(f"[dim]rm {path.relative_to(ROOT)}[/dim]")
            if path.is_dir():
                shutil.rmtree(path)
            else:
                path.unlink()
    rprint("[green]Clean done.[/green]")
    return 0


if __name__ == "__main__":
    sys.exit(main())
