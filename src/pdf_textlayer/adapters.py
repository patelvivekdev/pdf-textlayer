"""Adapters that translate OCR-source JSON into :class:`ParsedPage` objects.

An adapter is anything that implements the :class:`Adapter` protocol —
typically a small class with a ``parse(data) -> Iterable[ParsedPage]``
method. New adapters can be plugged in at runtime with
:func:`register_adapter`.

Currently bundled:

* ``"liteparse"`` — :class:`LiteparseAdapter`, for JSON produced by
  `liteparse <https://github.com/run-llama/liteparse>`_'s ``--format json``
  output. liteparse tags OCR-derived text items with
  ``fontName == "OCR"``; native-text items keep the real font name and
  are skipped here so the original PDF text stays untouched.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any, Protocol, runtime_checkable

from .core import ParsedPage, TextBox


@runtime_checkable
class Adapter(Protocol):
    """Protocol for OCR-source adapters.

    Implementations take a deserialized JSON document and yield one
    :class:`ParsedPage` per page that carries OCR text. Pages with no OCR
    text may be omitted (or yielded with an empty ``boxes`` list — both
    are treated identically by the core).
    """

    def parse(self, data: Any) -> Iterable[ParsedPage]: ...


class LiteparseAdapter:
    """Adapter for `liteparse <https://github.com/run-llama/liteparse>`_ JSON.

    Expected shape (simplified)::

        {
          "pages": [
            {
              "page": 1,
              "textItems": [
                {
                  "text": "Hello",
                  "x": 10, "y": 20,
                  "width": 60, "height": 14,
                  "fontName": "OCR",
                  "confidence": 0.95
                },
                ...
              ]
            },
            ...
          ]
        }

    Only items with ``fontName == "OCR"`` are emitted — native-text items
    are left to the original PDF.
    """

    OCR_FONT_MARKER = "OCR"

    def parse(self, data: dict[str, Any]) -> Iterable[ParsedPage]:
        for page in data.get("pages", []):
            page_no = page.get("page")
            if not isinstance(page_no, int):
                continue
            boxes = list(self._page_boxes(page))
            if boxes:
                yield ParsedPage(page_number=page_no, boxes=boxes)

    def _page_boxes(self, page: dict[str, Any]) -> Iterable[TextBox]:
        for item in page.get("textItems", []):
            if item.get("fontName") != self.OCR_FONT_MARKER:
                continue
            text = (item.get("text") or "").strip()
            if not text:
                continue
            try:
                width = float(item.get("width") or 0)
                height = float(item.get("height") or 0)
                x = float(item["x"])
                y = float(item["y"])
            except (KeyError, TypeError, ValueError):
                continue
            if width <= 0 or height <= 0:
                continue
            yield TextBox(
                text=text,
                x=x,
                y=y,
                width=width,
                height=height,
                confidence=float(item.get("confidence") or 1.0),
            )


_REGISTRY: dict[str, Adapter] = {
    "liteparse": LiteparseAdapter(),
}


def get_adapter(name: str) -> Adapter:
    """Look up a registered adapter by name.

    Raises:
        KeyError: If ``name`` is not registered.
    """
    try:
        return _REGISTRY[name]
    except KeyError:
        known = ", ".join(sorted(_REGISTRY)) or "(none)"
        raise KeyError(f"Unknown adapter {name!r}. Registered: {known}") from None


def register_adapter(name: str, adapter: Adapter) -> None:
    """Register a custom adapter under ``name``.

    Overwrites any existing entry with the same name.
    """
    _REGISTRY[name] = adapter


def available_adapters() -> list[str]:
    """Return the names of all registered adapters."""
    return sorted(_REGISTRY)
