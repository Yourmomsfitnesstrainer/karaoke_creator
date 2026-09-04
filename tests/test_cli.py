from karaoke_generator.cli import _apply_overrides, _parser
from karaoke_generator.config import load_config


def test_generate_arguments_are_parsed() -> None:
    args = _parser().parse_args(
        ["generate", "--audio", "song.wav", "--lyrics", "lyrics.txt", "--output", "result"]
    )
    assert args.command == "generate"
    assert args.audio.name == "song.wav"


def test_generate_timing_overrides_are_applied() -> None:
    args = _parser().parse_args(
        [
            "generate",
            "--audio",
            "song.wav",
            "--lyrics",
            "lyrics.txt",
            "--output",
            "result",
            "--no-vad",
            "--timing-offset-ms",
            "-325",
        ]
    )
    config = load_config()

    _apply_overrides(config, args)

    assert config["alignment"]["vad_filter"] is False
    assert config["karaoke"]["timing_offset_ms"] == -325
