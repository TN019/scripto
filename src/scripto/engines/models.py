"""Whisper model registry: presets, local detection, download, delete (R1).

Both backends pull from the Hugging Face Hub, so "installed" means "present in
the HF cache". The cache is scanned once per query — never once per model
(a my-transcriptor lesson).
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Callable

from ..core.errors import ScriptoError

logger = logging.getLogger(__name__)

# Silence HF progress bars before any huggingface import happens.
os.environ.setdefault("HF_HUB_DISABLE_PROGRESS_BARS", "1")

ProgressFn = Callable[[int, int], None]  # (done, total) — coarse units


@dataclass(frozen=True)
class WhisperModelSpec:
    key: str          # stable id used in config, e.g. "large-v3-turbo"
    label: str        # short human label
    size_hint: str    # rough download size, for UI
    mlx_repo: str     # Hugging Face repo for the mlx backend
    fw_repo: str      # Hugging Face repo for the faster-whisper backend


PRESETS: list[WhisperModelSpec] = [
    WhisperModelSpec(
        key="tiny",
        label="Tiny (fastest, lowest quality)",
        size_hint="~80 MB",
        mlx_repo="mlx-community/whisper-tiny",
        fw_repo="Systran/faster-whisper-tiny",
    ),
    WhisperModelSpec(
        key="small",
        label="Small (fast)",
        size_hint="~500 MB",
        mlx_repo="mlx-community/whisper-small-mlx",
        fw_repo="Systran/faster-whisper-small",
    ),
    WhisperModelSpec(
        key="large-v3-turbo",
        label="Large v3 Turbo (recommended)",
        size_hint="~1.6 GB",
        mlx_repo="mlx-community/whisper-large-v3-turbo",
        fw_repo="mobiuslabsgmbh/faster-whisper-large-v3-turbo",
    ),
    WhisperModelSpec(
        key="large-v3",
        label="Large v3 (most accurate, slowest)",
        size_hint="~3 GB",
        mlx_repo="mlx-community/whisper-large-v3",
        fw_repo="Systran/faster-whisper-large-v3",
    ),
]

_BY_KEY = {spec.key: spec for spec in PRESETS}


def get_spec(key: str) -> WhisperModelSpec:
    spec = _BY_KEY.get(key)
    if spec is None:
        raise ScriptoError(
            f"Unknown whisper model: {key}", key="errors.unknown_model", model=key
        )
    return spec


def repo_for(spec: WhisperModelSpec, engine_name: str) -> str:
    if engine_name == "mlx":
        return spec.mlx_repo
    if engine_name == "faster-whisper":
        return spec.fw_repo
    raise ScriptoError(
        f"Unknown engine: {engine_name}", key="errors.unknown_engine", engine=engine_name
    )


def _hf_cache_dir() -> str | None:
    """HF cache path from the environment, resolved at call time.

    huggingface_hub freezes HF_HOME/HF_HUB_CACHE into constants on first
    import, so relying on its default would make the effective cache depend
    on import order (and ignore env changes — which is also what lets tests
    point at an empty cache). None = the library default.
    """
    hub = os.environ.get("HF_HUB_CACHE")
    if hub:
        return hub
    home = os.environ.get("HF_HOME")
    return str(Path(home) / "hub") if home else None


def _cached_repo_ids() -> set[str]:
    """One scan of the HF cache; empty set when the cache does not exist."""
    try:
        from huggingface_hub import scan_cache_dir
        from huggingface_hub.errors import CacheNotFound

        try:
            info = scan_cache_dir(_hf_cache_dir())
        except CacheNotFound:
            return set()
        return {repo.repo_id for repo in info.repos}
    except Exception:
        logger.exception("could not scan the Hugging Face cache")
        return set()


def installed_keys(engine_name: str) -> set[str]:
    """Preset keys whose model for ``engine_name`` is already downloaded."""
    cached = _cached_repo_ids()
    return {s.key for s in PRESETS if repo_for(s, engine_name) in cached}


def download_model(
    spec: WhisperModelSpec,
    engine_name: str,
    progress: ProgressFn | None = None,
) -> None:
    """Blocking download into the HF cache; coarse per-file progress."""
    from huggingface_hub import snapshot_download
    from tqdm.auto import tqdm as _tqdm

    class _Progress(_tqdm):  # routes the outer files-bar to our callback
        def update(self, n=1):
            result = super().update(n)
            if progress is not None and self.total:
                progress(int(self.n), int(self.total))
            return result

    repo = repo_for(spec, engine_name)
    logger.info("downloading %s (%s)", repo, spec.size_hint)
    # token=False: anonymous access for public repos; a stale HF_TOKEN env var
    # otherwise surfaces as a misleading 401 (lesson from my-transcriptor).
    snapshot_download(
        repo_id=repo, token=False, tqdm_class=_Progress, cache_dir=_hf_cache_dir()
    )


def delete_model(spec: WhisperModelSpec, engine_name: str) -> bool:
    """Remove the model from the HF cache; True if something was deleted."""
    from huggingface_hub import scan_cache_dir
    from huggingface_hub.errors import CacheNotFound

    repo = repo_for(spec, engine_name)
    try:
        info = scan_cache_dir(_hf_cache_dir())
    except CacheNotFound:
        return False
    hashes = [
        revision.commit_hash
        for cached in info.repos
        if cached.repo_id == repo
        for revision in cached.revisions
    ]
    if not hashes:
        return False
    info.delete_revisions(*hashes).execute()
    logger.info("deleted %s from the HF cache", repo)
    return True
