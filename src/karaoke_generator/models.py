from __future__ import annotations

from dataclasses import asdict, dataclass, field
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


@dataclass
class AlignedWord:
    text: str
    normalized: str
    start: float
    end: float
    confidence: float
    aligned: bool
    alignment_source: str
    asr_text: str | None = None
    match_similarity: float | None = None


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
    recognized_words: int
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
    schema_version: int = 2

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "AlignmentResult":
        lines: list[AlignedLine] = []
        for raw_line in data["lines"]:
            words = [
                AlignedWord(
                    text=raw_word["text"],
                    normalized=raw_word["normalized"],
                    start=float(raw_word["start"]),
                    end=float(raw_word["end"]),
                    confidence=float(raw_word.get("confidence", 0.0)),
                    aligned=bool(raw_word.get("aligned", False)),
                    alignment_source=raw_word.get("alignment_source", "unknown"),
                    asr_text=raw_word.get("asr_text"),
                    match_similarity=(
                        float(raw_word["match_similarity"])
                        if raw_word.get("match_similarity") is not None
                        else None
                    ),
                )
                for raw_word in raw_line["words"]
            ]
            lines.append(
                AlignedLine(
                    text=raw_line["text"],
                    start=float(raw_line["start"]),
                    end=float(raw_line["end"]),
                    words=words,
                    block_index=int(raw_line.get("block_index", 0)),
                )
            )
        all_words = [word for line in lines for word in line.words]
        raw_quality = data.get("quality")
        if raw_quality:
            quality = AlignmentQuality(
                total_words=int(raw_quality.get("total_words", len(all_words))),
                recognized_words=int(raw_quality.get("recognized_words", 0)),
                directly_aligned=int(raw_quality.get("directly_aligned", 0)),
                interpolated=int(raw_quality.get("interpolated", 0)),
                aligned_ratio=float(raw_quality.get("aligned_ratio", 0.0)),
            )
        else:
            directly_aligned = sum(word.aligned for word in all_words)
            total_words = len(all_words)
            quality = AlignmentQuality(
                total_words=total_words,
                recognized_words=directly_aligned,
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
            schema_version=int(data.get("schema_version", 1)),
        )
