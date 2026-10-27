"""LLM boundary: one interface, an Ollama implementation, a fake for tests, and the
structured-extraction loop (cache -> call -> validate -> retry -> ledger).

Nothing outside this module talks to Ollama. Failures come back as an LLMStatus,
never as an exception, so one bad file cannot kill a 518-case run (hard rule 9).
"""
from __future__ import annotations

import time
from enum import Enum
from typing import Any, Protocol

import ollama
from pydantic import BaseModel, ValidationError

from app.config import Settings
from app.llm.cache import LLMCache, make_cache_key
from app.llm.ledger import Ledger

ALLOWED_HOST = "http://localhost:11434"
MAX_RETRIES = 2  # parse failures: 1 first attempt + 2 retries, then EXTRACTION_FAILED
# A prompt whose evaluated length reaches this share of num_ctx was probably truncated.
OVERFLOW_RATIO = 0.95


class LLMStatus(str, Enum):
    OK = "OK"
    EXTRACTION_FAILED = "EXTRACTION_FAILED"
    CONTEXT_OVERFLOW = "CONTEXT_OVERFLOW"
    UNREACHABLE = "UNREACHABLE"
    TIMEOUT = "TIMEOUT"


class LLMConfigError(ValueError):
    """Raised at construction time for a forbidden model/host. Config bugs should be loud."""


class LLMTransportError(Exception):
    def __init__(self, status: LLMStatus, message: str):
        super().__init__(message)
        self.status = status


class RawChatResult(BaseModel):
    content: str
    prompt_eval_count: int = 0
    eval_count: int = 0
    total_duration_s: float = 0.0
    load_duration_s: float = 0.0
    prompt_eval_duration_s: float = 0.0
    eval_duration_s: float = 0.0
    done_reason: str | None = None


class LLMResult(BaseModel):
    status: LLMStatus
    data: dict[str, Any] | None = None
    error: str | None = None
    attempts: int = 0
    cached: bool = False
    cache_key: str = ""
    prompt_eval_count: int = 0
    eval_count: int = 0
    latency_s: float = 0.0


class LLMClient(Protocol):
    provider: str
    model: str

    def chat(self, messages: list[dict[str, Any]], schema: dict[str, Any], num_predict: int) -> RawChatResult:
        """Return the raw reply, or raise LLMTransportError."""
        ...


def check_local_only(model: str, host: str) -> None:
    if "cloud" in model.lower():
        raise LLMConfigError(f"Refusing model '{model}': cloud models are not allowed (local only).")
    if host.rstrip("/") != ALLOWED_HOST:
        raise LLMConfigError(f"Refusing host '{host}': only {ALLOWED_HOST} is allowed.")


def _ns_to_s(value: int | None) -> float:
    return round((value or 0) / 1e9, 3)


class OllamaClient:
    provider = "ollama"

    def __init__(self, settings: Settings):
        check_local_only(settings.LLM_MODEL, settings.OLLAMA_HOST)
        self.settings = settings
        self.model = settings.LLM_MODEL
        self._client = ollama.Client(host=settings.OLLAMA_HOST, timeout=settings.LLM_TIMEOUT_S)

    def options(self, num_predict: int) -> dict[str, Any]:
        # gemma4 ships with temperature 1; override every call for reproducibility.
        return {
            "temperature": 0,
            "seed": self.settings.LLM_SEED,
            "num_ctx": self.settings.LLM_NUM_CTX,
            "num_predict": num_predict,
        }

    def build_chat_kwargs(self, messages, schema, num_predict) -> dict[str, Any]:
        return {
            "model": self.model,
            "messages": messages,
            "format": schema,
            "options": self.options(num_predict),
            "think": False,  # thinking tokens are slow, unreproducible, and not needed here
            "keep_alive": self.settings.LLM_KEEP_ALIVE,
            "stream": False,
        }

    def chat(self, messages, schema, num_predict) -> RawChatResult:
        try:
            resp = self._client.chat(**self.build_chat_kwargs(messages, schema, num_predict))
        except ollama.ResponseError as exc:
            raise LLMTransportError(LLMStatus.UNREACHABLE, f"ollama error: {exc}") from exc
        except Exception as exc:  # httpx connect/timeout errors surface here
            name = type(exc).__name__.lower()
            status = LLMStatus.TIMEOUT if "timeout" in name else LLMStatus.UNREACHABLE
            raise LLMTransportError(status, f"{type(exc).__name__}: {exc}") from exc
        return RawChatResult(
            content=resp.message.content or "",
            prompt_eval_count=resp.prompt_eval_count or 0,
            eval_count=resp.eval_count or 0,
            total_duration_s=_ns_to_s(resp.total_duration),
            load_duration_s=_ns_to_s(resp.load_duration),
            prompt_eval_duration_s=_ns_to_s(resp.prompt_eval_duration),
            eval_duration_s=_ns_to_s(resp.eval_duration),
            done_reason=resp.done_reason,
        )


