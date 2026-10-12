"""Conservative review hints, never automatic rewriting of recognized text."""

from __future__ import annotations

import re


def ocr_review_hints(text: str) -> list[str]:
    compact = "".join(text.split())
    hints = []
    if "\ufffd" in text:
        hints.append("ocr_invalid_characters")
    # Short repeated cries/onomatopoeia are legitimate. Only long non-punctuation
    # loops are suspicious; they remain visible for the user to judge.
    if re.search(r"([^\W_])\1{11,}", compact) or re.search(r"([^\W_]{2,6})\1{7,}", compact):
        hints.append("ocr_repetition")
    return hints
