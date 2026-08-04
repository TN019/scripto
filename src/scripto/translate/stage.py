"""OllamaTranslateStage: the pipeline's TranslateStage implementation.

Batch policy (docs/PLAN.md §5): ~40 blocks / 3000 chars per request, sized to
stay comfortably inside the explicit num_ctx. A failed batch splits in half
and retries; a single failing block falls back to one-block prompts and, as
the last resort, keeps the original text — a translation problem never breaks
the subtitle file. Stop is honored at batch boundaries.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Callable

from ..core.errors import OperationStopped, ScriptoError
from ..core.languages import get_language
from . import srt
from .ollama import OllamaClient

logger = logging.getLogger(__name__)

StopCheck = Callable[[], bool]
ProgressFn = Callable[[int, int], None]


class BatchAlignmentError(Exception):
    """A batched response could not be aligned back to its input blocks."""


class OllamaTranslateStage:
    def __init__(
        self,
        client: OllamaClient,
        *,
        model: str,
        target: str,
        overwrite: bool = False,
        batch_blocks: int = srt.DEFAULT_BATCH_BLOCKS,
        batch_max_chars: int = srt.DEFAULT_BATCH_MAX_CHARS,
        keep_alive: str | int = "10m",
    ):
        self._client = client
        self._model = model
        self._target = get_language(target)
        self._overwrite = overwrite
        self._batch_blocks = batch_blocks
        self._batch_max_chars = batch_max_chars
        self._keep_alive = keep_alive
        self.label = f"ollama/{model}"
        self.target_code = self._target.code

    # Pipeline protocol ------------------------------------------------- #

    def translate(
        self,
        srt_path: Path,
        source: Path,
        *,
        stop_check: StopCheck | None = None,
        progress: ProgressFn | None = None,
    ) -> list[Path]:
        out_path = source.with_name(f"{source.stem}{self._target.suffix}.srt")
        if out_path == srt_path:
            # transcript already carries the target language suffix — nothing to do
            return []
        if not self._overwrite:
            # Accept alias-named files (lecture.cn.srt dropped in by hand or
            # by another tool) as the existing translation — never redo it.
            for suffix in (self._target.suffix, *self._target.aliases):
                candidate = source.with_name(f"{source.stem}{suffix}.srt")
                if candidate != srt_path and candidate.exists():
                    return [candidate]

        content = srt_path.read_text(encoding="utf-8")
        translated = self.translate_content(content, stop_check=stop_check, progress=progress)
        out_path.write_text(translated, encoding="utf-8")
        return [out_path]

    def release(self) -> None:
        """Evict the model from Ollama (called by the pipeline when done)."""
        self._client.unload(self._model)

    # Core -------------------------------------------------------------- #

    def translate_content(
        self,
        content: str,
        *,
        stop_check: StopCheck | None = None,
        progress: ProgressFn | None = None,
    ) -> str:
        blocks = srt.parse_srt(content)
        total = len(blocks)
        if total == 0:
            return content

        texts = [b.text for b in blocks]
        translated: list[str] = [""] * total

        for start, end in srt.batch_ranges(texts, self._batch_blocks, self._batch_max_chars):
            if stop_check is not None and stop_check():
                raise OperationStopped()
            results = self._translate_span(texts[start:end], stop_check)
            for offset, value in enumerate(results):
                index = start + offset
                translated[index] = value or texts[index]
            if progress is not None:
                progress(end, total)

        for i, block in enumerate(blocks):
            block.text = translated[i]
        return srt.build_srt(blocks)

    def _translate_span(
        self, chunk: list[str], stop_check: StopCheck | None
    ) -> list[str]:
        """Translate a span; halve on failure, single-block fallback at size 1."""
        if stop_check is not None and stop_check():
            raise OperationStopped()
        n = len(chunk)
        if n == 0:
            return []

        if n == 1:
            return [self._translate_single(chunk[0], stop_check)]

        try:
            return self._translate_marked(chunk, stop_check)
        except (BatchAlignmentError, ScriptoError) as exc:
            half = (n + 1) // 2
            logger.info("batch of %d failed (%s); splitting into %d + %d",
                        n, type(exc).__name__, half, n - half)
            left = self._translate_span(chunk[:half], stop_check)
            right = self._translate_span(chunk[half:], stop_check)
            return left + right

    def _translate_marked(
        self, chunk: list[str], stop_check: StopCheck | None
    ) -> list[str]:
        prompt = srt.build_marker_prompt(chunk, self._target.prompt_name)
        response = self._client.generate(
            prompt, model=self._model, keep_alive=self._keep_alive, stop_check=stop_check
        )
        parsed = srt.parse_marker_response(response, len(chunk))
        if any(item is None for item in parsed):
            raise BatchAlignmentError(f"{parsed.count(None)}/{len(chunk)} markers missing")
        return [item or "" for item in parsed]

    def _translate_single(self, text: str, stop_check: StopCheck | None) -> str:
        if not text.strip():
            return text
        try:
            response = self._client.generate(
                srt.build_single_prompt(text, self._target.prompt_name),
                model=self._model,
                keep_alive=self._keep_alive,
                stop_check=stop_check,
            )
            return srt.strip_think(response).strip() or text
        except OperationStopped:
            raise
        except Exception as exc:
            # Last resort: keep the original text — never break the file.
            logger.warning("single-block translation failed, keeping original: %s", exc)
            return text
