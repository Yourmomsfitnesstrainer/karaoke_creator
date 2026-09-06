from __future__ import annotations

from dataclasses import replace
import math
from pathlib import Path

from .models import AlignedLine, AlignmentResult


def seconds_to_ass(value: float) -> str:
    centiseconds = max(0, int(round(value * 100)))
    hours, remainder = divmod(centiseconds, 360000)
    minutes, remainder = divmod(remainder, 6000)
    seconds, fraction = divmod(remainder, 100)
    return f"{hours}:{minutes:02d}:{seconds:02d}.{fraction:02d}"


def _ass_color(value: str, alpha: str = "00") -> str:
    hex_value = value.lstrip("#")
    if len(hex_value) != 6:
        raise ValueError(f"Expected #RRGGBB color, got {value}")
    red, green, blue = hex_value[0:2], hex_value[2:4], hex_value[4:6]
    return f"&H{alpha}{blue}{green}{red}"


def _escape(value: str) -> str:
    return value.replace("\\", r"\\").replace("{", r"\{").replace("}", r"\}")


def _split_visual_lines(lines: list[AlignedLine], max_chars: int) -> list[AlignedLine]:
    output: list[AlignedLine] = []
    for line in lines:
        chunk = []
        length = 0
        for word in line.words:
            projected = length + (1 if chunk else 0) + len(word.text)
            if chunk and projected > max_chars:
                output.append(
                    replace(
                        line,
                        text=" ".join(item.text for item in chunk),
                        start=chunk[0].start,
                        end=chunk[-1].end,
                        words=list(chunk),
                    )
                )
                chunk = []
                length = 0
            chunk.append(word)
            length += (1 if length else 0) + len(word.text)
        if chunk:
            output.append(
                replace(
                    line,
                    text=" ".join(item.text for item in chunk),
                    start=chunk[0].start,
                    end=chunk[-1].end,
                    words=list(chunk),
                )
            )
    return output


def _karaoke_text(line: AlignedLine, origin_cs: int, offset: float) -> str:
    """Quantize absolute boundaries; kt also preserves progress before time zero."""
    parts: list[str] = []
    for index, word in enumerate(line.words):
        start_cs = round((word.start + offset) * 100)
        end_cs = round((word.end + offset) * 100)
        prefix = "" if index == 0 else r"{\k0} "
        parts.append(
            f"{prefix}{{\\kt{start_cs - origin_cs}}}"
            f"{{\\kf{max(0, end_cs - start_cs)}}}{_escape(word.text)}"
        )
    return "".join(parts)


def generate_ass(result: AlignmentResult, output: Path, settings: dict) -> None:
    width = int(settings.get("width", 1920))
    height = int(settings.get("height", 1080))
    font = settings.get("font", "Arial")
    font_size = int(settings.get("font_size", 72))
    active = _ass_color(settings.get("active_color", "#FFD43B"))
    inactive = _ass_color(settings.get("inactive_color", "#F2F3F5"))
    preview = _ass_color(settings.get("preview_color", "#A7ABB7"))
    max_chars = int(settings.get("max_chars_per_line", 42))
    timing_offset = float(settings.get("timing_offset_ms", 0)) / 1000.0
    if not math.isfinite(timing_offset) or not -1 <= timing_offset <= 1:
        raise ValueError("Timing offset must be between -1000 and 1000 ms")
    for line in result.lines:
        for word in line.words:
            if not (math.isfinite(word.start) and math.isfinite(word.end)
                    and 0 <= word.start < word.end <= result.duration):
                raise ValueError(f"Invalid canonical timing for word: {word.text}")
    lines = _split_visual_lines(result.lines, max_chars)

    header = f"""[Script Info]
Title: karaoke-creator
ScriptType: v4.00+
PlayResX: {width}
PlayResY: {height}
WrapStyle: 2
ScaledBorderAndShadow: yes
YCbCr Matrix: TV.709

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Current,{font},{font_size},{active},{inactive},&H00101010,&H80000000,-1,0,0,0,100,100,0,0,1,4,2,2,90,90,190,1
Style: Next,{font},{max(28, int(font_size * 0.72))},{preview},{preview},&H00101010,&H80000000,0,0,0,0,100,100,0,0,1,3,1,2,110,110,90,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    events: list[str] = []
    for index, line in enumerate(lines):
        start = max(0, round((min(w.start for w in line.words) + timing_offset) * 100)) / 100
        end = max(w.end for w in line.words) + timing_offset + 0.18
        if end <= start:
            continue
        events.append(
            f"Dialogue: 1,{seconds_to_ass(start)},{seconds_to_ass(end)},Current,,0,0,0,,{_karaoke_text(line, round(start * 100), timing_offset)}"
        )
        if index + 1 < len(lines):
            next_line = lines[index + 1]
            next_start = max(0.0, next_line.start + timing_offset)
            preview_end = max(start + 0.05, min(next_start, end))
            events.append(
                f"Dialogue: 0,{seconds_to_ass(start)},{seconds_to_ass(preview_end)},Next,,0,0,0,,{_escape(next_line.text)}"
            )
    output.write_text(header + "\n".join(events) + "\n", encoding="utf-8")
