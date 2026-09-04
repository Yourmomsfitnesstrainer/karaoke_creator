from __future__ import annotations

import hashlib
import re
import unicodedata
from pathlib import Path

from .models import LyricsDocument, LyricsLine, LyricsWord, RemovedLyricsLine


_JOINERS = {"-", "‐", "‑", "‒", "–", "—", "'", "’", "ʼ"}
_SECTION_HEADER = re.compile(r"^\[[^\[\]\n]{1,100}\]$")
_PROMO_BLOCK_MARKERS = {
    "you might also like",
    "you may also like",
    "вам также может понравиться",
}
_SINGLE_METADATA_LINES = (
    re.compile(r"^\d*\s*embed$", re.IGNORECASE),
    re.compile(r"^translations?(?:\s+\d+)?$", re.IGNORECASE),
    re.compile(r"^contributors?(?:\s+\d+)?$", re.IGNORECASE),
)


def _metadata_reason(line: str) -> str | None:
    if _SECTION_HEADER.fullmatch(line):
        return "section_header"
    folded = line.casefold()
    if folded in _PROMO_BLOCK_MARKERS:
        return "promo_marker"
    if any(pattern.fullmatch(line) for pattern in _SINGLE_METADATA_LINES):
        return "page_metadata"
    return None


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
    skipping_promo_block = False
    removed: list[RemovedLyricsLine] = []

    for line_number, raw_line in enumerate(text.split("\n"), start=1):
        display_line = " ".join(raw_line.split())
        if not display_line:
            if lines:
                pending_break = True
            continue
        reason = _metadata_reason(display_line)
        if reason == "section_header":
            skipping_promo_block = False
            if lines:
                pending_break = True
            removed.append(RemovedLyricsLine(display_line, line_number, reason))
            continue
        if reason == "promo_marker":
            skipping_promo_block = True
            removed.append(RemovedLyricsLine(display_line, line_number, reason))
            continue
        if skipping_promo_block:
            removed.append(RemovedLyricsLine(display_line, line_number, "promo_recommendation"))
            continue
        if reason is not None:
            removed.append(RemovedLyricsLine(display_line, line_number, reason))
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
    return LyricsDocument(text, processed_text, tuple(lines), tuple(removed))


def cleanup_report(document: LyricsDocument) -> dict:
    return {
        "schema_version": 1,
        "kept_lines": len(document.lines),
        "removed_count": len(document.removed_lines),
        "removed_lines": [
            {
                "line_number": line.line_number,
                "text": line.text,
                "reason": line.reason,
            }
            for line in document.removed_lines
        ],
    }


def parse_lyrics_file(path: Path) -> LyricsDocument:
    try:
        return parse_lyrics_text(path.read_text(encoding="utf-8-sig"))
    except UnicodeDecodeError as exc:
        raise ValueError(f"Lyrics must be UTF-8: {path}") from exc


def text_sha256(document: LyricsDocument) -> str:
    return hashlib.sha256(document.processed_text.encode("utf-8")).hexdigest()
