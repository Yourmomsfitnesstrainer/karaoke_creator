from karaoke_generator.lyrics import cleanup_report, normalize_for_alignment, parse_lyrics_text


def test_parser_preserves_display_text_and_blocks() -> None:
    document = parse_lyrics_text("  Я   тебя  \n\nЧто-то rock’n’roll, ёлка\n")
    assert document.processed_text == "Я тебя\n\nЧто-то rock’n’roll, ёлка\n"
    assert [line.block_index for line in document.lines] == [0, 1]
    assert [word.display for word in document.words] == ["Я", "тебя", "Что-то", "rock’n’roll,", "ёлка"]


def test_normalization_supports_cyrillic_joiners_and_punctuation() -> None:
    assert normalize_for_alignment("Что-то") == "что то"
    assert normalize_for_alignment("Ёлка!") == "ёлка"
    assert normalize_for_alignment("rock’n’roll,") == "rock n roll"


def test_parser_removes_section_headers_and_resumes_after_promo_block() -> None:
    document = parse_lyrics_text(
        "[Текст песни «Нервы»]\n"
        "[Куплет 1]\n"
        "Первая строка\n"
        "You might also like\n"
        "POPSTAR\n"
        "Some Artist\n"
        "[Аутро]\n"
        "Последняя строка\n"
    )

    assert document.processed_text == "Первая строка\n\nПоследняя строка\n"
    assert [line.text for line in document.removed_lines] == [
        "[Текст песни «Нервы»]",
        "[Куплет 1]",
        "You might also like",
        "POPSTAR",
        "Some Artist",
        "[Аутро]",
    ]
    assert document.removed_lines[3].reason == "promo_recommendation"
    assert cleanup_report(document)["removed_count"] == 6


def test_parser_does_not_remove_real_bilingual_lyrics() -> None:
    document = parse_lyrics_text("Я здесь\nI am still singing\n")

    assert document.processed_text == "Я здесь\nI am still singing\n"
    assert document.removed_lines == ()
