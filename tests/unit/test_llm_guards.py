import pytest

from app.config import Settings
from app.llm.client import LLMConfigError, OllamaClient, check_local_only

BASE = dict(LLM_PROVIDER="ollama", LLM_MODEL="gemma4:latest", OLLAMA_HOST="http://localhost:11434",
            LLM_NUM_CTX=8192)


@pytest.mark.parametrize("model", ["gemma4:cloud", "gpt-oss:120b-cloud", "Qwen3-CLOUD"])
def test_refuses_cloud_models(model):
    with pytest.raises(LLMConfigError, match="cloud"):
        check_local_only(model, "http://localhost:11434")


@pytest.mark.parametrize("host", ["https://ollama.com", "http://192.168.1.5:11434",
                                  "http://localhost:8080", "https://localhost:11434"])
def test_refuses_non_localhost_hosts(host):
    with pytest.raises(LLMConfigError, match="host"):
        check_local_only("gemma4:latest", host)


def test_accepts_local_model_and_host():
    check_local_only("gemma4:latest", "http://localhost:11434/")


def test_ollama_client_refuses_cloud_before_connecting():
    s = Settings(_env_file=None, **{**BASE, "LLM_MODEL": "gemma4:31b-cloud"})
    with pytest.raises(LLMConfigError):
        OllamaClient(s)


def test_unit_tests_cannot_build_real_client():
    # The conftest guard replaces ollama.Client; a valid config must still not reach the model.
    with pytest.raises(RuntimeError, match="blocked in unit tests"):
        OllamaClient(Settings(_env_file=None, **BASE))


def test_every_call_overrides_model_defaults():
    s = Settings(_env_file=None, **BASE)
    client = OllamaClient.__new__(OllamaClient)  # skip __init__: no network object needed
    client.settings, client.model = s, s.LLM_MODEL
    kw = client.build_chat_kwargs([{"role": "user", "content": "x"}], {"type": "object"}, 512)
    assert kw["think"] is False
    assert kw["options"] == {"temperature": 0, "seed": 42, "num_ctx": 8192, "num_predict": 512}
    assert kw["format"] == {"type": "object"}
    assert kw["keep_alive"] == "30m"
