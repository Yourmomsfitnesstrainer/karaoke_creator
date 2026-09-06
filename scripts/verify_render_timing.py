#!/usr/bin/env python3
"""Save synthetic B0/B1/B2 frames/audio measurements using an explicit Git baseline."""
import argparse
import json
from pathlib import Path
import runpy
import subprocess
import types

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--baseline-ref', default='350088c')
parser.add_argument('--output', type=Path, default=Path('output/timing-verification'))
args = parser.parse_args()
root = Path(__file__).resolve().parents[1]
measure = runpy.run_path(str(root/'tests/test_media_timing.py'))['measure_clip']


def original_module(name):
    source = subprocess.check_output(['git', 'show', f'{args.baseline_ref}:src/karaoke_generator/{name}.py'], cwd=root, text=True)
    module = types.ModuleType(f'karaoke_generator.baseline_{name}')
    exec(compile(source, f'{args.baseline_ref}:{name}.py', 'exec'), module.__dict__)
    return module


legacy_ass, legacy_video = original_module('subtitles'), original_module('renderer')
output = args.output.resolve()
reports = {}
for label, offset in [('B0', -250), ('B1', 0)]:
    reports[label] = measure(output/label, offset, legacy_ass.generate_ass, legacy_video.render_video)
for label, offset in [('B2', 0), ('B2-negative', -250), ('B2-positive', 250)]:
    reports[label] = measure(output/label, offset)
report = {'baseline_ref': args.baseline_ref, 'fixture': '3 canonical intervals and broadband audio markers',
          'variants': reports, 'C': {'status': 'not_measured', 'reason': 'no annotated vocal corpus'},
          'scope': 'render transport only; B0/B1/B2 share identical acoustic JSON boundaries'}
(output/'report.json').write_text(json.dumps(report, indent=2)+'\n')
print(json.dumps(report, indent=2))
