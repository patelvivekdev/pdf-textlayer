"""Adapters that translate OCR-source JSON into :class:`ParsedPage` objects.

An adapter is anything that implements the :class:`Adapter` protocol —
typically a small class with a ``parse(data) -> Iterable[ParsedPage]``
method. New adapters can be plugged in at runtime with
:func:`register_adapter`.

Currently bundled:

* ``"liteparse"`` — :class:`LiteparseAdapter`, for JSON produced by
  `liteparse <https://github.com/run-llama/liteparse>`_ ``--format json``
  (the ``lit parse`` CLI). OCR items are tagged ``font_name == "OCR"``.
  Native-text items keep the real font name and are skipped so the
  original PDF text stays untouched.
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


def _first_present(item: dict[str, Any], *keys: str) -> Any:
    """First present, non-null value among ``keys``."""
    for key in keys:
        if key in item and item[key] is not None:
            return item[key]

    return None


class LiteparseAdapter:
    """Adapter for `liteparse <https://github.com/run-llama/liteparse>`_ JSON.

    Current ``--format json`` shape (liteparse 2.17)::

        {
          "total_pages": 1,
          "pages": [
            {
              "page": 1,
              "width": 612,
              "height": 792,
              "text": "Hello",
              "text_items": [
                {
                  "text": "Hello",
                  "x": 72.0,
                  "y": 100.0,
                  "width": 40.0,
                  "height": 12.0,
                  "font_name": "OCR",
                  "font_size": 12.0,
                  "confidence": 0.98,
                  "rotation": 0.0
                }
              ]
            }
          ]
        }

    ``rotation`` is emitted only when the parse used
    ``--extract-text-metadata``. Missing ``rotation`` means horizontal text.

    ``textItems`` / ``fontName`` are still accepted so JSON written against
    the 0.1 field names keeps working.

    Only items with ``font_name == "OCR"`` are emitted — native-text items
    are left to the original PDF. Extra keys (font metrics, colors, blocks,
    annotations) are ignored.
    """

    OCR_FONT_MARKER = "OCR"

    def parse(self, data: dict[str, Any]) -> Iterable[ParsedPage]:
        for page in data.get("pages") or []:
            if not isinstance(page, dict):
                continue

            page_number = page.get("page")

            if not isinstance(page_number, int):
                continue

            boxes = self._ocr_boxes(page)

            if boxes:
                yield ParsedPage(page_number=page_number, boxes=boxes)

    def _text_items(self, page: dict[str, Any]) -> list[Any]:
        if "text_items" in page:
            items = page.get("text_items")
        else:
            items = page.get("textItems")

        if not isinstance(items, list):
            return []

        return items

    def _ocr_boxes(self, page: dict[str, Any]) -> list[TextBox]:
        boxes: list[TextBox] = []

        for text_item in self._text_items(page):
            if not isinstance(text_item, dict):
                continue

            box = self._ocr_box_from_item(text_item)
            if box is not None:
                boxes.append(box)

        return boxes

    def _ocr_box_from_item(self, text_item: dict[str, Any]) -> TextBox | None:
        """One drawable OCR box, or None when the item should be skipped."""
        font_name = _first_present(text_item, "font_name", "fontName")
        if font_name != self.OCR_FONT_MARKER:
            return None

        text = (text_item.get("text") or "").strip()
        if not text:
            return None

        try:
            width = float(text_item.get("width") or 0)
            height = float(text_item.get("height") or 0)
            x = float(text_item["x"])
            y = float(text_item["y"])
            confidence_value = _first_present(text_item, "confidence")
            confidence = 1.0 if confidence_value is None else float(confidence_value)
            rotation_value = _first_present(text_item, "rotation")
            rotation = 0.0 if rotation_value is None else float(rotation_value)
        except (KeyError, TypeError, ValueError):
            return None

        if width <= 0 or height <= 0:
            return None

        return TextBox(
            text=text,
            x=x,
            y=y,
            width=width,
            height=height,
            confidence=confidence,
            rotation=rotation,
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
