"""Stage the Claude Code trace for this challenge into ~/Downloads/agent-trace/ and zip it (agent_trace_instructions.md).

    .venv/Scripts/python.exe scripts/stage_agent_trace.py

Run it AFTER `/export` and AFTER quitting Claude Code, so the session JSONL is complete.
- Copies only this project's session files (main JSONL + its subagents/ and tool-results/ folder), keeping the
  original layout. It does not copy memory/, settings, credentials or other projects.
- Redacts, in the copies only: the literal HF_TOKEN value from .env, any token-shaped string (HF/Google/OpenAI/GitHub
  keys, private keys), and the account email address. The originals are never modified.
- Keeps any other files already in agent-trace/claude-code/ (e.g. the /export transcript you saved there).
- Writes ~/Downloads/agent-trace.zip, then re-opens it and verifies that no secret pattern remains.
"""
from __future__ import annotations

import os
import re
import shutil
import zipfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
PROJECT_DIR = Path(os.environ["USERPROFILE"]) / ".claude" / "projects" / "C--Users-K8IN-Downloads-hackerrank-orchestrate-october26"
STAGE = Path(os.environ["USERPROFILE"]) / "Downloads" / "agent-trace"
DEST = STAGE / "claude-code"
ZIP = STAGE.parent / "agent-trace.zip"
ACCOUNT_EMAILS = ["tripti.mirani2@gmail.com"]
SECRET_RES = [re.compile(p) for p in (r"hf_[A-Za-z0-9]{30,}", r"AIza[0-9A-Za-z_\-]{30,}", r"sk-[A-Za-z0-9]{20,}",
                                      r"ghp_[A-Za-z0-9]{30,}", r"-----BEGIN (?:RSA |EC )?PRIVATE KEY-----")]


def env_token() -> str | None:
    env = REPO / ".env"
    if env.exists():
        for line in env.read_text(encoding="utf-8").splitlines():
            if line.startswith("HF_TOKEN=") and line.split("=", 1)[1].strip():
                return line.split("=", 1)[1].strip()
    return None


def redact(text: str, token: str | None, counts: dict) -> str:
    if token and token in text:
        counts["hf_token_literal"] += text.count(token)
        text = text.replace(token, "[REDACTED_HF_TOKEN]")
    for rx in SECRET_RES:
        text, n = rx.subn("[REDACTED_SECRET]", text)
        counts["secret_pattern"] += n
    for email in ACCOUNT_EMAILS:
        for variant in (email, email.upper()):
            counts["account_email"] += text.count(variant)
            text = text.replace(variant, "[REDACTED_EMAIL]")
    return text


def session_files() -> list[Path]:
    files = sorted(PROJECT_DIR.glob("*.jsonl"))
    for jsonl in list(files):
        folder = PROJECT_DIR / jsonl.stem          # <session-id>/ holds subagents/ and tool-results/
        if folder.is_dir():
            files += sorted(p for p in folder.rglob("*") if p.is_file())
    return files


def main() -> None:
    token = env_token()
    counts = {"hf_token_literal": 0, "secret_pattern": 0, "account_email": 0}
    DEST.mkdir(parents=True, exist_ok=True)
    copied = []
    for src in session_files():
        rel = src.relative_to(PROJECT_DIR)
        out = DEST / rel
        out.parent.mkdir(parents=True, exist_ok=True)
        try:
            out.write_text(redact(src.read_text(encoding="utf-8"), token, counts), encoding="utf-8")
        except UnicodeDecodeError:
            shutil.copyfile(src, out)              # binary files are copied as-is (none expected)
        copied.append(rel.as_posix())
    # Redact any other text files already staged (e.g. the /export transcript saved by the user).
    for extra in DEST.rglob("*"):
        if extra.is_file() and extra.relative_to(DEST).as_posix() not in copied:
            try:
                extra.write_text(redact(extra.read_text(encoding="utf-8"), token, counts), encoding="utf-8")
            except UnicodeDecodeError:
                pass
    if ZIP.exists():
        ZIP.unlink()
    with zipfile.ZipFile(ZIP, "w", compression=zipfile.ZIP_DEFLATED) as z:
        for p in sorted(STAGE.rglob("*")):
            if p.is_file():
                z.write(p, p.relative_to(STAGE.parent).as_posix())
    with zipfile.ZipFile(ZIP) as z:
        names = z.namelist()
        leftovers = []
        for n in names:
            data = z.read(n).decode("utf-8", errors="ignore")
            if any(rx.search(data) for rx in SECRET_RES) or (token and token in data) or \
                    any(e in data for e in ACCOUNT_EMAILS):
                leftovers.append(n)
    print(f"staged {len(copied)} session files into {DEST}")
    for rel in copied:
        print("  ", rel)
    others = [n for n in names if not n.startswith("agent-trace/claude-code/32dab08c")]
    print(f"other staged files (e.g. /export): {others or 'none yet'}")
    print(f"redactions: {counts}")
    print(f"wrote {ZIP} ({ZIP.stat().st_size / 1e6:.1f} MB, {len(names)} entries); "
          f"secret/email leftovers: {leftovers or 'none'}")


if __name__ == "__main__":
    main()
