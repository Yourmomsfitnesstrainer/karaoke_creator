from __future__ import annotations

import argparse
import logging
import shutil
import sys
from pathlib import Path

from .audio import ffmpeg_has_ass, find_ffmpeg
from .config import load_config
from .pipeline import generate, rerender
from .separation import DemucsSeparator


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="karaoke-gen", description="Exact lyrics + audio → karaoke MP4")
    parser.add_argument("--config", type=Path, help="YAML config path")
    subparsers = parser.add_subparsers(dest="command")

    generate_parser = subparsers.add_parser("generate", help="Run the complete pipeline")
    generate_parser.add_argument("--audio", type=Path, required=True)
    generate_parser.add_argument("--lyrics", type=Path, required=True)
    generate_parser.add_argument("--output", type=Path, required=True)
    generate_parser.add_argument("--language")
    generate_parser.add_argument("--backend", choices=["faster-whisper", "whisperx", "uniform"])
    generate_parser.add_argument("--model")
    generate_parser.add_argument("--audio-mode", choices=["original", "instrumental"])
    generate_parser.add_argument("--skip-separation", action="store_true")
    generate_parser.add_argument("--resolution", help="e.g. 1920x1080")
    generate_parser.add_argument("--background", help="procedural, solid, image, or video path")

    render_parser = subparsers.add_parser("render", help="Render edited alignment.json without ML")
    render_parser.add_argument("--alignment", type=Path, required=True)
    render_parser.add_argument("--audio", type=Path, required=True)
    render_parser.add_argument("--output", type=Path, required=True)
    render_parser.add_argument("--background")

    subparsers.add_parser("doctor", help="Check local runtime dependencies")
    web_parser = subparsers.add_parser("web", help="Start the local web UI")
    web_parser.add_argument("--host", default="127.0.0.1")
    web_parser.add_argument("--port", type=int, default=8080)
    return parser


def _apply_overrides(config: dict, args: argparse.Namespace) -> None:
    for option in ("language", "backend", "model"):
        value = getattr(args, option, None)
        if value:
            config["alignment"][option] = value
    if getattr(args, "audio_mode", None):
        config["output"]["audio_mode"] = args.audio_mode
    if getattr(args, "skip_separation", False):
        config["separation"]["enabled"] = False
    if getattr(args, "resolution", None):
        try:
            width, height = args.resolution.lower().split("x", 1)
            config["video"].update(width=int(width), height=int(height))
        except ValueError as exc:
            raise SystemExit("--resolution must look like 1920x1080") from exc


def _doctor() -> int:
    print(f"Python: {sys.version.split()[0]}")
    print(f"Architecture: {__import__('platform').machine()}")
    try:
        ffmpeg = find_ffmpeg(require_ass=True)
        print(f"FFmpeg/libass: OK ({ffmpeg})")
    except RuntimeError as exc:
        fallback = shutil.which("ffmpeg")
        print(f"FFmpeg/libass: MISSING ({exc})")
        if fallback:
            print(f"Found incompatible FFmpeg: {fallback}; ass={ffmpeg_has_ass(fallback)}")
    try:
        import faster_whisper  # noqa: F401

        print("faster-whisper: OK")
    except ImportError:
        print("faster-whisper: optional dependency missing")
    print(f"Demucs: {'OK' if DemucsSeparator.available() else 'optional dependency missing'}")
    return 0


def main(argv: list[str] | None = None) -> None:
    argv = list(sys.argv[1:] if argv is None else argv)
    commands = {"generate", "render", "doctor", "web"}
    if argv and argv[0] not in commands and any(arg == "--audio" for arg in argv):
        argv.insert(0, "generate")
    parser = _parser()
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    if args.command == "doctor":
        raise SystemExit(_doctor())
    config = load_config(args.config)
    if args.command == "generate":
        _apply_overrides(config, args)
        artifacts = generate(args.audio, args.lyrics, args.output, config, background=args.background)
        print("\nCreated:")
        for name, path in artifacts.items():
            print(f"  {name}: {path}")
        return
    if args.command == "render":
        artifacts = rerender(args.alignment, args.audio, args.output, config, background=args.background)
        for name, path in artifacts.items():
            print(f"{name}: {path}")
        return
    if args.command == "web":
        try:
            import uvicorn
        except ImportError as exc:
            raise SystemExit("Install the web extra: pip install -e '.[web]'") from exc
        uvicorn.run("karaoke_generator.web:app", host=args.host, port=args.port)
        return
    parser.print_help()


if __name__ == "__main__":
    main()

