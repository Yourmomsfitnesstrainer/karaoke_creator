from karaoke_generator.alignment import align_lyrics, monotonic_word_mapping
from karaoke_generator.lyrics import parse_lyrics_text
from karaoke_generator.models import TimedWord


def test_repeated_lines_map_to_distinct_monotonic_occurrences() -> None:
    lyrics = ["я", "тебя", "люблю", "я", "тебя", "люблю"]
    timed = [TimedWord(word, index, index + 0.5, 0.9) for index, word in enumerate(lyrics)]
    assert monotonic_word_mapping(lyrics, timed) == list(range(6))


def test_missing_word_is_interpolated_and_display_is_unchanged() -> None:
    document = parse_lyrics_text("Я тебя навсегда люблю")
    timed = [
        TimedWord("я", 1.0, 1.2, 0.99),
        TimedWord("тебя", 1.2, 1.6, 0.98),
        TimedWord("люблю", 2.2, 3.0, 0.97),
    ]
    result = align_lyrics(document, timed, 4.0, "ru", "test")
    missing = result.lines[0].words[2]
    assert missing.text == "навсегда"
    assert missing.aligned is False
    assert missing.alignment_source == "interpolated"
    assert 1.6 <= missing.start < missing.end <= 2.2
    all_words = result.lines[0].words
    assert all_words == sorted(all_words, key=lambda word: word.start)

