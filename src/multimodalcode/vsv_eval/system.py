"""Run the existing six-metric evaluator from one frozen experiment manifest."""
from __future__ import annotations

import csv
import hashlib
import re
from datetime import datetime, timezone
from pathlib import Path

from multimodalcode.io import read_json, write_json
from .catalogue import load_catalogue
from .episodes import extract_verification_rounds
from .evaluation import (build_packet, evaluate_rounds, finalize_evaluation, load_rounds,
                         round_cutoffs, unassessed_repairs)
from .judge import build_client, load_judge_config, write_model_call_report
from .metrics import check_validity, proportion, PROTOCOL_VERSION
from .states import evaluate_states, load_repair_results
from .preparation import load_acceptance_binding, build_transitions, prepare_case_acceptance


METRICS = ('VC', 'CV', 'BDA', 'RS', 'CP', 'VCS')
PATH_FIELDS = ('task_root', 'catalogue', 'rounds_json', 'run_json', 'state_spec',
               'repair_results', 'check_results', 'reference_map', 'reconstructed_images', 'acceptance_binding')


def file_hash(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def load_experiment(path):
    path = Path(path).resolve()
    value = read_json(path)
    allowed = {'config', 'primary_profile', 'allow_draft', 'workers', 'port', 'cases'}
    if not isinstance(value, dict) or set(value) - allowed:
        raise ValueError('Unknown experiment fields')
    if not isinstance(value.get('cases'), list) or not value['cases']:
        raise ValueError('An experiment requires at least one case')
    value = {**value, 'config': str((path.parent / value['config']).resolve())}
    for name, default in (('workers', 1), ('port', 18951)):
        value.setdefault(name, default)
        if type(value[name]) is not int or value[name] < 1:
            raise ValueError(f'{name} must be a positive integer')
    ids = set()
    cases = []
    for case in value['cases']:
        if set(case) - {'id', 'model', 'benchmark', *PATH_FIELDS}:
            raise ValueError('Unknown case fields')
        from .benchmarks import BENCHMARKS
        if case.get('benchmark', 'vision2web') not in BENCHMARKS:
            raise ValueError('Unsupported benchmark')
        cid = case.get('id', '')
        if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]*', cid) or cid in ids:
            raise ValueError('Case IDs must be unique safe directory names')
        ids.add(cid)
        if bool(case.get('rounds_json')) == bool(case.get('run_json')):
            raise ValueError('Supply exactly one of rounds_json and run_json')
        if sum(bool(case.get(k)) for k in ('state_spec', 'repair_results', 'acceptance_binding')) > 1:
            raise ValueError('Supply only one acceptance_binding, state_spec or repair_results')
        if case.get('check_results') and not case.get('rounds_json'):
            raise ValueError('Stored check results require their original rounds_json')
        if not case.get('task_root') or not case.get('catalogue'):
            raise ValueError('Each case requires task_root and a frozen catalogue')
        cases.append({**case, **{k: str((path.parent / case[k]).resolve())
                                for k in PATH_FIELDS if case.get(k)}})
    return {**value, 'cases': cases}


