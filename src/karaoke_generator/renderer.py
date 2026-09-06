from __future__ import annotations

from pathlib import Path
import subprocess

from .audio import find_ffmpeg, probe_duration, run_command


IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}


def render_video(
    ass_path: Path,
    audio_path: Path,
    output_path: Path,
    video: dict,
    output_settings: dict,
    background: str | None = None,
) -> dict:
    ffmpeg = find_ffmpeg(require_ass=True)
    duration = probe_duration(audio_path, ffmpeg)
    width = int(video.get("width", 1920))
    height = int(video.get("height", 1080))
    fps = int(video.get("fps", 30))
    background = background or str(video.get("background", "procedural"))
    common = [ffmpeg, "-y", "-hide_banner", "-loglevel", "info"]

    if background == "procedural":
        source = (
            f"gradients=size={width}x{height}:rate={fps}:duration={duration:.3f}:"
            "c0=0x080B1A:c1=0x24295C:c2=0x116466:nb_colors=3:speed=0.012:type=radial"
        )
        inputs = ["-f", "lavfi", "-i", source]
        scale_filter = ""
    elif background == "solid":
        color = str(video.get("background_color", "#080B1A")).replace("#", "0x")
        inputs = ["-f", "lavfi", "-i", f"color=c={color}:s={width}x{height}:r={fps}:d={duration:.3f}"]
        scale_filter = ""
    else:
        path = Path(background).expanduser().resolve()
        if not path.exists():
            raise FileNotFoundError(f"Background does not exist: {path}")
        if path.suffix.lower() in IMAGE_SUFFIXES:
            inputs = ["-loop", "1", "-framerate", str(fps), "-i", str(path)]
        else:
            inputs = ["-stream_loop", "-1", "-i", str(path)]
        scale_filter = (
            f"scale={width}:{height}:force_original_aspect_ratio=increase,"
            f"crop={width}:{height},"
        )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = output_path.with_name(output_path.stem + ".tmp.mp4")
    # Run inside the ASS directory so the filter never has to parse escaped absolute paths.
    filter_graph = f"{scale_filter}setpts=PTS-STARTPTS,ass=filename={ass_path.name}"
    command = common + inputs + [
        "-i",
        str(audio_path.resolve()),
        "-vf",
        filter_graph,
        "-af",
        "asetpts=N/SR/TB",
        "-map",
        "0:v:0",
        "-map",
        "1:a:0",
        "-t",
        f"{duration:.3f}",
        "-r",
        str(fps),
        "-c:v",
        "libx264",
        "-preset",
        "medium",
        "-b:v",
        str(output_settings.get("video_bitrate", "8M")),
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        "-b:a",
        str(output_settings.get("audio_bitrate", "256k")),
        "-movflags",
        "+faststart",
        str(temp_path.resolve()),
    ]
    log = run_command(command, cwd=ass_path.parent)
    temp_path.replace(output_path)
    return {"ffmpeg_binary": ffmpeg,
            "ffmpeg_version": subprocess.check_output([ffmpeg, "-version"], text=True).splitlines()[0], "versions": [line.strip() for line in log.splitlines()
            if "libass API version" in line or "libass source" in line],
            "settings": {"fps": fps, "width": width, "height": height, "background": background}}

