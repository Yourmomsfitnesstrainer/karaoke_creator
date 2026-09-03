from __future__ import annotations

import hashlib
import re
import unicodedata
from pathlib import Path

from .models import LyricsDocument, LyricsLine, LyricsWord


_JOINERS = {"-", "‐", "‑", "‒", "–", "—", "'", "’", "ʼ"}


def normalize_for_alignment(value: str) -> str:
    """Return a comparison form without mutating the display text."""
    value = unicodedata.normalize("NFKC", value).casefold()
    chars: list[str] = []
    for char in value:
        if char in _JOINERS or char.isspace():
            chars.append(" ")
        elif unicodedata.category(char)[0] in {"L", "N"}:
            chars.append(char)
    return re.sub(r"\s+", " ", "".join(chars)).strip()


def comparison_key(value: str) -> str:
    return normalize_for_alignment(value).replace(" ", "")


def parse_lyrics_text(text: str) -> LyricsDocument:
    text = text.replace("\r\n", "\n").replace("\r", "\n").lstrip("\ufeff")
    lines: list[LyricsLine] = []
    processed: list[str] = []
    word_index = 0
    block_index = 0
    pending_break = False

    for raw_line in text.split("\n"):
        display_line = " ".join(raw_line.split())
        if not display_line:
            if lines:
                pending_break = True
            continue
        if pending_break:
            block_index += 1
            processed.append("")
            pending_break = False
        displays = display_line.split(" ")
        line_index = len(lines)
        words: list[LyricsWord] = []
        for display in displays:
            words.append(
                LyricsWord(
                    display=display,
                    normalized=normalize_for_alignment(display),
                    index=word_index,
                    line_index=line_index,
                )
            )
            word_index += 1
        lines.append(
            LyricsLine(
                text=display_line,
                words=tuple(words),
                index=line_index,
                block_index=block_index,
            )
        )
        processed.append(display_line)

    if not lines:
        raise ValueError("Lyrics file contains no words")
    processed_text = "\n".join(processed).strip() + "\n"
    return LyricsDocument(text, processed_text, tuple(lines))


def parse_lyrics_file(path: Path) -> LyricsDocument:
    try:
        return parse_lyrics_text(path.read_text(encoding="utf-8-sig"))
    except UnicodeDecodeError as exc:
        raise ValueError(f"Lyrics must be UTF-8: {path}") from exc


def text_sha256(document: LyricsDocument) -> str:
    return hashlib.sha256(document.processed_text.encode("utf-8")).hexdigest()