def preflight(case, experiment, output):
    """Inspect evidence and acceptance bindings before spending any API budget."""
    issues, warnings = [], []
    missing_fields = set()
    for key in PATH_FIELDS:
        if case.get(key) and not Path(case[key]).exists():
            issues.append(f'Missing {key}: {case[key]}')
            missing_fields.add(key)
    if missing_fields & {'task_root', 'catalogue', 'rounds_json', 'run_json'}:
        return {'issues': issues, 'warnings': warnings}
    catalogue = load_catalogue(case['catalogue'], allow_draft=experiment.get('allow_draft', False))
    for rid, path in catalogue['references'].items():
        if not Path(path).is_file():
            issues.append(f'Missing reference image: {rid}')
    task = (Path(case['task_root']) / 'prompt.txt').read_text()
    source_metadata = Path(case['task_root']) / 'source.json'
    if source_metadata.is_file() and read_json(source_metadata).get('missing_references'):
        issues.append('Task reference images are missing; recover them before freezing the catalogue')
    if hashlib.sha256(task.encode()).hexdigest() != catalogue['task_sha256']:
        raise ValueError('Catalogue and task input differ')
    if not case.get('rounds_json'):
        warnings.append('Image and repair-link validation will run immediately after extraction')
        return {'issues': issues, 'warnings': warnings}
    rounds = load_rounds(case['rounds_json'])
    if any(not ep.get('relation_annotation_complete') for ep in rounds['episodes']):
        issues.append('Round relation annotations are incomplete')
    repairs = []
    if case.get('repair_results') and 'repair_results' not in missing_fields:
        repairs = load_repair_results(case['repair_results'], case['rounds_json'], case['catalogue'])
    if case.get('state_spec') and 'state_spec' not in missing_fields:
        spec = read_json(case['state_spec'])
        if spec.get('runtime', {}).get('kind') == 'command':
            from .artifact_acceptance import validate_assignments
            validate_assignments(spec.get('acceptance_workflows', []), spec['runtime'],
                                 {**catalogue, 'checks': catalogue['checks'] + spec.get('repair_checks', []),
                                  'unresolved_check_ids': spec.get('unresolved_check_ids', [])})
        for key in ('fixture', 'runtime', 'transitions'):
            if key not in spec:
                issues.append(f'State specification is missing {key}')
        if spec.get('fixture') and not Path(spec['fixture']).is_dir():
            issues.append('State fixture is unavailable')
        repairs = spec.get('transitions', [])
    if case.get('acceptance_binding') and 'acceptance_binding' not in missing_fields:
        try:
            binding = load_acceptance_binding(case['acceptance_binding'], rounds)
            repairs = build_transitions(rounds, read_json(binding['version_manifest']))
        except (OSError, ValueError, KeyError) as error:
            issues.append(f'Acceptance binding is not ready: {error}')
    gaps = unassessed_repairs(rounds, repairs)
    if gaps:
        issues.append(f'{len(gaps)} repair groups lack acceptance bindings; supply state_spec or repair_results')
    if case.get('reference_map') and 'reference_map' not in missing_fields:
        mapping = read_json(case['reference_map'])['mapping']
        if set(mapping) != {e['episode_id'] for e in rounds['episodes']}:
            issues.append('Reference mapping does not cover exactly these rounds')
        if any(not isinstance(refs, list) or any(r not in catalogue['references'] for r in refs)
               for refs in mapping.values()):
            issues.append('Reference mapping contains unknown references')
    reconstructed = None
    if case.get('reconstructed_images') and 'reconstructed_images' not in missing_fields:
        from .archive_replay import load_reconstructed_images
        reconstructed = load_reconstructed_images(case['reconstructed_images'], rounds)
        warnings.append('Reconstructed pixels are not verified historical originals')
    missing = set()
    for ep in rounds['episodes']:
        packet, _ = build_packet(rounds, ep, max(round_cutoffs(rounds, ep)), task, catalogue,
                                 [], output, reconstructed_images=reconstructed)
        missing.update(row['event_id'] for row in packet['missing_images']
                       if row['role'] == 'agent_observation')
    if missing:
        issues.append('Missing recorded observation images at events: ' + ', '.join(map(str, sorted(missing))))
    return {'issues': issues, 'warnings': warnings, 'round_count': len(rounds['episodes']),
            'unbound_repair_groups': gaps}


def metric_record(metric, row):
    """Describe existing arithmetic; never turn infrastructure gaps into scores."""
    unknown = row.get('unknown_count', 0)
    if unknown or row.get('unassessed_episode_count') or row.get('unassessed_transition_count'):
        status = 'unresolved'
    elif row.get('score') is not None:
        status = 'scored'
    else:
        status = 'not_applicable'
    return {**row, 'assessment_status': status}


def verify_complete(result, rounds_path):
    rounds = load_rounds(rounds_path)
    expected = [e['episode_id'] for e in rounds['episodes']]
    actual = [e['episode_id'] for e in result['episodes']]
    if actual != expected or any(e['status'] not in {'evaluated', 'excluded'} for e in result['episodes']):
        raise ValueError('Final result omits, duplicates, reorders, or leaves pending rounds')
    if result['repair_gaps']:
        raise ValueError('Final result has unassessed repair groups')


