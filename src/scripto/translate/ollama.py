"""Ollama client: request management done right (docs/PLAN.md §2).

The my-transcriptor lesson this module exists for: batched translations were
silently blowing past Ollama's default num_ctx (4096), the context window
slid, markers fell off the front, and every batch "failed" into expensive
split-retries. Here every request sets num_ctx explicitly, sized from the
prompt.

Also covered: streaming reads (stop between chunks), timeouts, keep_alive
control (batch-lifetime residency vs immediate unload), and the model
management surface (list / pull with progress / delete) for the GUI.
"""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request
from typing import Callable, Iterator

from ..core.errors import OperationStopped, ScriptoError

logger = logging.getLogger(__name__)

DEFAULT_URL = "http://localhost:11434"
DEFAULT_TIMEOUT = 300.0
MIN_NUM_CTX = 8192
MAX_NUM_CTX = 32768

StopCheck = Callable[[], bool]


def required_num_ctx(prompt: str) -> int:
    """Context window big enough for prompt + translated output, with margin.

    Rough token estimate: ~3 chars/token for mixed EN text; output of a
    translation is comparable to input, so budget 2.5× prompt tokens + slack,
    then clamp to [8192, 32768] rounded up to 1024.
    """
    prompt_tokens = len(prompt) / 3
    required = int(prompt_tokens * 2.5) + 512
    clamped = max(MIN_NUM_CTX, min(MAX_NUM_CTX, required))
    return ((clamped + 1023) // 1024) * 1024


class OllamaClient:
    def __init__(self, url: str = DEFAULT_URL, timeout: float = DEFAULT_TIMEOUT):
        self.url = url.rstrip("/")
        self.timeout = timeout

    # ------------------------------------------------------------------ #
    # Generation
    # ------------------------------------------------------------------ #

    def generate(
        self,
        prompt: str,
        *,
        model: str,
        keep_alive: str | int = "10m",
        stop_check: StopCheck | None = None,
    ) -> str:
        """Streamed /api/generate; honors stop between chunks."""
        payload = {
            "model": model,
            "prompt": prompt,
            "stream": True,
            "keep_alive": keep_alive,
            "options": {
                "temperature": 0.2,
                "num_ctx": required_num_ctx(prompt),
            },
        }
        try:
            # think:false silences reasoning models (qwen3); servers/models
            # that reject the field get one retry without it.
            return self._generate_stream({**payload, "think": False}, stop_check)
        except urllib.error.HTTPError as exc:
            if exc.code == 400:
                return self._generate_stream(payload, stop_check)
            raise self._wrap_http_error(exc)
        except OperationStopped:
            raise
        except urllib.error.URLError as exc:
            raise ScriptoError(
                f"Ollama not reachable at {self.url}: {exc.reason}",
                key="errors.ollama_unreachable",
                url=self.url,
            ) from None

    def _generate_stream(self, payload: dict, stop_check: StopCheck | None) -> str:
        chunks: list[str] = []
        for data in self._post_stream("/api/generate", payload):
            if stop_check is not None and stop_check():
                raise OperationStopped()
            piece = data.get("response")
            if piece:
                chunks.append(piece)
            if data.get("error"):
                raise ScriptoError(f"Ollama error: {data['error']}")
        return "".join(chunks)

    def unload(self, model: str) -> None:
        """Ask Ollama to evict the model now (keep_alive=0) — memory hygiene."""
        try:
            self._post_json(
                "/api/generate",
                {"model": model, "prompt": "", "stream": False, "keep_alive": 0},
            )
        except Exception:
            logger.debug("could not unload ollama model %s", model, exc_info=True)

    # ------------------------------------------------------------------ #
    # Model management (GUI surface)
    # ------------------------------------------------------------------ #

    def is_reachable(self) -> bool:
        try:
            self.list_models(timeout=3.0)
            return True
        except Exception:
            return False

    def list_models(self, timeout: float | None = None) -> list[str]:
        req = urllib.request.Request(self.url + "/api/tags")
        with urllib.request.urlopen(req, timeout=timeout or self.timeout) as resp:
            body = json.loads(resp.read().decode("utf-8"))
        names = [m.get("name") for m in body.get("models", []) if m.get("name")]
        return sorted(set(names))

    def pull(
        self,
        model: str,
        *,
        progress: Callable[[str, float | None], None] | None = None,
        stop_check: StopCheck | None = None,
    ) -> None:
        """Pull a model; progress gets (detail, fraction 0..1 or None)."""
        for data in self._post_stream(
            "/api/pull", {"name": model, "stream": True}, timeout=3600.0
        ):
            if stop_check is not None and stop_check():
                raise OperationStopped()
            if data.get("error"):
                raise ScriptoError(f"Ollama pull failed: {data['error']}")
            if progress is not None:
                total = int(data.get("total") or 0)
                completed = int(data.get("completed") or 0)
                if total > 0:
                    progress(data.get("status", ""), min(1.0, completed / total))
                else:
                    progress(data.get("status", ""), None)

    def delete(self, model: str) -> None:
        req = urllib.request.Request(
            self.url + "/api/delete",
            data=json.dumps({"name": model}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="DELETE",
        )
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            resp.read()

    # ------------------------------------------------------------------ #
    # HTTP plumbing (single seam for tests)
    # ------------------------------------------------------------------ #

    def _post_stream(
        self, path: str, payload: dict, timeout: float | None = None
    ) -> Iterator[dict]:
        req = urllib.request.Request(
            self.url + path,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=timeout or self.timeout) as resp:
            for raw in resp:
                line = raw.decode("utf-8").strip()
                if not line:
                    continue
                try:
                    yield json.loads(line)
                except json.JSONDecodeError:
                    continue

    def _post_json(self, path: str, payload: dict) -> dict:
        req = urllib.request.Request(
            self.url + path,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            raw = resp.read()
        return json.loads(raw) if raw else {}

    def _wrap_http_error(self, exc: urllib.error.HTTPError) -> ScriptoError:
        try:
            detail = json.loads(exc.read().decode("utf-8")).get("error", "")
        except Exception:
            detail = ""
        return ScriptoError(
            f"Ollama request failed (HTTP {exc.code}): {detail or exc.reason}",
            key="errors.ollama_http",
            code=exc.code,
            reason=detail or str(exc.reason),
        )
