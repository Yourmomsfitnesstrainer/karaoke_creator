from pathlib import Path

from karaoke_generator.alignment import align_lyrics
from karaoke_generator.lyrics import parse_lyrics_text
from karaoke_generator.models import TimedWord
from karaoke_generator.subtitles import generate_ass, seconds_to_ass


def test_ass_time_rounding() -> None:
    assert seconds_to_ass(62.349) == "0:01:02.35"


def test_ass_uses_exact_words_kf_and_next_line(tmp_path: Path) -> None:
    document = parse_lyrics_text("Привет, мир!\nNext line")
    timed = [
        TimedWord("привет", 0.5, 1.0, 1),
        TimedWord("мир", 1.0, 1.5, 1),
        TimedWord("next", 2.0, 2.5, 1),
        TimedWord("line", 2.5, 3.0, 1),
    ]
    alignment = align_lyrics(document, timed, 4.0, "mixed", "test")
    output = tmp_path / "karaoke.ass"
    generate_ass(alignment, output, {"width": 1280, "height": 720, "max_chars_per_line": 40})
    content = output.read_text()
    assert "{\\kf50}Привет," in content
    assert "мир!" in content
    assert "Dialogue: 0" in content
    assert "Next line" in content


def test_ass_applies_negative_highlight_offset(tmp_path: Path) -> None:
    document = parse_lyrics_text("Привет мир")
    alignment = align_lyrics(
        document,
        [TimedWord("Привет", 1.0, 1.4, 1), TimedWord("мир", 1.4, 1.8, 1)],
        3.0,
        "ru",
        "test",
    )
    output = tmp_path / "karaoke.ass"

    generate_ass(alignment, output, {"timing_offset_ms": -250})

    assert "Dialogue: 1,0:00:00.75" in output.read_text()
