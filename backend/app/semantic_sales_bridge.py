from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from typing import Iterator

from app.sales_models import SalesProfileExtraction
from app.sales_profile_extractor import sales_profile_extractor

_override: ContextVar[SalesProfileExtraction | None] = ContextVar(
    "semantic_sales_extraction_override", default=None
)
_original_extract = sales_profile_extractor.extract
_installed = False


def _extract_with_semantic_override(
    text: str,
    *,
    language: str,
    recent_context: str = "",
) -> SalesProfileExtraction:
    value = _override.get()
    if value is not None:
        return value.model_copy(deep=True)
    return _original_extract(
        text,
        language=language,  # type: ignore[arg-type]
        recent_context=recent_context,
    )


def install_semantic_sales_bridge() -> None:
    global _installed
    if _installed:
        return
    _installed = True
    sales_profile_extractor.extract = _extract_with_semantic_override  # type: ignore[method-assign]


@contextmanager
def use_semantic_sales_extraction(
    extraction: SalesProfileExtraction,
) -> Iterator[None]:
    install_semantic_sales_bridge()
    token = _override.set(extraction.model_copy(deep=True))
    try:
        yield
    finally:
        _override.reset(token)


install_semantic_sales_bridge()
