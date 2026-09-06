"""Acoustic metrics on canonical JSON, never on offset-adjusted display times."""
from __future__ import annotations

import math
from collections import Counter


def nearest_rank(values: list[float], quantile: float) -> float:
    if not values:
        return math.inf
    return sorted(values)[max(0, math.ceil(quantile*len(values))-1)]


def _finite(value) -> bool:
    return isinstance(value, (float, int)) and math.isfinite(value)


def _summary(rows: list[dict]) -> dict:
    starts = [r['start_error_ms'] for r in rows]
    ends = [r['end_error_ms'] for r in rows if r['end_unambiguous']]
    signed = [r['signed_start_error_ms'] for r in rows if r['signed_start_error_ms'] is not None]
    return {'words': len(rows), 'valid_starts': sum(math.isfinite(x) for x in starts),
            'coverage': sum(math.isfinite(x) for x in starts)/len(rows) if rows else 0,
            'start_median_ms': nearest_rank(starts, .5), 'start_p90_ms': nearest_rank(starts, .9),
            'start_over_250_ratio': sum(x > 250 for x in starts)/len(rows) if rows else 1,
            'signed_start_median_ms': nearest_rank(signed, .5), 'signed_bias_words': len(signed),
            'end_p90_ms': nearest_rank(ends, .9), 'unambiguous_ends': len(ends),
            'sources': dict(Counter(r['source'] for r in rows)),
            'statuses': dict(Counter(r['status'] for r in rows))}


def evaluate(reference: dict, predictions: dict[str, dict], split: str = 'acceptance') -> dict:
    rows = []
    for song in reference['songs']:
        if song['split'] != split:
            continue
        prediction = predictions.get(song['id'], {})
        words = [word for line in prediction.get('lines', []) for word in line['words']]
        seen = set()
        for fragment in song['fragments']:
            for truth in fragment['words']:
                index = truth['index']
                if not isinstance(index, int) or index < 0:
                    raise ValueError('Reference word index must be a nonnegative integer')
                if index in seen:
                    raise ValueError(f'Duplicate reference index in song {song["id"]}: {index}')
                seen.add(index)
                if not _finite(truth.get('start')) or not _finite(truth.get('end')) or not 0 <= truth['start'] < truth['end']:
                    raise ValueError('Reference requires finite, positive boundaries')
                if not _finite(truth.get('uncertainty_ms')) or truth['uncertainty_ms'] < 0:
                    raise ValueError('Freeze annotation uncertainty_ms before evaluation')
                if not fragment['start'] <= truth['start'] < truth['end'] <= fragment['end']:
                    raise ValueError('Reference word lies outside its fragment')
                word = words[index] if 0 <= index < len(words) else None
                status, start_error, end_error, signed = 'missing_prediction', math.inf, math.inf, None
                if word:
                    start, end = word.get('start'), word.get('end')
                    duration = prediction.get('duration')
                    if word.get('text') != truth['text']:
                        status = 'text_mismatch'
                    elif not (_finite(start) and _finite(end) and _finite(duration) and 0 <= start < end <= duration):
                        status = 'invalid_interval'
                    else:
                        status = 'valid'
                        signed = (start-truth['start'])*1000
                        start_error, end_error = abs(signed), abs(end-truth['end'])*1000
                rows.append({'song': song['id'], 'fragment': fragment['id'], 'index': index,
                    'status': status, 'start_error_ms': start_error, 'end_error_ms': end_error,
                    'signed_start_error_ms': signed, 'end_unambiguous': truth.get('end_unambiguous', True),
                    'uncertainty_ms': truth['uncertainty_ms'], 'cases': truth.get('cases', []),
                    'source': (word.get('timing') or {}).get('source', 'unknown') if word else 'missing'})
    by_song = {song: _summary([r for r in rows if r['song'] == song]) for song in sorted({r['song'] for r in rows})}
    by_case = {case: _summary([r for r in rows if case in r['cases']]) for case in sorted({c for r in rows for c in r['cases']})}
    return {'split': split, 'quantiles': 'nearest-rank', 'overall': _summary(rows),
            'by_song': by_song, 'by_case': by_case, 'words': rows}


def corpus_requirements(reference: dict) -> dict:
    songs = reference['songs']
    ids = [s['id'] for s in songs]
    if len(ids) != len(set(ids)) or any(s['split'] not in {'tuning', 'acceptance'} for s in songs):
        raise ValueError('Song IDs must be unique and split must be tuning or acceptance')
    for song in songs:
        fragment_ids = [f['id'] for f in song['fragments']]
        if len(fragment_ids) != len(set(fragment_ids)):
            raise ValueError('Fragment IDs must be unique within a song')
        for fragment in song['fragments']:
            if not (_finite(fragment.get('start')) and _finite(fragment.get('end'))
                    and 0 <= fragment['start'] < fragment['end']):
                raise ValueError('Reference fragments require finite positive intervals')
    # Validate both groups even when only one is being measured.
    evaluate(reference, {}, 'tuning')
    evaluate(reference, {}, 'acceptance')
    fragments = [f for s in songs for f in s['fragments']]
    acceptance = [s for s in songs if s['split'] == 'acceptance']
    counts = {'songs': len(songs), 'tuning_songs': sum(s['split'] == 'tuning' for s in songs), 'fragments': len(fragments),
              'words': sum(len(f['words']) for f in fragments), 'acceptance_songs': len(acceptance),
              'acceptance_words': sum(w.get('start_unambiguous', False) is True for s in acceptance for f in s['fragments'] for w in f['words'])}
    enough = (counts['songs'] >= 5 and counts['fragments'] >= 15 and counts['words'] >= 500
              and counts['acceptance_songs'] >= 3 and counts['acceptance_words'] >= 300
              and all(20 <= f['end']-f['start'] <= 40 for f in fragments))
    return {**counts, 'sufficient': enough}


def acceptance(candidate: dict, baseline: dict, corpus: dict) -> dict:
    new, old = candidate['overall'], baseline['overall']
    checks = {'median_start': new['start_median_ms'] <= 80, 'p90_start': new['start_p90_ms'] <= 150,
              'large_errors': new['start_over_250_ratio'] <= .02,
              'signed_bias': abs(new['signed_start_median_ms']) <= 40, 'p90_end': new['end_p90_ms'] <= 200}
    old_p90 = old['start_p90_ms']
    checks['relative_improvement'] = (new['start_p90_ms'] <= .7*old_p90 if math.isfinite(old_p90) and old_p90 > 150
                                      else new['start_p90_ms'] <= old_p90)
    checks['per_song_regression'] = all(metrics['start_p90_ms'] <= baseline['by_song'][song]['start_p90_ms']+20
                                      for song, metrics in candidate['by_song'].items())
    return {'status': 'insufficient_corpus' if not corpus['sufficient'] else 'passed' if all(checks.values()) else 'failed',
            'checks': checks, 'corpus': corpus}


def json_metrics(value):
    """JSON null denotes an unbounded error, accompanied by a word error status."""
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, dict):
        return {k: json_metrics(v) for k, v in value.items()}
    if isinstance(value, list):
        return [json_metrics(v) for v in value]
    return value
