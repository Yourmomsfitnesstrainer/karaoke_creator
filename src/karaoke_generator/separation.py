from __future__ import annotations

import importlib.util
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from .audio import ExternalCommandError


@dataclass(frozen=True)
class SeparationResult:
    vocals: Path
    instrumental: Path | None
    backend: str


class DemucsSeparator:
    def __init__(self, model: str = "htdemucs") -> None:
        self.model = model

    @staticmethod
    def available() -> bool:
        return importlib.util.find_spec("demucs") is not None

    def separate(self, source: Path, output_dir: Path) -> SeparationResult:
        if not self.available():
            raise RuntimeError("Demucs is not installed; install the 'separation' extra")
        work = output_dir / "demucs"
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "demucs",
                "--two-stems=vocals",
                "-n",
                self.model,
                "-d",
                "cpu",
                "-o",
                str(work),
                str(source),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode:
            detail = "\n".join(result.stderr.strip().splitlines()[-12:])
            raise ExternalCommandError(f"Vocal separation failed.\n{detail}")
        stem_dir = work / self.model / source.stem
        vocals_source = stem_dir / "vocals.wav"
        instrumental_source = stem_dir / "no_vocals.wav"
        if not vocals_source.exists() or not instrumental_source.exists():
            raise RuntimeError(f"Demucs completed but stems were not found in {stem_dir}")
        vocals = output_dir / "vocals.wav"
        instrumental = output_dir / "instrumental.wav"
        shutil.copy2(vocals_source, vocals)
        shutil.copy2(instrumental_source, instrumental)
        return SeparationResult(vocals, instrumental, f"demucs:{self.model}")


def original_audio_fallback(source: Path, output_dir: Path) -> SeparationResult:
    vocals = output_dir / "vocals.wav"
    shutil.copy2(source, vocals)
    return SeparationResult(vocals, None, "original-audio")

