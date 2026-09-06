from __future__ import annotations

from dataclasses import asdict, dataclass, field
from copy import deepcopy
from typing import Any


@dataclass(frozen=True)
class LyricsWord:
    display: str
    normalized: str
    index: int
    line_index: int


@dataclass(frozen=True)
class LyricsLine:
    text: str
    words: tuple[LyricsWord, ...]
    index: int
    block_index: int


@dataclass(frozen=True)
class RemovedLyricsLine:
    text: str
    line_number: int
    reason: str


@dataclass(frozen=True)
class LyricsDocument:
    original_text: str
    processed_text: str
    lines: tuple[LyricsLine, ...]
    removed_lines: tuple[RemovedLyricsLine, ...] = ()

    @property
    def words(self) -> tuple[LyricsWord, ...]:
        return tuple(word for line in self.lines for word in line.words)


@dataclass(frozen=True)
class TimedWord:
    text: str
    start: float
    end: float
    confidence: float | None = None
    segment_id: int | None = None
    timing: dict[str, Any] | None = None
    characters: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class AlignedWord:
    text: str
    normalized: str
    start: float
    end: float
    confidence: float | None
    aligned: bool
    alignment_source: str
    asr_text: str | None = None
    match_similarity: float | None = None
    timing: dict[str, Any] | None = None


@dataclass
class AlignedLine:
    text: str
    start: float
    end: float
    words: list[AlignedWord] = field(default_factory=list)
    block_index: int = 0


@dataclass(frozen=True)
class AlignmentQuality:
    total_words: int
    recognized_words: int | None
    directly_aligned: int
    interpolated: int
    aligned_ratio: float


@dataclass
class AlignmentResult:
    language: str
    duration: float
    source_text_sha256: str
    lines: list[AlignedLine]
    backend: str
    quality: AlignmentQuality
    schema_version: int = 3
    diagnostics: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "AlignmentResult":
        if int(data.get("schema_version", 1)) not in (1, 2, 3):
            raise ValueError("Unsupported alignment JSON schema")
        lines: list[AlignedLine] = []
        for raw_line in data["lines"]:
            words = [
                AlignedWord(
                    text=raw_word["text"],
                    normalized=raw_word["normalized"],
                    start=float(raw_word["start"]),
                    end=float(raw_word["end"]),
                    confidence=float(raw_word["confidence"]) if raw_word.get("confidence") is not None else None,
                    aligned=bool(raw_word.get("aligned", False)),
                    alignment_source=raw_word.get("alignment_source", "unknown"),
                    asr_text=raw_word.get("asr_text"),
                    match_similarity=(
                        float(raw_word["match_similarity"])
                        if raw_word.get("match_similarity") is not None
                        else None
                    ),
                    timing=deepcopy(raw_word.get("timing")),
                )
                for raw_word in raw_line["words"]
            ]
            for word in words:
                # Canonical user edits win. Historical evidence is never replayed.
                if word.timing and word.timing.get("generated"):
                    generated = word.timing["generated"]
                    if (word.start, word.end) != (generated["start"], generated["end"]):
                        word.timing["source"] = "manual"
            lines.append(
                AlignedLine(
                    text=raw_line["text"],
                    start=min((word.start for word in words), default=float(raw_line["start"])),
                    end=max((word.end for word in words), default=float(raw_line["end"])),
                    words=words,
                    block_index=int(raw_line.get("block_index", 0)),
                )
            )
        all_words = [word for line in lines for word in line.words]
        raw_quality = data.get("quality")
        if raw_quality:
            quality = AlignmentQuality(
                total_words=int(raw_quality.get("total_words", len(all_words))),
                recognized_words=int(raw_quality["recognized_words"]) if raw_quality.get("recognized_words") is not None else None,
                directly_aligned=int(raw_quality.get("directly_aligned", 0)),
                interpolated=int(raw_quality.get("interpolated", 0)),
                aligned_ratio=float(raw_quality.get("aligned_ratio", 0.0)),
            )
        else:
            directly_aligned = sum(word.aligned for word in all_words)
            total_words = len(all_words)
            quality = AlignmentQuality(
                total_words=total_words,
                recognized_words=None,
                directly_aligned=directly_aligned,
                interpolated=total_words - directly_aligned,
                aligned_ratio=round(directly_aligned / total_words, 4) if total_words else 0.0,
            )
        return cls(
            language=data.get("language", "unknown"),
            duration=float(data["duration"]),
            source_text_sha256=data.get("source_text_sha256", ""),
            lines=lines,
            backend=data.get("backend", "unknown"),
            quality=quality,
            schema_version=3,
            diagnostics=deepcopy(data.get("diagnostics", {})),
        )
