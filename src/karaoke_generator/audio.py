from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path


class ExternalCommandError(RuntimeError):
    pass


def _candidate_ffmpeg_paths() -> list[str]:
    candidates = [
        os.environ.get("KARAOKE_FFMPEG", ""),
        "/opt/homebrew/opt/ffmpeg-full/bin/ffmpeg",
        "/usr/local/opt/ffmpeg-full/bin/ffmpeg",
        shutil.which("ffmpeg") or "",
    ]
    return list(dict.fromkeys(candidate for candidate in candidates if candidate))


def ffmpeg_has_ass(path: str) -> bool:
    result = subprocess.run(
        [path, "-hide_banner", "-filters"], capture_output=True, text=True, check=False
    )
    filters = result.stdout + result.stderr
    return any(line.split()[1:2] == ["ass"] for line in filters.splitlines() if line.strip())


def find_ffmpeg(require_ass: bool = False) -> str:
    for candidate in _candidate_ffmpeg_paths():
        if not Path(candidate).exists():
            continue
        if not require_ass or ffmpeg_has_ass(candidate):
            return candidate
    if require_ass:
        raise RuntimeError(
            "FFmpeg with the libass 'ass' filter was not found. On macOS run "
            "`brew install ffmpeg-full`, or set KARAOKE_FFMPEG to a compatible binary."
        )
    raise RuntimeError("FFmpeg was not found. Install it or set KARAOKE_FFMPEG.")


def find_ffprobe(ffmpeg_path: str | None = None) -> str:
    if ffmpeg_path:
        sibling = str(Path(ffmpeg_path).with_name("ffprobe"))
        if Path(sibling).exists():
            return sibling
    path = shutil.which("ffprobe")
    if path:
        return path
    raise RuntimeError("ffprobe was not found")


def run_command(command: list[str], *, cwd: Path | None = None) -> None:
    result = subprocess.run(command, cwd=cwd, capture_output=True, text=True, check=False)
    if result.returncode:
        detail = "\n".join((result.stderr or result.stdout).strip().splitlines()[-12:])
        raise ExternalCommandError(f"Command failed ({result.returncode}): {' '.join(command[:3])}\n{detail}")


def probe_duration(path: Path, ffmpeg_path: str | None = None) -> float:
    ffprobe = find_ffprobe(ffmpeg_path)
    result = subprocess.run(
        [
            ffprobe,
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(path),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode:
        raise ExternalCommandError(f"Could not read audio duration: {result.stderr.strip()}")
    return float(result.stdout.strip())


def prepare_audio(input_path: Path, output_path: Path) -> None:
    ffmpeg = find_ffmpeg()
    temp_path = output_path.with_suffix(".tmp.wav")
    command = [
        ffmpeg,
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-i",
        str(input_path),
        "-vn",
        "-acodec",
        "pcm_s16le",
        "-ar",
        "44100",
        "-ac",
        "2",
        str(temp_path),
    ]
    run_command(command)
    temp_path.replace(output_path)

