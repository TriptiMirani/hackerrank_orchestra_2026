"""Upload deploy/hf_space/ to a PRIVATE Hugging Face Space through the HTTP API (never git; CLAUDE.md hard rule 1).

    .venv-ui/Scripts/python.exe scripts/deploy_space.py [--name northstar-deletion-audit] [--sdk static|gradio]

Reads HF_TOKEN from .env without printing it. Creates the Space as private if missing, uploads the folder, adds no
secrets or variables, and prints the Space URL plus its variables list (expected empty).
"""
from __future__ import annotations

import argparse
from pathlib import Path

from huggingface_hub import HfApi

ROOT = Path(__file__).resolve().parent.parent
FOLDERS = {"static": ROOT / "deploy" / "hf_static", "gradio": ROOT / "deploy" / "hf_space"}


def read_token() -> str:
    for line in (ROOT / ".env").read_text(encoding="utf-8").splitlines():
        if line.startswith("HF_TOKEN="):
            token = line.split("=", 1)[1].strip()
            if token:
                return token
    raise SystemExit("HF_TOKEN is empty in .env")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--name", default="northstar-deletion-audit")
    # Gradio Spaces on free CPU now need HF PRO (402); static Spaces are free, so static is the default.
    ap.add_argument("--sdk", choices=sorted(FOLDERS), default="static")
    args = ap.parse_args()
    folder = FOLDERS[args.sdk]
    api = HfApi(token=read_token())
    user = api.whoami()["name"]
    repo_id = f"{user}/{args.name}"
    api.create_repo(repo_id, repo_type="space", space_sdk=args.sdk, private=True, exist_ok=True)
    info = api.repo_info(repo_id, repo_type="space")
    if not info.private:
        raise SystemExit(f"{repo_id} exists and is PUBLIC; refusing to upload (the developer asked for private).")
    api.upload_folder(repo_id=repo_id, repo_type="space", folder_path=str(folder),
                      ignore_patterns=["__pycache__/*", "*.pyc"], commit_message="Deploy case viewer")
    variables = api.get_space_variables(repo_id)
    print(f"uploaded {folder.relative_to(ROOT)} -> https://huggingface.co/spaces/{repo_id} (private={info.private})")
    print(f"space variables: {dict(variables) or 'none'}; secrets: none added by this script")


if __name__ == "__main__":
    main()
