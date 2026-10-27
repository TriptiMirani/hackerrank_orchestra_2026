"""Regenerate the evaluation and usage reports from measurements, and place them where the starter layout expects
them (code/evaluation/) as well as in evaluation/.

    python code/evaluation/main.py

Runs: scripts/usage_report.py (from data/llm_ledger.jsonl + data/run_log.jsonl) and scripts/evaluation_report.py
(sample scorer, error analysis, ablations, custom cases, validator on output.csv). Run code/main.py first.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
HERE = Path(__file__).resolve().parent


def main() -> None:
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    for script in ("scripts/usage_report.py", "scripts/evaluation_report.py"):
        subprocess.run([sys.executable, script], cwd=REPO_ROOT, check=True, env=env, stdout=subprocess.DEVNULL)
    for name in ("evaluation_report.md", "usage_report.md"):
        shutil.copyfile(REPO_ROOT / "evaluation" / name, HERE / name)
        print(f"wrote evaluation/{name} and code/evaluation/{name}")


if __name__ == "__main__":
    main()