def run_case(case, experiment, output):
    output = Path(output)
    config = load_judge_config(experiment['config'])
    common = {'primary_profile': experiment.get('primary_profile'),
              'allow_draft': experiment.get('allow_draft', False), 'workers': experiment['workers']}
    if not case.get('rounds_json'):
        judge = build_client(config, experiment.get('primary_profile') or
                             config['primary_stage_profiles']['verification_annotation'],
                             output / 'extraction/judge_cache')
        rounds = extract_verification_rounds(case['run_json'], judge)
        rounds_path = output / 'extraction/verification_rounds.json'
        write_json(rounds_path, rounds)
        case = {**case, 'rounds_json': str(rounds_path)}
        check = preflight(case, experiment, output / 'preflight')
        write_json(output / 'preflight.json', check)
        if check['issues']:
            return {'status': 'blocked', **check}
    checks_path = case.get('check_results')
    if checks_path:
        saved = read_json(checks_path)
        if Path(saved['source_rounds']).resolve() != Path(case['rounds_json']).resolve():
            raise ValueError('Stored scores belong to different rounds')
        if Path(saved['catalogue']).resolve() != Path(case['catalogue']).resolve():
            raise ValueError('Stored scores use a different catalogue')
        original_index = saved.get('reconstructed_index')
        if bool(original_index) != bool(case.get('reconstructed_images')) or (
                original_index and Path(original_index).resolve() != Path(case['reconstructed_images']).resolve()):
            raise ValueError('Stored scores use a different evidence mode or image index')
    else:
        evaluate_rounds(case['rounds_json'], case['catalogue'], case['task_root'], experiment['config'],
                        output / 'checks', reference_map=case.get('reference_map'),
                        reconstructed_index=case.get('reconstructed_images'), **common)
        checks_path = output / 'checks/scores.json'
    if case.get('acceptance_binding') and any(e['repair_links'] for e in load_rounds(case['rounds_json'])['episodes']):
        prepared = prepare_case_acceptance(case['rounds_json'], case['catalogue'], checks_path,
                    case['acceptance_binding'], case['task_root'], experiment['config'], output / 'acceptance',
                    primary_profile=experiment.get('primary_profile'))
        case = {**case, 'state_spec': str(prepared)}
    repair_path = case.get('repair_results')
    if case.get('state_spec'):
        evaluate_states(case['rounds_json'], case['catalogue'], case['state_spec'], experiment['config'],
                        output / 'states', port=experiment['port'], **common)
        repair_path = output / 'states/repair_results.json'
    result = finalize_evaluation(checks_path, output / 'final', repair_path)
    verify_complete(result, case['rounds_json'])
    metrics = dict(result['metrics'])
    for suffix, visual in (('V', True), ('T', False)):
        metrics[f'CV-{suffix}'] = proportion(
            check_validity(t['method_ok'], t['evidence_ok'])
            for ep in result['episodes'] for t in ep['targets']
            if t.get('check_attempted', True) and (t['modality'] != 'text') == visual)
    metrics = {k: metric_record(k, v) for k, v in metrics.items()}
    unresolved = any(m['assessment_status'] == 'unresolved' for m in metrics.values())
    return {'status': 'completed_with_unresolved_evidence' if unresolved else 'completed',
            'scores': str((output / 'final/scores.json').resolve()), 'metrics': metrics,
            'round_count': len(result['episodes']), 'evidence_mode': result.get('evidence_mode', 'original'),
            'protocol_status': result['status'], 'stored_checks_reused': bool(case.get('check_results'))}


def display_metric(row):
    if not row:
        return 'not_run'
    if row['assessment_status'] == 'unresolved':
        lo, hi = row.get('lower_bound'), row.get('upper_bound')
        return f'{lo:.2f}–{hi:.2f}% (unresolved)' if lo is not None and hi is not None else 'unresolved'
    return f"{row['score']:.2f}%" if row.get('score') is not None else row['assessment_status']


