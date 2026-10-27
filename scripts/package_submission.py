"""Build code.zip for submission, with a whitelist and secret checks (CLAUDE.md hard rule 4).

    python scripts/package_submission.py          # writes code.zip at the repo root and lists its contents

Includes: code, prompts (in source), config example, README, docs, evaluation/ (+ code/evaluation/ copies), tests, and
the caches/ledgers that make output.csv reproducible offline (data/ocr_cache, llm_ledger, run_log, intent decisions).
Excludes: dataset/, .venv, .git, caches, .env (refused), nested archives (refused), log.txt (uploaded separately as
the chat transcript), the orphaned LLM cache.
Refuses to build if any included file looks like a secret or is an archive.
"""
from __future__ import annotations

import re
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "code.zip"

INCLUDE_FILES = ["README.md", "requirements.txt", ".env.example", ".gitignore", "pytest.ini", "CLAUDE.md",
                 "AGENTS.md", "PROJECT_STATE.md", "output.csv", "data/llm_ledger.jsonl", "data/run_log.jsonl",
                 "data/intent_decisions.json"]
INCLUDE_DIRS = ["app", "code", "scripts", "tests", "docs", "evaluation", "data/ocr_cache", "ui", "deploy"]
EXCLUDE_PARTS = {"__pycache__", ".pytest_cache", ".venv", ".venv-ui", "venv", ".git", "node_modules", "dataset", "llm_cache"}
EXCLUDE_NAMES = {".env", "output_repeat.csv", "log.txt"}
ARCHIVE_EXT = {".zip", ".tar", ".gz", ".tgz", ".7z", ".rar", ".bz2", ".xz"}
SECRET_PATTERNS = [re.compile(p) for p in (
    r"hf_[A-Za-z0-9]{30,}",                 # Hugging Face token
    r"AIza[0-9A-Za-z_\-]{30,}",             # Google API key
    r"sk-[A-Za-z0-9]{20,}",                 # OpenAI-style key
    r"ghp_[A-Za-z0-9]{30,}",                # GitHub token
    r"-----BEGIN (RSA |EC )?PRIVATE KEY-----",
)]
REQUIRED = ["README.md", "code/main.py", "code/evaluation/evaluation_report.md", "code/evaluation/usage_report.md",
            "evaluation/evaluation_report.md", "evaluation/usage_report.md", "requirements.txt"]


class Refused(SystemExit):
    pass


def collect() -> list[Path]:
    files = [ROOT / f for f in INCLUDE_FILES if (ROOT / f).is_file()]
    for d in INCLUDE_DIRS:
        base = ROOT / d
        if base.is_dir():
            files += [p for p in sorted(base.rglob("*")) if p.is_file()]
    keep = []
    for p in files:
        rel = p.relative_to(ROOT)
        if set(rel.parts) & EXCLUDE_PARTS or p.name in EXCLUDE_NAMES or p.suffix in (".pyc", ".tmp"):
            continue
        keep.append(p)
    return sorted(set(keep))


def check(files: list[Path]) -> None:
    problems = []
    for p in files:
        rel = p.relative_to(ROOT).as_posix()
        if p.name == ".env" or (p.name.startswith(".env") and p.name != ".env.example"):
            problems.append(f"{rel}: secrets file")
        if p.suffix.lower() in ARCHIVE_EXT:
            problems.append(f"{rel}: nested archive")
        try:
            text = p.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue  # binary files (none expected) are not scanned for text secrets
        for pat in SECRET_PATTERNS:
            if pat.search(text):
                problems.append(f"{rel}: matches secret pattern {pat.pattern[:20]}")
    present = {p.relative_to(ROOT).as_posix() for p in files}
    problems += [f"missing required file {r}" for r in REQUIRED if r not in present]
    if problems:
        print("REFUSED to build code.zip:")
        for x in problems:
            print("  ", x)
        raise Refused(1)


def main() -> None:
    files = collect()
    check(files)
    with zipfile.ZipFile(OUT, "w", compression=zipfile.ZIP_DEFLATED) as z:
        for p in files:
            z.write(p, p.relative_to(ROOT).as_posix())
    size = OUT.stat().st_size
    by_top: dict[str, int] = {}
    for p in files:
        top = p.relative_to(ROOT).parts[0]
        by_top[top] = by_top.get(top, 0) + 1
    print(f"wrote {OUT.name}: {len(files)} files, {size / 1024:.0f} KiB")
    for top, n in sorted(by_top.items()):
        print(f"  {top:24s} {n:4d} files")
    with zipfile.ZipFile(OUT) as z:
        names = z.namelist()
    assert not any(n == ".env" or n.startswith("dataset/") or n.startswith(".venv/") for n in names)
    print("checks: no .env, no dataset/, no .venv/, no nested archives, no secret patterns; required reports present")


if __name__ == "__main__":
    main()
