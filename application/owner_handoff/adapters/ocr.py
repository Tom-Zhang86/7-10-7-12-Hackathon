"""OCR adapter boundary (Master Spec section 4, 8).

Only a no-op and a deterministic mock implementation exist in Phase 1. This
module implements no screenshot capture, no macOS Vision integration, no
Tesseract binding, and no cloud OCR dependency — real OCR is explicitly
deferred to a later phase. Neither implementation touches the filesystem, so
neither can ever leave behind a screenshot or a temporary image path.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class OCRResult:
    """Sanitized text only — never screenshot bytes or a file path."""

    text: str
    had_text: bool


class OCRProvider(Protocol):
    """Boundary a future real OCR adapter must implement.

    A real implementation must use the minimum screen region, must not
    persist screenshots by default, and must delete any temporary capture
    immediately — only sanitized extracted text may be retained (Master
    Spec section 4). Phase 1 ships no such implementation.
    """

    def capture_and_read(self) -> OCRResult:
        ...


class NoOpOCRProvider:
    """Always returns no evidence. The default adapter in Phase 1."""

    def capture_and_read(self) -> OCRResult:
        return OCRResult(text="", had_text=False)


class MockOCRProvider:
    """Deterministic OCR stand-in for tests and simulator development only."""

    def __init__(self, text: str = "") -> None:
        self._text = text

    def capture_and_read(self) -> OCRResult:
        return OCRResult(text=self._text, had_text=bool(self._text.strip()))
