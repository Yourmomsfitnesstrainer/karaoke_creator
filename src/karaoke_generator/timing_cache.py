"""Content-addressed ASR and refinement stages, independent of rendering."""
from __future__ import annotations

import hashlib
from dataclasses import asdict
from importlib import metadata
import json
import math
import platform
from pathlib import Path
import time

from .alignment import ASR_ALGORITHM_VERSION, REFINEMENT_ALGORITHM_VERSION, create_backend
from .lyrics import text_sha256
from .models import TimedWord


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as handle:
        for block in iter(lambda: handle.read(1024*1024), b''):
            digest.update(block)
    return digest.hexdigest()


def fingerprint(payload: dict) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True, allow_nan=False).encode()).hexdigest()


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix+'.tmp')
    temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False)+'\n', encoding='utf-8')
    temp.replace(path)


def package_versions() -> dict:
    packages = {}
    for name in ['faster-whisper', 'ctranslate2', 'whisperx', 'torch', 'torchaudio', 'transformers', 'numpy', 'demucs']:
        try:
            packages[name] = metadata.version(name)
        except metadata.PackageNotFoundError:
            packages[name] = None
    return {'python': platform.python_version(), 'platform': platform.platform(), 'packages': packages}


def model_identity(name: str | None, *, asr=False) -> dict:
    """Record a cached HF revision when available; never invent an unknown revision."""
    identity = {'name': name, 'revision': None}
    if not name:
        return identity
    try:
        if asr and '/' not in name:
            from faster_whisper.utils import available_models
            if name in available_models():
                from faster_whisper.utils import _MODELS
                name = _MODELS[name]
        if Path(name).is_dir():
            identity['files'] = {str(p.relative_to(name)): file_sha256(p)
                                 for p in Path(name).rglob('*') if p.is_file()}
        else:
            from huggingface_hub import try_to_load_from_cache
            cached = try_to_load_from_cache(name, 'config.json')
            if isinstance(cached, str):
                identity['revision'] = Path(cached).parent.name
            identity['repository'] = name
    except (ImportError, ValueError, OSError):
        pass
    return identity


def _word_payload(word):
    def safe(value):
        if isinstance(value, float) and not math.isfinite(value):
            return None
        if isinstance(value, dict):
            return {k: safe(v) for k, v in value.items()}
        if isinstance(value, list):
            return [safe(v) for v in value]
        return value
    return safe(asdict(word))


def _read_word(raw):
    return TimedWord(**{**raw, 'start': raw['start'] if raw['start'] is not None else math.nan,
                       'end': raw['end'] if raw['end'] is not None else math.nan})


def cached_timing(audio, document, language, duration, options, work_dir):
    backend_name = str(options.get('backend', 'whisperx'))
    if backend_name not in {'whisperx', 'faster-whisper', 'uniform'}:
        raise ValueError(f'Unknown alignment backend: {backend_name}')
    asr_options = {key: options.get(key, default) for key, default in {
        'model': 'small', 'device': 'auto', 'compute_type': 'int8',
        'use_lyrics_prompt': True, 'vad_filter': True, 'vad_threshold': .30,
        'vad_min_silence_duration_ms': 1000, 'vad_speech_pad_ms': 600}.items()}
    versions = package_versions()
    asr_spec = {'algorithm': ASR_ALGORITHM_VERSION, 'audio_sha256': file_sha256(audio),
                'text_sha256': text_sha256(document) if asr_options['use_lyrics_prompt'] or backend_name == 'uniform' else None,
                'duration': duration, 'language': language, 'options': asr_options,
                'backend': 'uniform' if backend_name == 'uniform' else 'faster-whisper',
                'runtime': {k: v for k, v in versions.items() if k != 'packages'},
                'packages': {p: versions['packages'][p] for p in ['faster-whisper', 'ctranslate2']},
                'model': model_identity(str(asr_options['model']), asr=True)}
    cache = work_dir / 'timing-cache'
    start = time.monotonic()
    asr_key = fingerprint(asr_spec)
    asr_path = cache / f'asr-{asr_key}.json'
    asr_hit = asr_path.exists()
    backend = None
    if asr_hit:
        raw = json.loads(asr_path.read_text(encoding='utf-8'))
        base, detected = [_read_word(word) for word in raw['words']], raw['language']
        actual_runtime = raw.get('actual_runtime', {})
    else:
        backend = create_backend('uniform' if backend_name == 'uniform' else 'faster-whisper',
                                 **asr_options)
        base, detected = backend.transcribe(audio, document, language, duration)
        actual_runtime = getattr(backend, 'runtime', {})
        asr_spec['model'] = model_identity(str(asr_options['model']), asr=True)
        asr_key = fingerprint(asr_spec)
        write_json(cache / f'asr-{asr_key}.json', {'spec': asr_spec, 'language': detected, 'actual_runtime': actual_runtime,
                   'words': [_word_payload(word) for word in base]})
    asr_seconds = time.monotonic()-start
    details = {'asr_key': asr_key, 'asr_cache_hit': asr_hit, 'asr_seconds': asr_seconds,
               'asr': asr_spec, 'versions': versions, 'asr_actual_runtime': actual_runtime}
    if backend_name != 'whisperx':
        details.update(timing_key=asr_key, refinement_cache_hit=None, refinement_seconds=0)
        return base, detected, details
    refiner_options = {k: options.get(k, default) for k, default in {
        'align_models': {}, 'context_seconds': .3, 'max_window_seconds': 20., 'score_thresholds': {}}.items()}
    model_name = refiner_options['align_models'].get(detected)
    if model_name is None:
        from whisperx.alignment import DEFAULT_ALIGN_MODELS_HF, DEFAULT_ALIGN_MODELS_TORCH
        model_name = DEFAULT_ALIGN_MODELS_TORCH.get(detected) or DEFAULT_ALIGN_MODELS_HF.get(detected)
    refine_spec = {'algorithm': REFINEMENT_ALGORITHM_VERSION, 'asr_key': asr_key,
                   'model': model_identity(model_name), 'options': refiner_options,
                   'device': asr_options['device'], 'packages': {p: versions['packages'][p]
                   for p in ['whisperx', 'torch', 'torchaudio', 'transformers', 'numpy']}}
    start = time.monotonic()
    key = fingerprint(refine_spec)
    refine_path = cache / f'refined-{key}.json'
    refine_hit = refine_path.exists()
    if refine_hit:
        raw = json.loads(refine_path.read_text(encoding='utf-8'))
        refined = [_read_word(word) for word in raw['words']]
    else:
        backend = create_backend('whisperx', **asr_options, **refiner_options)
        refined = backend.refine(audio, base, detected, duration)
        refine_spec['model'] = model_identity(model_name)
        key = fingerprint(refine_spec)
        write_json(cache / f'refined-{key}.json', {'spec': refine_spec,
                   'words': [_word_payload(word) for word in refined]})
    details.update(timing_key=key, refinement_cache_hit=refine_hit,
                   refinement_seconds=time.monotonic()-start, refinement=refine_spec)
    return refined, detected, details
