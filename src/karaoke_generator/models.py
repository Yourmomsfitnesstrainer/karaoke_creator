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
class LyricsDocument:
    original_text: str
    processed_text: str
    lines: tuple[LyricsLine, ...]

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


@dataclass
class AlignedLine:
    text: str
    start: float
    end: float
    words: list[AlignedWord] = field(default_factory=list)
    block_index: int = 0


@dataclass
class AlignmentResult:
    language: str
    duration: float
    source_text_sha256: str
    lines: list[AlignedLine]
    backend: str
    schema_version: int = 1

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "AlignmentResult":
        lines: list[AlignedLine] = []
        for raw_line in data["lines"]:
            words = [AlignedWord(**raw_word) for raw_word in raw_line["words"]]
            lines.append(
                AlignedLine(
                    text=raw_line["text"],
                    start=float(raw_line["start"]),
                    end=float(raw_line["end"]),
                    words=words,
                    block_index=int(raw_line.get("block_index", 0)),
                )
            )
        return cls(
            language=data.get("language", "unknown"),
            duration=float(data["duration"]),
            source_text_sha256=data.get("source_text_sha256", ""),
            lines=lines,
            backend=data.get("backend", "unknown"),
            schema_version=int(data.get("schema_version", 1)),
        )

