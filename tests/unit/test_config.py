import pytest
from pydantic import ValidationError

from app.config import Settings

BASE = dict(LLM_PROVIDER="ollama", LLM_MODEL="gemma4:latest", OLLAMA_HOST="http://localhost:11434",
            LLM_NUM_CTX=8192)


def test_settings_load_from_values():
    s = Settings(_env_file=None, **BASE)
    assert s.LLM_NUM_CTX == 8192
    assert s.LLM_SEED == 42


@pytest.mark.parametrize("missing", list(BASE))
def test_missing_required_key_fails_fast(missing, monkeypatch):
    monkeypatch.delenv(missing, raising=False)
    values = {k: v for k, v in BASE.items() if k != missing}
    with pytest.raises(ValidationError):
        Settings(_env_file=None, **values)


def test_hf_token_never_in_repr():
    s = Settings(_env_file=None, HF_TOKEN="hf_supersecretvalue", **BASE)
    assert "supersecret" not in repr(s)
    assert "supersecret" not in str(s.model_dump())
