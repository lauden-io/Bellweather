"""Assemble the benchmark add-on: the add-on files, the runner, the address
list and a copy of Bellweather's core, in benchmark/build/bellweather_benchmark.

    python benchmark/build_addon.py

Copy that folder to the Home Assistant server's /addons folder, then install
"Bellweather Benchmark" from the local add-ons.
"""

import shutil
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
    print(OUT)


if __name__ == "__main__":
    main()
