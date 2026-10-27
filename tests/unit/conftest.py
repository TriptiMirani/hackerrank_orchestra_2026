"""Guard: unit tests must never reach a real model. Any attempt to build or use the real
ollama.Client fails the test loudly instead of silently calling the model."""
import pytest

import app.llm.client as client_mod


class _BlockedOllama:
    def __init__(self, *args, **kwargs):
        raise RuntimeError("Real Ollama client is blocked in unit tests; use FakeLLMClient.")


@pytest.fixture(autouse=True)
def block_real_ollama(monkeypatch):
    monkeypatch.setattr(client_mod.ollama, "Client", _BlockedOllama)
