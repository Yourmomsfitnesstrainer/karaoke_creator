from karaoke_generator.cli import _parser


def test_generate_arguments_are_parsed() -> None:
    args = _parser().parse_args(
        ["generate", "--audio", "song.wav", "--lyrics", "lyrics.txt", "--output", "result"]
    )
    assert args.command == "generate"
    assert args.audio.name == "song.wav"

