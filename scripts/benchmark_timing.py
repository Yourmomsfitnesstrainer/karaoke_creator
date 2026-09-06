#!/usr/bin/env python3
"""Compare refiners on frozen local ASR evidence; no ASR or separation runs here."""
from __future__ import annotations

import argparse
from copy import deepcopy
import json
from pathlib import Path
import time

from karaoke_generator.alignment import WhisperXBackend, align_lyrics, REFINEMENT_ALGORITHM_VERSION, MAPPING_ALGORITHM_VERSION
from karaoke_generator.audio import probe_duration
from karaoke_generator.evaluation import acceptance, corpus_requirements, evaluate, json_metrics
from karaoke_generator.lyrics import parse_lyrics_file, text_sha256
from karaoke_generator.models import TimedWord
from karaoke_generator.timing_cache import file_sha256, fingerprint, model_identity, package_versions, write_json


def run(manifest: Path, output: Path, models: list[str], contexts: list[float], split: str,
        score_thresholds: dict | None = None):
    reference = json.loads(manifest.read_text(encoding='utf-8'))
    corpus = corpus_requirements(reference)
    root = manifest.resolve().parent
    output.mkdir(parents=True, exist_ok=True)
    versions = package_versions()
    frozen = []
    baselines = {}
    for song in reference['songs']:
        if song['split'] != split:
            continue
        audio, lyrics, asr = (root / song[key] for key in ['analysis_audio', 'lyrics', 'asr_json'])
        document = parse_lyrics_file(lyrics)
        raw = json.loads(asr.read_text(encoding='utf-8'))
        audio_hash = file_sha256(audio)
        if raw['spec']['audio_sha256'] != audio_hash:
            raise ValueError(f'Frozen ASR audio does not match song {song["id"]}')
        if raw['spec'].get('text_sha256') not in (None, text_sha256(document)):
            raise ValueError('Frozen ASR prompt differs from current lyrics')
        words = [TimedWord(**{**w, 'start': w['start'] if w['start'] is not None else float('nan'),
                             'end': w['end'] if w['end'] is not None else float('nan')}) for w in raw['words']]
        duration = probe_duration(audio)
        frozen.append((song, audio, document, words, raw['language'], duration,
                       {'asr_sha256': file_sha256(asr), 'audio_sha256': audio_hash,
                        'text_sha256': text_sha256(document)}))
        if song.get('baseline_alignment'):
            baseline = json.loads((root/song['baseline_alignment']).read_text(encoding='utf-8'))
            if song.get('baseline_asr_sha256') != file_sha256(asr):
                raise ValueError('Declare baseline_asr_sha256 to tie the baseline to the frozen ASR file')
            if baseline.get('source_text_sha256') != text_sha256(document) or abs(baseline['duration']-duration) > .01:
                raise ValueError('Baseline text or duration differs from frozen inputs')
            baselines[song['id']] = baseline
    if not frozen:
        raise ValueError(f'No songs assigned to {split}')
    baseline = evaluate(reference, baselines, split) if len(baselines) == len(frozen) else None
    report = {'corpus_sha256': file_sha256(manifest), 'split': split, 'versions': versions,
              'corpus': corpus, 'baseline': baseline, 'variants': {},
              'note': 'Acoustic JSON metrics only. Null errors are infinite internally. No render offset is applied.'}
    for model in models:
        for context in contexts:
            label = f'{model}:context={context}'
            predictions, evidence = {}, []
            for song, audio, document, words, language, duration, hashes in frozen:
                settings = {'model': model_identity(model), 'context_seconds': context,
                            'score_thresholds': score_thresholds or {}, 'max_window_seconds': 20,
                            'algorithm': REFINEMENT_ALGORITHM_VERSION, 'mapping_algorithm': MAPPING_ALGORITHM_VERSION, 'device': 'cpu',
                            'versions': versions, **hashes}
                path = output / f'{fingerprint(settings)}.alignment.json'
                start = time.monotonic()
                hit = path.exists()
                if hit:
                    payload = json.loads(path.read_text(encoding='utf-8'))
                else:
                    backend = WhisperXBackend(device='cpu', align_models={language: model},
                        context_seconds=context, score_thresholds=score_thresholds)
                    refined = backend.refine(audio, deepcopy(words), language, duration)
                    payload = align_lyrics(document, refined, duration, language, 'whisperx').to_dict()
                    settings['model'] = model_identity(model)
                    payload['diagnostics']['benchmark'] = settings
                    path = output / f'{fingerprint(settings)}.alignment.json'
                    write_json(path, payload)
                predictions[song['id']] = payload
                evidence.append({'song': song['id'], 'settings': settings, 'cache_hit': hit,
                                 'seconds': time.monotonic()-start, 'alignment': path.name})
            metrics = evaluate(reference, predictions, split)
            report['variants'][label] = {'metrics': metrics, 'runs': evidence,
                'acceptance': acceptance(metrics, baseline, corpus) if baseline and split == 'acceptance'
                else {'status': 'not_run', 'reason': 'tuning_split_or_missing_baseline'}}
    write_json(output/'report.json', json_metrics(report))
    return output/'report.json'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--corpus', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--split', choices=['tuning', 'acceptance'], default='tuning')
    parser.add_argument('--models', nargs='+', default=['bond005/wav2vec2-base-ru',
                        'jonatasgrosman/wav2vec2-large-xlsr-53-russian'])
    parser.add_argument('--contexts', nargs='+', type=float, default=[0, .3, .6])
    parser.add_argument('--score-thresholds', type=Path, help='Frozen JSON mapping of model IDs to thresholds')
    args = parser.parse_args()
    thresholds = json.loads(args.score_thresholds.read_text()) if args.score_thresholds else None
    print(run(args.corpus, args.output, args.models, args.contexts, args.split, thresholds))


if __name__ == '__main__':
    main()
