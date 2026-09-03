from karaoke_generator.lyrics import normalize_for_alignment, parse_lyrics_text


def test_parser_preserves_display_text_and_blocks() -> None:
    document = parse_lyrics_text("  Я   тебя  \n\nЧто-то rock’n’roll, ёлка\n")
    assert document.processed_text == "Я тебя\n\nЧто-то rock’n’roll, ёлка\n"
    assert [line.block_index for line in document.lines] == [0, 1]
    assert [word.display for word in document.words] == ["Я", "тебя", "Что-то", "rock’n’roll,", "ёлка"]


def test_normalization_supports_cyrillic_joiners_and_punctuation() -> None:
    assert normalize_for_alignment("Что-то") == "что то"
    assert normalize_for_alignment("Ёлка!") == "ёлка"
    assert normalize_for_alignment("rock’n’roll,") == "rock n roll"

