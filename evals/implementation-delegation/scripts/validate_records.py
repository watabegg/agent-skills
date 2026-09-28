#!/usr/bin/env python3
"""Validate curated records and public artifact integrity; never launch models."""
import argparse
import hashlib
import json
import math
from pathlib import Path


def check(condition, message):
    if not condition:
        raise ValueError(message)


def read_json(path):
    return json.loads(path.read_text(encoding='utf-8'))


def safe_child(root, relative):
    check(not Path(relative).is_absolute(), f'Absolute artifact path: {relative}')
    child = root / relative
    check(not child.is_symlink(), f'Symlink: {child}')
    check(child.resolve().is_relative_to(root.resolve()), f'Escaping path: {relative}')
    return child


def validate(root):
    base = root / 'evals' / 'implementation-delegation'
    check(base.is_dir(), 'Pass the repository root, not the eval directory.')
    tasks = list((base / 'tasks').glob('*/task.json'))
    check(bool(tasks), 'No task packages found')
    for path in tasks:
        task = read_json(path)
        check(task['id'] == path.parent.name, f'Task ID: {path}')
        check(task['public_reproducibility'] in {'runnable', 'not_runnable'}, f'Task status: {path}')
        check(bool(task['artifacts']), f'No artifact hashes: {path}')
        for name, expected in task['artifacts'].items():
            artifact = safe_child(path.parent, name)
            check(artifact.is_file(), f'Missing artifact: {artifact}')
            check(hashlib.sha256(artifact.read_bytes()).hexdigest() == expected,
                  f'Hash mismatch: {artifact}')
        if task['public_reproducibility'] == 'runnable':
            check((path.parent / 'SPEC.md').is_file(), f'Missing contract: {path}')
            check(bool(list((path.parent / 'tests' / 'evaluator').glob('*.py'))),
                  f'Missing evaluator: {path}')
    total = 0
    by_study = {}
    for path in sorted((base / 'studies').glob('*/manifest.json')):
        manifest = read_json(path)
        sid = manifest['id']
        check(sid == path.parent.name, f'Study ID: {path}')
        catalog = {t['id'] for t in manifest['task_catalog']}
        for task in manifest['task_catalog']:
            if task['package']:
                package = (path.parent / task['package']).resolve()
                check(package.is_relative_to(base.resolve()), f'Package path escapes archive: {sid}')
                check((package / 'task.json').is_file(), f'Missing task package: {task}')
        records_path = safe_child(path.parent, manifest['results'])
        records = [json.loads(line) for line in records_path.read_text().splitlines() if line.strip()]
        check(bool(records), f'No records: {sid}')
        ids = set()
        for row in records:
            check(row['schema_version'] == manifest['schema_version'] == 1, f'Schema: {sid}')
            check(row['study_id'] == sid and row['task_id'] in catalog, f'Reference: {sid}')
            check(row['trial_id'] not in ids, f'Duplicate trial: {sid}')
            ids.add(row['trial_id'])
            for key in ['model_requested', 'model_observed', 'effort_requested', 'effort_observed',
                        'evidence_level', 'public_reproducibility', 'status', 'status_reason']:
                check(key in row, f'Missing {key}: {sid}')
            passed, count = row['objective_passed'], row['objective_total']
            check((passed is None and count is None) or
                  (isinstance(passed, int) and isinstance(count, int) and 0 <= passed <= count),
                  f'Invalid test counts: {sid}/{row["trial_id"]}')
            sec = row['solver_seconds']
            check(sec is None or (math.isfinite(sec) and sec >= 0), f'Invalid time: {sid}')
            if row.get('total_score') is not None:
                scores = row['subjective_scores']
                check(row['total_score'] == row['correctness_points'] + scores['maintainability']
                      + scores['submitted_test_quality'], f'Score arithmetic: {sid}')
            u = row.get('usage') or {}
            check(all(isinstance(v, int) and v >= 0 for v in u.values()), f'Invalid usage: {sid}')
            if 'input_tokens' in u:
                check(u.get('cached_input_tokens', 0) <= u['input_tokens'], f'Cached subset: {sid}')
                if 'total_tokens' in u:
                    check(u['total_tokens'] == u['input_tokens'] + u['output_tokens'], f'Usage sum: {sid}')
            if 'output_tokens' in u:
                check(u.get('reasoning_output_tokens', 0) <= u['output_tokens'], f'Reasoning subset: {sid}')
            c = row.get('cost')
            if c:
                check(c['type'] == 'historical_api_equivalent_estimate', f'Unlabeled estimate: {sid}')
                check(c['actual_billed_usd'] is None, f'Unexpected actual bill claim: {sid}')
                check(math.isfinite(c['usd']) and c['usd'] >= 0, f'Invalid cost: {sid}')
                if 'breakdown_usd' in c:
                    check(math.isclose(sum(c['breakdown_usd'].values()), c['usd'], abs_tol=.000001), f'Cost sum: {sid}')
                if sid == 'repository-replay':
                    rate = c['rates_usd_per_million']['gpt-6-luna']
                    estimate = ((u['input_tokens'] - u['cached_input_tokens']) * rate['uncached_input']
                                + u['cached_input_tokens'] * rate['cached_input']
                                + u['cache_write_input_tokens'] * rate['cache_write_input']
                                + u['output_tokens'] * rate['output']) / 1_000_000
                    check(math.isclose(estimate, c['usd'], abs_tol=1e-8), f'Cost formula: {sid}')
        by_study[sid] = records
        total += len(records)
        print(f'{sid}: {len(records)} valid records')
    accounting = read_json(base / 'studies' / 'repository-replay' / 'accounting.json')
    check(math.isclose(sum(accounting['parent_by_role_usd'].values()), accounting['parent_usd'], abs_tol=1e-6), 'Parent accounting sum')
    workers = sum(r['cost']['usd'] for r in by_study['repository-replay'])
    check(math.isclose(workers, accounting['all_nine_workers_usd'], abs_tol=1e-8), 'Worker accounting sum')
    check(math.isclose(workers + accounting['parent_usd'], accounting['known_subtotal_usd'], abs_tol=1e-6), 'Subtotal accounting sum')
    print(f'OK: {len(tasks)} task manifests and {total} historical trials; no model runs.')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('repo_root', nargs='?', type=Path, default=Path(__file__).resolve().parents[3])
    args = parser.parse_args()
    try:
        validate(args.repo_root.resolve())
    except (ValueError, KeyError, OSError, TypeError) as error:
        parser.exit(1, f'Validation failed: {error}\n')