def write_summary(rows, output):
    write_json(output / 'summary.json', {'cases': rows})
    breakdowns = {
        'VC-visual-requirements': ('VC', 'by_requirement_type', 'visual'),
        'VC-interactive-requirements': ('VC', 'by_requirement_type', 'interactive'),
        'BDA-visible-defects': ('BDA-V', 'error'),
        'RS-diagnosed-visual': ('RS', 'diagnosed_visual'),
        'Repair-regression-rate': ('CP', 'repair_outcomes', 'regression_rate'),
    }
    columns = ('id', 'benchmark', 'model', 'status', *METRICS, 'CV-V', 'CV-T', 'BDA-V', 'BDA-T', *breakdowns)
    flat = []
    for r in rows:
        metrics = dict(r.get('metrics', {}))
        for label, keys in breakdowns.items():
            value = metrics
            for key in keys:
                value = value.get(key, {})
            if value:
                metrics[label] = metric_record(label, value)
                if value.get('classification_complete') is False:
                    metrics[label]['assessment_status'] = 'unclassified'
        flat.append({**{k: r.get(k, '') for k in columns[:4]},
                     **{k: display_metric(metrics.get(k)) for k in columns[4:]}})
    with (output / 'comparison.csv').open('w', newline='') as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        writer.writerows(flat)
    lines = ['# Evaluation results', '',
             'Requirement coverage is separate from evidence modality. Repair-regression-rate is a failure frequency (lower is better).',
             'Breakdowns reuse existing judgments; unresolved evidence is not a model failure.', '',
             '| ' + ' | '.join(columns) + ' |', '| ' + ' | '.join(['---'] * len(columns)) + ' |']
    lines += ['| ' + ' | '.join(str(r[k]).replace('|', '\\|') for k in columns) + ' |' for r in flat]
    for row in rows:
        for issue in row.get('issues', []):
            lines += ['', f"- {row['id']}: {issue}"]
    (output / 'comparison.md').write_text('\n'.join(lines) + '\n')


def failure_record(exc):
    """Keep errors actionable without storing environment credentials."""
    import os
    message = str(exc)
    for name, value in os.environ.items():
        if value and ('KEY' in name or 'TOKEN' in name or 'SECRET' in name):
            message = message.replace(value, '[redacted]')
    return {'status': 'failed', 'issues': [f'{type(exc).__name__}: {message}'],
            'error_type': type(exc).__name__}


def run_experiment(manifest, output, *, check_only=False):
    experiment = load_experiment(manifest)
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    # Freeze inputs, not credentials or expanded model configuration.
    hashes = {experiment['config']: file_hash(experiment['config'])}
    for case in experiment['cases']:
        for key in PATH_FIELDS:
            if case.get(key) and Path(case[key]).is_file():
                hashes[case[key]] = file_hash(case[key])
        if case.get('acceptance_binding'):
            binding_path = Path(case['acceptance_binding'])
            binding = read_json(binding_path)
            for raw in (binding['version_manifest'], str(Path(binding['fixture']) / 'trajectory/run.json')):
                source = (binding_path.parent / raw).resolve()
                if source.is_file():
                    hashes[str(source)] = file_hash(source)
        task = Path(case['task_root']) / 'prompt.txt'
        if task.is_file():
            hashes[str(task)] = file_hash(task)
    frozen = {'experiment': experiment, 'input_sha256': hashes, 'protocol_version': PROTOCOL_VERSION}
    lock = output / 'experiment.json'
    if lock.exists() and read_json(lock) != frozen:
        raise ValueError('Experiment inputs changed; use a fresh output directory')
    write_json(lock, frozen)
    started = datetime.now(timezone.utc).isoformat()
    rows = []
    # Inspect the whole batch before starting its first model call.
    for case in experiment['cases']:
        directory = output / case['id']
        try:
            check = preflight(case, experiment, directory / 'preflight')
            write_json(directory / 'preflight.json', check)
            result = {'status': 'blocked' if check['issues'] else 'ready', **check}
        except Exception as exc:
            result = failure_record(exc)
        row = {'id': case['id'], 'benchmark': case.get('benchmark', 'vision2web'), 'model': case.get('model', ''), **result}
        rows.append(row)
        write_json(directory / 'status.json', row)
        print(f"{case['id']}: preflight {row['status']}", flush=True)
    write_summary(rows, output)
    if not check_only:
        for index, (case, row) in enumerate(zip(experiment['cases'], rows)):
            if row['status'] != 'ready':
                continue
            directory = output / case['id']
            rows[index] = {**row, 'status': 'running'}
            write_json(directory / 'status.json', rows[index])
            write_summary(rows, output)
            try:
                result = run_case(case, experiment, directory)
                result['warnings'] = row['warnings']
            except Exception as exc:
                result = failure_record(exc)
            rows[index] = {'id': case['id'], 'benchmark': case.get('benchmark', 'vision2web'), 'model': case.get('model', ''), **result}
            write_json(directory / 'status.json', rows[index])
            write_summary(rows, output)
            print(f"{case['id']}: {result['status']}", flush=True)
    write_model_call_report(output, since=started)
    good = {'ready'} if check_only else {'completed'}
    return 0 if all(r['status'] in good for r in rows) else 2
