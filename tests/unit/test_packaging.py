"""package_submission refuses secrets, .env files and nested archives (CLAUDE.md hard rule 4)."""
import pytest

import scripts.package_submission as pkg


def run_check(tmp_path, monkeypatch, name, content):
    monkeypatch.setattr(pkg, "ROOT", tmp_path)
    monkeypatch.setattr(pkg, "REQUIRED", [])
    f = tmp_path / name
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(content, encoding="utf-8")
    pkg.check([f])


@pytest.mark.parametrize("name,content", [
    (".env", "HF_TOKEN=x"),
    ("app/settings.py", "TOKEN = 'hf_" + "A" * 34 + "'"),
    ("docs/nested.zip", "PK"),
])
def test_refuses_secrets_env_and_archives(tmp_path, monkeypatch, name, content):
    with pytest.raises(SystemExit):
        run_check(tmp_path, monkeypatch, name, content)


def test_accepts_clean_file(tmp_path, monkeypatch):
    run_check(tmp_path, monkeypatch, "app/ok.py", "x = 1  # no secrets here\n")
