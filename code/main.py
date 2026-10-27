"""Entry point (the brief's suggested `code/main.py`): run the deletion audit over all 518 requests.

    python code/main.py            # writes output.csv at the repository root (offline, deterministic, ~10 s)

Equivalent to `python -m app.pipeline.run_all`. See README.md for setup.
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from app.pipeline.run_all import main  # noqa: E402

if __name__ == "__main__":
    main()
