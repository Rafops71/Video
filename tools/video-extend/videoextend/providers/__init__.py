"""Provider registry.

Adding a new cloud backend means writing a VideoProvider subclass and adding one
line here. Nothing else in the application needs to change.
"""

from __future__ import annotations

from .base import GenerationRequest, ProviderInfo, VideoProvider
from .hf_space import WanI2VFastProvider, WanVaceFastProvider

# Order matters: the first entry is the default.
_REGISTRY: dict[str, type[VideoProvider]] = {
    WanI2VFastProvider._info.key: WanI2VFastProvider,
    WanVaceFastProvider._info.key: WanVaceFastProvider,
}

DEFAULT_PROVIDER = WanI2VFastProvider._info.key


def available() -> list[ProviderInfo]:
    return [cls._info for cls in _REGISTRY.values()]  # type: ignore[attr-defined]


def get_provider(key: str | None = None, token: str | None = None) -> VideoProvider:
    key = key or DEFAULT_PROVIDER
    try:
        cls = _REGISTRY[key]
    except KeyError:
        raise KeyError(
            f"Unknown provider '{key}'. Available: {', '.join(_REGISTRY)}"
        ) from None
    return cls(token=token)  # type: ignore[call-arg]


__all__ = [
    "GenerationRequest", "ProviderInfo", "VideoProvider",
    "available", "get_provider", "DEFAULT_PROVIDER",
]