class FakeLLMClient:
    """Scripted client for unit tests. Each item is a reply string or an LLMTransportError."""

    provider = "fake"

    def __init__(self, replies: list[str | LLMTransportError], model: str = "fake-model",
                 prompt_eval_count: int = 100, eval_count: int = 20):
        self.model = model
        self.replies = list(replies)
        self.calls: list[list[dict[str, Any]]] = []
        self.prompt_eval_count = prompt_eval_count
        self.eval_count = eval_count

    def chat(self, messages, schema, num_predict) -> RawChatResult:
        self.calls.append([dict(m) for m in messages])
        reply = self.replies.pop(0)
        if isinstance(reply, LLMTransportError):
            raise reply
        return RawChatResult(content=reply, prompt_eval_count=self.prompt_eval_count,
                             eval_count=self.eval_count, total_duration_s=0.01, done_reason="stop")


class StructuredExtractor:
    """Cache-first structured calls. One instance per run; shares one ledger."""

    def __init__(self, client: LLMClient, cache: LLMCache, ledger: Ledger, num_ctx: int,
                 cache_options: dict[str, Any] | None = None):
        self.client = client
        self.cache = cache
        self.ledger = ledger
        self.num_ctx = num_ctx
        # Anything that changes the output (seed, num_ctx, image size...) belongs in the key.
        self.cache_options = cache_options or {}

    def extract(
        self,
        schema_model: type[BaseModel],
        system: str,
        prompt: str,
        images: list[bytes],
        input_hashes: list[str],
        purpose: str,
        file_id: str,
        prompt_version: str,
        num_predict: int,
        fresh: bool = False,
    ) -> LLMResult:
        key = make_cache_key(input_hashes, self.client.model, prompt_version,
                             {**self.cache_options, "num_predict": num_predict}, schema_model.__name__)
        log = dict(provider=self.client.provider, model=self.client.model, purpose=purpose,
                   file_id=file_id, cache_key=key)

        if not fresh:
            hit = self.cache.get(key)
            if hit is not None:
                self.ledger.append(status="CACHE_HIT", attempt=0, cached=True, **log)
                return LLMResult(status=LLMStatus.OK, data=hit["data"], attempts=0, cached=True, cache_key=key)

        schema = schema_model.model_json_schema()
        user_msg: dict[str, Any] = {"role": "user", "content": prompt}
        if images:
            user_msg["images"] = images
        messages: list[dict[str, Any]] = [{"role": "system", "content": system}, user_msg]

        total_in = total_out = 0
        total_latency = 0.0
        last_error = ""
        for attempt in range(1, MAX_RETRIES + 2):
            started = time.monotonic()
            try:
                raw = self.client.chat(messages, schema, num_predict)
            except LLMTransportError as exc:
                latency = round(time.monotonic() - started, 3)
                self.ledger.append(status=exc.status.value, attempt=attempt, cached=False,
                                   latency_s=latency, error=str(exc), **log)
                return LLMResult(status=exc.status, error=str(exc), attempts=attempt, cache_key=key,
                                 prompt_eval_count=total_in, eval_count=total_out,
                                 latency_s=total_latency + latency)
            latency = round(time.monotonic() - started, 3)
            total_in += raw.prompt_eval_count
            total_out += raw.eval_count
            total_latency += latency
            timing = dict(prompt_eval_count=raw.prompt_eval_count, eval_count=raw.eval_count,
                          latency_s=latency, load_duration_s=raw.load_duration_s,
                          prompt_eval_duration_s=raw.prompt_eval_duration_s,
                          eval_duration_s=raw.eval_duration_s, done_reason=raw.done_reason)

            # Never answer from a cut-off prompt: Ollama truncates silently at num_ctx.
            if raw.prompt_eval_count >= OVERFLOW_RATIO * self.num_ctx:
                err = f"prompt_eval_count {raw.prompt_eval_count} >= {OVERFLOW_RATIO:.0%} of num_ctx {self.num_ctx}"
                self.ledger.append(status=LLMStatus.CONTEXT_OVERFLOW.value, attempt=attempt, cached=False,
                                   error=err, **timing, **log)
                return LLMResult(status=LLMStatus.CONTEXT_OVERFLOW, error=err, attempts=attempt,
                                 cache_key=key, prompt_eval_count=total_in, eval_count=total_out,
                                 latency_s=total_latency)

            try:
                parsed = schema_model.model_validate_json(raw.content)
            except ValidationError as exc:
                last_error = _short_error(exc, raw.done_reason)
                self.ledger.append(status="PARSE_ERROR", attempt=attempt, cached=False,
                                   error=last_error, **timing, **log)
                # At temperature 0 an identical prompt gives an identical reply, so a plain
                # retry is pointless. Feed the error back so the next prompt differs.
                messages = messages + [
                    {"role": "assistant", "content": raw.content[:4000]},
                    {"role": "user", "content": "Your previous reply was not valid for the schema: "
                     f"{last_error}. Reply again with only a JSON object matching the schema."},
                ]
                continue

            data = parsed.model_dump(mode="json")
            self.ledger.append(status="OK", attempt=attempt, cached=False, **timing, **log)
            self.cache.put(key, {"data": data, "model": self.client.model, "prompt_version": prompt_version,
                                 "purpose": purpose, "file_id": file_id, "schema": schema_model.__name__,
                                 "prompt_eval_count": total_in, "eval_count": total_out,
                                 "attempts": attempt})
            return LLMResult(status=LLMStatus.OK, data=data, attempts=attempt, cache_key=key,
                             prompt_eval_count=total_in, eval_count=total_out, latency_s=total_latency)

        # Bounded retries exhausted: a structured failure, never a silent drop.
        self.ledger.append(status=LLMStatus.EXTRACTION_FAILED.value, attempt=MAX_RETRIES + 1,
                           cached=False, summary=True, error=last_error, **log)
        return LLMResult(status=LLMStatus.EXTRACTION_FAILED, error=last_error, attempts=MAX_RETRIES + 1,
                         cache_key=key, prompt_eval_count=total_in, eval_count=total_out,
                         latency_s=total_latency)


def _short_error(exc: ValidationError, done_reason: str | None) -> str:
    first = exc.errors()[0] if exc.errors() else {"msg": str(exc)}
    msg = f"{first.get('type', 'error')} at {'.'.join(str(p) for p in first.get('loc', ()))}: {first.get('msg')}"
    if done_reason == "length":
        msg += " (output hit num_predict and was cut off; be more concise)"
    return msg[:300]


def build_extractor(settings: Settings, ledger: Ledger, cache: LLMCache) -> StructuredExtractor:
    """Factory for the real pipeline. Provider selection lives here so a hosted model
    can be added later through .env only."""
    if settings.LLM_PROVIDER != "ollama":
        raise LLMConfigError(f"Unsupported LLM_PROVIDER '{settings.LLM_PROVIDER}'.")
    client = OllamaClient(settings)
    return StructuredExtractor(
        client, cache, ledger, num_ctx=settings.LLM_NUM_CTX,
        cache_options={"temperature": 0, "seed": settings.LLM_SEED, "num_ctx": settings.LLM_NUM_CTX,
                       "max_image_side": settings.MAX_IMAGE_SIDE, "pdf_dpi": settings.PDF_DPI},
    )
