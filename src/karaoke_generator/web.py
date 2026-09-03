from __future__ import annotations

import shutil
import tempfile
import threading
import uuid
from pathlib import Path
from typing import Any

from fastapi import BackgroundTasks, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, HTMLResponse

from .config import load_config
from .pipeline import generate


app = FastAPI(title="Karaoke Creator", version="0.1.0")
JOBS_ROOT = Path(tempfile.gettempdir()) / "karaoke-creator-jobs"
JOBS_ROOT.mkdir(parents=True, exist_ok=True)
JOBS: dict[str, dict[str, Any]] = {}
JOBS_LOCK = threading.Lock()

FORM = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width">
<title>Karaoke Creator</title><style>
body{margin:0;background:#080b1a;color:#f7f7fb;font:16px system-ui;display:grid;place-items:center;min-height:100vh}
main{width:min(720px,90vw);background:#151a36;padding:36px;border-radius:24px;box-shadow:0 24px 70px #0008}
h1{margin-top:0;font-size:38px}label{display:block;margin:18px 0 8px;color:#c8cbe0}
input,select,button{width:100%;box-sizing:border-box;padding:13px;border-radius:10px;border:1px solid #40476f;background:#0f1430;color:white}
button{margin-top:24px;background:#ffd43b;color:#171717;border:0;font-weight:800;cursor:pointer}
button:disabled{cursor:wait;opacity:.65}small{color:#9fa5c5}.ok{color:#70e5b1}a{color:#ffd43b}
#progress-panel{margin-top:28px;padding:20px;border:1px solid #343d68;border-radius:16px;background:#0d122a}
#progress-panel[hidden]{display:none}.progress-head{display:flex;justify-content:space-between;gap:18px;margin-bottom:12px}
.progress-track{height:14px;overflow:hidden;border-radius:999px;background:#252c52;box-shadow:inset 0 1px 4px #0008}
#progress-bar{position:relative;width:0;height:100%;border-radius:inherit;background:linear-gradient(90deg,#ffd43b,#ff8a3d);transition:width .35s ease}
#progress-bar:after{content:"";position:absolute;inset:0;background:linear-gradient(100deg,transparent 25%,#fff7 50%,transparent 75%);animation:shimmer 1.3s infinite}
#progress-detail{min-height:1.5em;margin:12px 0 0;color:#aeb5d8}.error{color:#ff8f9b!important}
#result{margin-top:18px}#result video{width:100%;margin-top:16px;border-radius:12px;background:#000}
@keyframes shimmer{from{transform:translateX(-100%)}to{transform:translateX(100%)}}
</style></head><body><main><h1>Karaoke Creator</h1>
<p>Exact lyrics in. Word-highlighted MP4 out.</p>
<form id="generate-form" action="/generate" method="post" enctype="multipart/form-data">
<label>Audio</label><input name="audio" type="file" accept="audio/*" required>
<label>Lyrics (UTF-8 TXT)</label><input name="lyrics" type="file" accept=".txt,text/plain" required>
<label>Language</label><input name="language" value="auto" list="languages" aria-describedby="language-help">
<datalist id="languages"><option value="auto"><option value="English"><option value="Russian"><option value="en"><option value="ru"></datalist>
<small id="language-help">Use auto, a language name, or an ISO code.</small>
<label>Audio output</label><select name="audio_mode"><option value="instrumental">Instrumental</option><option value="original">Original</option></select>
<button id="generate-button">Generate karaoke</button></form>
<section id="progress-panel" aria-live="polite" hidden>
<div class="progress-head"><strong id="progress-stage">Uploading files</strong><span id="progress-percent">0%</span></div>
<div class="progress-track" role="progressbar" aria-label="Karaoke generation progress" aria-valuemin="0" aria-valuemax="100" aria-valuenow="0"><div id="progress-bar"></div></div>
<p id="progress-detail">The five pipeline stages report real completion.</p><div id="result"></div>
</section><p><small>Files stay on this computer.</small></p></main>
<script>
const form=document.querySelector('#generate-form');
const button=document.querySelector('#generate-button');
const panel=document.querySelector('#progress-panel');
const stage=document.querySelector('#progress-stage');
const percent=document.querySelector('#progress-percent');
const detail=document.querySelector('#progress-detail');
const bar=document.querySelector('#progress-bar');
const track=document.querySelector('.progress-track');
const result=document.querySelector('#result');
const delay=ms=>new Promise(resolve=>setTimeout(resolve,ms));

function updateProgress(value,label){
  const safe=Math.max(0,Math.min(100,Number(value)||0));
  bar.style.width=`${safe}%`; percent.textContent=`${safe}%`;
  track.setAttribute('aria-valuenow',String(safe)); stage.textContent=label;
}

function showArtifacts(artifacts){
  result.replaceChildren();
  const heading=document.createElement('strong'); heading.className='ok'; heading.textContent='Karaoke ready';
  const list=document.createElement('ul');
  for(const [name,url] of Object.entries(artifacts)){
    const item=document.createElement('li'); const link=document.createElement('a');
    link.href=url; link.textContent=`Download ${name}`; item.append(link); list.append(item);
  }
  const video=document.createElement('video'); video.controls=true; video.src=artifacts['karaoke.mp4'];
  result.append(heading,list,video);
}

form.addEventListener('submit',async event=>{
  event.preventDefault(); panel.hidden=false; result.replaceChildren(); detail.className='';
  button.disabled=true; button.textContent='Generating…'; updateProgress(0,'Uploading files');
  try{
    const response=await fetch('/api/jobs',{method:'POST',body:new FormData(form)});
    const created=await response.json();
    if(!response.ok) throw new Error(created.detail||'Could not start generation');
    while(true){
      const statusResponse=await fetch(created.status_url,{cache:'no-store'});
      const job=await statusResponse.json();
      if(!statusResponse.ok) throw new Error(job.detail||'Could not read job status');
      updateProgress(job.progress,job.stage); detail.textContent=job.detail||'';
      if(job.status==='complete'){showArtifacts(job.artifacts);break;}
      if(job.status==='failed') throw new Error(job.error||'Generation failed');
      await delay(700);
    }
  }catch(error){
    stage.textContent='Generation failed'; detail.className='error'; detail.textContent=error.message;
  }finally{button.disabled=false;button.textContent='Generate karaoke';}
});
</script></body></html>"""


@app.get("/", response_class=HTMLResponse)
def index() -> str:
    return FORM


def _set_job(job_id: str, **changes: Any) -> None:
    with JOBS_LOCK:
        job = JOBS[job_id]
        if "progress" in changes:
            changes["progress"] = max(job["progress"], min(100, int(changes["progress"])))
        job.update(changes)


def _job_snapshot(job_id: str) -> dict[str, Any]:
    if not job_id.isalnum():
        raise HTTPException(404)
    with JOBS_LOCK:
        if job_id not in JOBS:
            raise HTTPException(404)
        return dict(JOBS[job_id])


def _normalize_language(language: str) -> str:
    value = language.strip().lower()
    aliases = {"english": "en", "russian": "ru"}
    value = aliases.get(value, value)
    if value == "auto" or (value.isalpha() and 2 <= len(value) <= 3):
        return value
    raise HTTPException(400, "Language must be auto, a language name, or a 2–3 letter ISO code")


def _prepare_job(
    audio: UploadFile,
    lyrics: UploadFile,
    language: str,
    audio_mode: str,
) -> tuple[str, Path, Path, str]:
    if audio_mode not in {"original", "instrumental"}:
        raise HTTPException(400, "Invalid audio mode")
    normalized_language = _normalize_language(language)
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
    with JOBS_LOCK:
        JOBS[job_id] = {
            "id": job_id,
            "status": "queued",
            "progress": 0,
            "stage": "Queued",
            "detail": "Waiting for a worker",
            "artifacts": {},
        }
    return job_id, audio_path, lyrics_path, normalized_language


def _run_job(job_id: str, audio_path: Path, lyrics_path: Path, language: str, audio_mode: str) -> None:
    try:
        config = load_config()
        config["alignment"]["language"] = language
        config["output"]["audio_mode"] = audio_mode
        _set_job(job_id, status="running", stage="Preparing audio", detail="Generation started")

        def report(progress: int, label: str) -> None:
            _set_job(job_id, progress=progress, stage=label, detail=f"Stage progress: {progress}%")

        generate(audio_path, lyrics_path, audio_path.parent / "result", config, progress_callback=report)
    except Exception as exc:
        _set_job(job_id, status="failed", stage="Generation failed", detail="", error=str(exc))
        return
    artifacts = {
        name: f"/jobs/{job_id}/{name}"
        for name in ("karaoke.mp4", "karaoke.ass", "alignment.json")
    }
    _set_job(
        job_id,
        status="complete",
        progress=100,
        stage="Karaoke ready",
        detail="Generation complete",
        artifacts=artifacts,
    )


@app.post("/api/jobs", status_code=202)
def start_job(
    background_tasks: BackgroundTasks,
    audio: UploadFile = File(...),
    lyrics: UploadFile = File(...),
    language: str = Form("auto"),
    audio_mode: str = Form("instrumental"),
) -> dict[str, str]:
    job_id, audio_path, lyrics_path, language = _prepare_job(audio, lyrics, language, audio_mode)
    background_tasks.add_task(_run_job, job_id, audio_path, lyrics_path, language, audio_mode)
    return {"job_id": job_id, "status_url": f"/api/jobs/{job_id}"}


@app.get("/api/jobs/{job_id}")
def job_status(job_id: str) -> dict[str, Any]:
    return _job_snapshot(job_id)


@app.post("/generate", response_class=HTMLResponse)
def generate_job(
    audio: UploadFile = File(...),
    lyrics: UploadFile = File(...),
    language: str = Form("auto"),
    audio_mode: str = Form("instrumental"),
) -> str:
    job_id, audio_path, lyrics_path, language = _prepare_job(audio, lyrics, language, audio_mode)
    _run_job(job_id, audio_path, lyrics_path, language, audio_mode)
    job = _job_snapshot(job_id)
    if job["status"] == "failed":
        raise HTTPException(500, job["error"])
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
