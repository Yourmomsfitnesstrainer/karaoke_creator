from __future__ import annotations

import shutil
import tempfile
import uuid
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, HTMLResponse

from .config import load_config
from .pipeline import generate


app = FastAPI(title="Karaoke Creator", version="0.1.0")
JOBS_ROOT = Path(tempfile.gettempdir()) / "karaoke-creator-jobs"
JOBS_ROOT.mkdir(parents=True, exist_ok=True)

FORM = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width">
<title>Karaoke Creator</title><style>
body{margin:0;background:#080b1a;color:#f7f7fb;font:16px system-ui;display:grid;place-items:center;min-height:100vh}
main{width:min(720px,90vw);background:#151a36;padding:36px;border-radius:24px;box-shadow:0 24px 70px #0008}
h1{margin-top:0;font-size:38px}label{display:block;margin:18px 0 8px;color:#c8cbe0}
input,select,button{width:100%;box-sizing:border-box;padding:13px;border-radius:10px;border:1px solid #40476f;background:#0f1430;color:white}
button{margin-top:24px;background:#ffd43b;color:#171717;border:0;font-weight:800;cursor:pointer}
small{color:#9fa5c5}.ok{color:#70e5b1}a{color:#ffd43b}
</style></head><body><main><h1>Karaoke Creator</h1>
<p>Exact lyrics in. Word-highlighted MP4 out.</p>
<form action="/generate" method="post" enctype="multipart/form-data">
<label>Audio</label><input name="audio" type="file" accept="audio/*" required>
<label>Lyrics (UTF-8 TXT)</label><input name="lyrics" type="file" accept=".txt,text/plain" required>
<label>Language</label><input name="language" value="auto">
<label>Audio output</label><select name="audio_mode"><option value="instrumental">Instrumental</option><option value="original">Original</option></select>
<button>Generate karaoke</button></form><p><small>Files stay on this computer.</small></p></main></body></html>"""


@app.get("/", response_class=HTMLResponse)
def index() -> str:
    return FORM


@app.post("/generate", response_class=HTMLResponse)
def generate_job(
    audio: UploadFile = File(...),
    lyrics: UploadFile = File(...),
    language: str = Form("auto"),
    audio_mode: str = Form("instrumental"),
) -> str:
    if audio_mode not in {"original", "instrumental"}:
        raise HTTPException(400, "Invalid audio mode")
    job_id = uuid.uuid4().hex
    job_dir = JOBS_ROOT / job_id
    job_dir.mkdir(parents=True)
    audio_suffix = Path(audio.filename or "audio.wav").suffix.lower() or ".wav"
    if audio_suffix not in {".mp3", ".wav", ".flac", ".m4a", ".aac", ".ogg"}:
        raise HTTPException(400, "Unsupported audio format")
    audio_path = job_dir / f"audio{audio_suffix}"
    lyrics_path = job_dir / "lyrics.txt"
    with audio_path.open("wb") as handle:
        shutil.copyfileobj(audio.file, handle)
    with lyrics_path.open("wb") as handle:
        shutil.copyfileobj(lyrics.file, handle)
    config = load_config()
    config["alignment"]["language"] = language
    config["output"]["audio_mode"] = audio_mode
    try:
        generate(audio_path, lyrics_path, job_dir / "result", config)
    except Exception as exc:
        raise HTTPException(500, str(exc)) from exc
    return f"""<html><body style="background:#080b1a;color:white;font:18px system-ui;padding:3rem">
<h1 class="ok">Karaoke ready</h1><ul>
<li><a href="/jobs/{job_id}/karaoke.mp4">Download karaoke.mp4</a></li>
<li><a href="/jobs/{job_id}/karaoke.ass">Download karaoke.ass</a></li>
<li><a href="/jobs/{job_id}/alignment.json">Download alignment.json</a></li>
</ul><a href="/">Create another</a></body></html>"""


@app.get("/jobs/{job_id}/{filename}")
def download(job_id: str, filename: str) -> FileResponse:
    if not job_id.isalnum() or filename not in {"karaoke.mp4", "karaoke.ass", "alignment.json"}:
        raise HTTPException(404)
    path = JOBS_ROOT / job_id / "result" / filename
    if not path.is_file():
        raise HTTPException(404)
    return FileResponse(path, filename=filename)

