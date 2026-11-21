"""Assemble the benchmark add-on: the add-on files, the runner, the address
list and a copy of Bellweather's core, in benchmark/build/bellweather_benchmark,
with the commit it was built from in app/build.json (every raw-data export
names it).

    python benchmark/build_addon.py

Copy that folder to the Home Assistant server's /addons folder, then install
"Bellweather Benchmark" from the local add-ons.
"""

import json
import shutil
import subprocess
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT = HERE / "build" / "bellweather_benchmark"


def main() -> None:
    if OUT.exists():
        shutil.rmtree(OUT)
    OUT.mkdir(parents=True)
    for name in ("config.yaml", "Dockerfile", "README.md"):
        shutil.copy(HERE / "addon" / name, OUT / name)
    app = OUT / "app"
    shutil.copytree(HERE / "app", app, ignore=shutil.ignore_patterns("__pycache__", "core", "data", "points.json"))
    shutil.copytree(HERE.parent / "custom_components" / "bellweather" / "core", app / "core",
                    ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copy(HERE / "points.json", app / "points.json")
    (app / "build.json").write_text(json.dumps(build_info()) + "\n", encoding="utf-8")
    print(OUT)


def build_info() -> dict:
    """The commit, and whether the benchmark or Bellweather's core had
    uncommitted changes (then the commit alone does not describe the code)."""
    root = HERE.parent

    def git(*args: str) -> str:
        return subprocess.run(["git", *args], cwd=root, capture_output=True, text=True, check=True).stdout.strip()

    try:
        commit = git("rev-parse", "HEAD")
        changes = git("status", "--porcelain", "--", "benchmark", "custom_components/bellweather/core")
    except (OSError, subprocess.CalledProcessError):
        return {"commit": None, "clean": None}
    return {"commit": commit, "clean": not changes}


if __name__ == "__main__":
    main()
