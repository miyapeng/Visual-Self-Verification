"""Evaluate target checks in the current, referenced verification rounds."""
from __future__ import annotations

import hashlib
import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from multimodalcode.io import read_json, write_json
from .catalogue import content_hash, image_views, load_catalogue
from .episodes import _resolve_image
from .judge import build_client, load_judge_config, rows_schema
from .metrics import summarize, findings, check_validity, METRICS, PROTOCOL_VERSION
from .repair_pilot import assess_output
from .check_stages import (judge_round_prefix, validate_stage, validate_observations,
                           metric_packet, merge_stages, stage_packet)
from .scope import locate_scope, scoped_rounds, validate_saved_scope
from .checks import _call_id, _results


def load_rounds(path):
    rounds = read_json(path)
    if rounds.get('schema') != 'multimodalcode-verification-rounds-1':
        raise ValueError('Use the current verification_rounds.json extraction, not legacy episodes')
    source = Path(rounds['source_run'])
    if hashlib.sha256(source.read_bytes()).hexdigest() != rounds['source_run_sha256']:
        raise ValueError('The source trajectory no longer matches extraction')
    original = read_json(source)['timeline']
    if rounds['events'] != original:
        raise ValueError('Extracted events differ from the original trajectory')
    ids = [e['ordinal'] for e in original]
    if len(ids) != len(set(ids)) or ids != sorted(ids):
        raise ValueError('Trajectory event IDs must be unique and ordered')
    episode_ids = [e['episode_id'] for e in rounds['episodes']]
    if len(episode_ids) != len(set(episode_ids)):
        raise ValueError('Duplicate episode IDs')
    for episode in rounds['episodes']:
        for field in ('core_event_ids', 'context_event_ids', 'judgment_event_ids'):
            if any(eid not in ids for eid in episode[field]):
                raise ValueError('An episode cites nonexistent events')
        if not episode['core_event_ids'] or not set(episode['judgment_event_ids']) <= set(episode['core_event_ids']):
            raise ValueError('Invalid core or diagnosis references')
    return rounds


def reference_mapping(rounds, catalogue, judge):
    """Fill only missing reference associations; do not reannotate existing boundaries."""
    packet = {'checks': catalogue['checks'], 'references': list(catalogue['references']),
              'episodes': [{'episode_id': e['episode_id'], 'core_event_ids': e['core_event_ids'],
                            'judgment_event_ids': e['judgment_event_ids']} for e in rounds['episodes']],
              'events': [e for e in rounds['events'] if e['ordinal'] in
                         {eid for ep in rounds['episodes'] for eid in ep['core_event_ids']} ]}
    prompt = """Associate supplied check rounds with relevant task reference images. This fills missing reference associations only.
Do not change boundaries, judgments, or repair links. Do not evaluate correctness.
For text-only runtime/HTTP/console/behavioral checks, reference_ids can be empty. Keep all episode IDs exactly once.
Use only existing episode IDs and reference IDs. Return only {"episodes":[{"episode_id":"...","reference_ids":[]}]}.
EVIDENCE:
""" + json.dumps(packet, ensure_ascii=False)
    record = judge.judge('annotation', prompt, [],
                         context={'episode_ids': [e['episode_id'] for e in rounds['episodes']]})
    if record.get('error'):
        raise ValueError(record['error'])
    value = record['parsed']
    if set(value) != {'episodes'} or not isinstance(value['episodes'], list):
        raise ValueError('Invalid reference annotation')
    mapping = {}
    allowed = {e['episode_id'] for e in rounds['episodes']}
    for row in value['episodes']:
        if set(row) != {'episode_id', 'reference_ids'} or row['episode_id'] not in allowed or row['episode_id'] in mapping:
            raise ValueError('Invalid or duplicate reference association')
        refs = row['reference_ids']
        if not isinstance(refs, list) or any(not isinstance(r, str) or r not in catalogue['references'] for r in refs):
            raise ValueError('Unknown reference ID')
        mapping[row['episode_id']] = refs
    if set(mapping) != allowed:
        raise ValueError('Reference annotation omitted episodes')
    return {'mapping': mapping, 'judge_request_sha256': record['request_sha256']}


def trailing_responses(rounds, episode):
    """Keep the direct response before the next operation, even across round labels."""
    events = rounds['events']
    end = max(episode['core_event_ids'])
    index = next(i for i, event in enumerate(events) if event['ordinal'] == end)
    anchor = events[index]
    if anchor['kind'] not in {'observation', 'model_text', 'reasoning'}:
        return []
    responses = []
    for event in events[index + 1:]:
        if (event['kind'] not in {'model_text', 'reasoning'}
                or any(event.get(k) != anchor.get(k) for k in ('attempt', 'scope', 'parent_tool_use_id'))):
            break
        responses.append(event['ordinal'])
    return responses


def round_cutoffs(rounds, episode):
    judgments = episode['judgment_event_ids'] + trailing_responses(rounds, episode)
    return sorted(set(judgments or [max(episode['core_event_ids'])]))


def build_packet(rounds, episode, cutoff, task, catalogue, reference_ids, output, assertions=(), *, attach_images=True, reconstructed_images=None):
    events = {e['ordinal']: e for e in rounds['events']}
    selected = {eid for eid in episode['core_event_ids'] + episode['context_event_ids'] if eid <= cutoff}
    responses = [eid for eid in trailing_responses(rounds, episode) if eid <= cutoff]
    selected.update(responses)
    # The preceding response can contain setup or a longer plan shared by several rounds.
    timeline = [e for e in rounds['events'] if e['ordinal'] <= cutoff]
    first_action = next((i for i, e in enumerate(timeline)
                         if e['ordinal'] in episode['core_event_ids'] and e['kind'] == 'action'), None)
    if first_action is not None:
        for row in reversed(timeline[:first_action]):
            if row['kind'] not in {'model_text', 'reasoning'}:
                break
            selected.add(row['ordinal'])
    states = {s['event_id']: s for ep in rounds['episodes'] for s in ep['evidence_states']}
    # Include a referenced producer and its ID-paired result, without inserting intervening events.
    producers = {states[eid].get('producer_event_id') for eid in selected if eid in states}
    selected.update(eid for eid in producers if eid is not None and eid <= cutoff)
    calls = {_call_id(events[eid]) for eid in selected if events[eid]['kind'] == 'observation'} - {None}
    selected.update(e['ordinal'] for e in timeline if e['kind'] == 'action' and _call_id(e) in calls)
    for index, row in enumerate(timeline):
        if row['ordinal'] in selected and row['kind'] == 'action':
            selected.update(e['ordinal'] for e in _results(timeline, index))
    rows = [events[eid] for eid in sorted(selected)]
    image_order, images, missing = [], [], []
    for rid in reference_ids:
        path = catalogue['references'][rid]
        if not attach_images or not Path(path).is_file():
            missing.append({'role': 'reference', 'reference_id': rid, 'path': path})
            continue
        for view in image_views(path, Path(output) / 'image_views'):
            image_order.append({'role': 'reference', 'reference_id': rid, **view, 'attachment_index': len(images) + 1})
            images.append(view['path'])
    recorded_image_ids = []
    for row in rows:
        if row['kind'] != 'observation':
            continue
        for image in row.get('images') or []:
            raw = image if isinstance(image, str) else image.get('path', '')
            path = _resolve_image(raw, Path(rounds['source_run'])) if raw else None
            recorded_image_ids.append(row['ordinal'])
            label = {'role': 'agent_observation', 'event_id': row['ordinal'],
                     'url_context': row.get('browser_url_context'), **states.get(row['ordinal'], {})}
            reconstructed = (reconstructed_images or {}).get(row['ordinal']) if not path else None
            if reconstructed:
                path = reconstructed['path']
                label.update({k: v for k, v in reconstructed.items() if k not in {'path', 'sha256'}})
            if not attach_images or not path:
                missing.append({**label, 'path': raw, 'agent_received_image': True})
                continue
            expected = reconstructed['sha256'] if reconstructed else image.get('sha256') if isinstance(image, dict) else None
            if expected and hashlib.sha256(Path(path).read_bytes()).hexdigest() != expected:
                raise ValueError('Recorded image bytes do not match their trajectory hash')
            for view in image_views(path, Path(output) / 'image_views'):
                image_order.append({**label, **view, 'attachment_index': len(images) + 1})
                images.append(view['path'])
    facts = []
    for definition in assertions:
        if definition['check_id'] not in {c['check_id'] for c in catalogue['checks']}:
            raise ValueError('Assertion refers to an unknown catalogue check')
        evidence_ids = definition['evidence_ids']
        if not set(evidence_ids) <= selected:
            continue
        observations = [events[eid] for eid in evidence_ids if events[eid]['kind'] == 'observation']
        if len(observations) != 1 or observations[0].get('is_error'):
            continue
        try:
            data = json.loads(observations[0].get('text', ''))
            if isinstance(data, str):
                data = json.loads(data)
        except ValueError:
            continue
        passed = assess_output(definition['assertions'], data)
        if passed is not None:
            facts.append({'check_id': definition['check_id'], 'state': 'pass' if passed else 'fail',
                          'evidence_ids': evidence_ids, 'assertions': definition['assertions'], 'observed': data})
    facts = [f for f in facts if f['check_id'] not in catalogue.get('unresolved_check_ids', [])]
    packet = {'episode_id': episode['episode_id'], 'observation_cutoff': cutoff, 'task': task,
              'catalogue': catalogue['checks'], 'criteria_sha256': catalogue['criteria_sha256'],
              **({'unresolved_check_ids': catalogue['unresolved_check_ids']} if catalogue.get('unresolved_check_ids') else {}),
              'core_event_ids': [eid for eid in episode['core_event_ids'] if eid <= cutoff],
              'context_event_ids': sorted(selected - set(episode['core_event_ids'])),
              'eligible_diagnosis_event_ids': sorted(set(responses + [eid for eid in episode['core_event_ids'] if eid <= cutoff
                                               and events[eid]['kind'] in {'model_text', 'reasoning'}])),
              'diagnosis_event_ids': [eid for eid in episode['judgment_event_ids'] if eid <= cutoff],
              'events': rows, 'evidence_states': [states[eid] for eid in selected if eid in states],
              'image_order': image_order, 'missing_images': missing, 'rule_facts': facts,
              'recorded_image_event_ids': sorted(set(recorded_image_ids))}
    if reconstructed_images is not None:
        packet['evidence_policy'] = ('reconstructed_observations: Images marked origin=reconstructed are new executions of '
            'the recorded capture commands, not original image bytes. Assess the supplied reconstructed state; '
            'historical equivalence is unverified. Do not infer new agent actions or diagnoses from reconstruction. '
            'Use unknown when timing, environment differences or missing evidence prevent a supported judgment.')
    return packet, images


def merge_prefix(merged, prefix):
    """One condition per round; a later explicit diagnosis supersedes an earlier one."""
    for row in prefix['targets']:
        previous = merged['targets'].get(row['target_id'])
        if previous and previous['coverage'] == 'full' and check_validity(previous['method_ok'], previous['evidence_ok']) is True:
            continue
        merged['targets'][row['target_id']] = row
    for row in prefix['assessments']:
        previous = merged['assessments'].get(row['target_id'])
        if previous is None or row['diagnosis_ids'] or not previous['diagnosis_ids']:
            merged['assessments'][row['target_id']] = row


def validate_saved_episode(row, root, rounds, catalogue):
    """Rebuild labels from the same role-separated inputs and original responses."""
    if row.get('protocol_version') != PROTOCOL_VERSION:
        raise ValueError('Stored checks use a different protocol; run fresh judgments')
    episode = next(ep for ep in rounds['episodes'] if ep['episode_id'] == row['episode_id'])
    original = {e['ordinal']: e for e in rounds['events']}
    cutoffs = round_cutoffs(rounds, episode)
    if ([r['cutoff'] for r in row['requests'] if r['metric'] == 'VC'] != cutoffs
            or row['relation_annotation_complete'] != episode['relation_annotation_complete']
            or row['repair_event_ids'] != sorted({eid for link in episode['repair_links'] for eid in link['repair_event_ids']})):
        raise ValueError('Stored check boundaries or repair relations differ from extraction')
    merged = {'targets': {}, 'assessments': {}}
    for cutoff in cutoffs:
        requests = [r for r in row['requests'] if r['cutoff'] == cutoff]
        stages = {'CV': [], 'BDA': []}
        prefix = read_json(requests[0]['input_path'])
        if (prefix['criteria_sha256'] != catalogue['criteria_sha256']
                or hashlib.sha256(prefix['task'].encode()).hexdigest() != catalogue['task_sha256']
                or prefix['episode_id'] != episode['episode_id']
                or prefix['observation_cutoff'] != cutoff
                or prefix['core_event_ids'] != [eid for eid in episode['core_event_ids'] if eid <= cutoff]
                or any(e['ordinal'] > cutoff or original.get(e['ordinal']) != e for e in prefix['events'])):
            raise ValueError('Stored check input differs from source or criteria')
        eligible = sorted(set(eid for eid in episode['core_event_ids'] + trailing_responses(rounds, episode)
                              if eid <= cutoff and original[eid]['kind'] in {'model_text', 'reasoning'}))
        if prefix['eligible_diagnosis_event_ids'] != eligible:
            raise ValueError('Stored diagnosis eligibility differs from original boundaries')
        for image in prefix['image_order']:
            if (hashlib.sha256(Path(image['path']).read_bytes()).hexdigest() != image['sha256']
                    or hashlib.sha256(Path(image['source_path']).read_bytes()).hexdigest() != image['source_sha256']):
                raise ValueError('Stored image evidence changed after evaluation')
        seen = []
        for request in requests:
            metric = request['metric']
            if metric not in {'VC', 'CV', 'OBSERVATION', 'BDA'} or metric in seen:
                raise ValueError('Invalid or duplicate stored stage')
            expected = prefix if metric == 'VC' else stage_packet(metric, prefix, stages['VC'], stages.get('OBSERVATION'))
            supplied = read_json(request['input_path'])
            record = read_json(request['response_path'])
            expected_stage = ('visual' if prefix['recorded_image_event_ids'] else 'text') + '_' + metric.lower()
            if (supplied != expected or record.get('error') or record['request_sha256'] != request['request_sha256']
                    or request['stage'] != expected_stage or record.get('stage', expected_stage) != expected_stage
                    or not record['prompt'].endswith(json.dumps(expected, ensure_ascii=False))):
                raise ValueError('Stored stage input differs from the original judge request')
            stages[metric] = (validate_observations(record['parsed'], supplied, catalogue) if metric == 'OBSERVATION'
                              else validate_stage(metric, record['parsed'], supplied, catalogue))
            seen.append(metric)
        expected_order = ['VC'] + (['CV'] if stages.get('VC') else []) + ['OBSERVATION']
        if stages.get('OBSERVATION', {}).get('targets'):
            expected_order.append('BDA')
        if seen != expected_order:
            raise ValueError('Stored metric calls omitted or reordered a stage')
        merge_prefix(merged, merge_stages(stages['VC'], stages['CV'], stages['BDA'], stages['OBSERVATION']))
    if (list(merged['targets'].values()) != row['targets']
            or list(merged['assessments'].values()) != row['assessments']
            or row['status'] != ('evaluated' if merged['targets'] or merged['assessments'] else 'excluded')):
        raise ValueError('Stored labels differ from original judge responses')
    return row


def unassessed_repairs(rounds, repairs):
    assessed = {tuple(r['repair_event_ids']) for r in repairs}
    gaps = {}
    for episode in rounds['episodes']:
        for link in episode['repair_links']:
            edits = tuple(link['repair_event_ids'])
            if edits in assessed:
                continue
            gap = gaps.setdefault(edits, {'repair_event_ids': list(edits), 'episode_ids': []})
            if episode['episode_id'] not in gap['episode_ids']:
                gap['episode_ids'].append(episode['episode_id'])
    return list(gaps.values())


def validate_repair_aliases(episodes, repairs):
    """Do not attach an older target identity to newly generated metric labels."""
    by_episode = {e['episode_id']: {t['target_id'] for t in e['targets'] + e.get('assessments', [])} for e in episodes}
    for repair in repairs:
        known = {tid for eid in repair['episode_ids'] for tid in by_episode.get(eid, set())}
        if not set(repair.get('target_aliases', {})) <= known:
            raise ValueError('Repair target aliases do not match the current check labels')


def evaluate_rounds(rounds_path, catalogue_path, task_root, config_path, output, *,
                    primary_profile=None, allow_draft=False, offline=False, reference_map=None,
                    assertions=(), repairs=(), workers=1, reuse_checks=None, reconstructed_index=None, scope_max_input_chars=500000):
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    previous = output / 'scores.json'
    if previous.is_file() and read_json(previous).get('protocol_version') != PROTOCOL_VERSION:
        raise ValueError('Check output uses an older protocol; use a new output directory')
    rounds = load_rounds(rounds_path)
    original_episode_ids = {ep['episode_id'] for ep in rounds['episodes']}
    reconstructed = None
    if reconstructed_index:
        if reuse_checks:
            raise ValueError('Reconstructed evidence requires fresh judgments; resume using the same output cache')
        from .archive_replay import load_reconstructed_images
        reconstructed = load_reconstructed_images(reconstructed_index, rounds)
    catalogue = load_catalogue(catalogue_path, allow_draft=allow_draft)
    if not offline and catalogue.get('unresolved_check_ids'):
        raise ValueError('Resolve catalogue acceptance conflicts before evaluating trajectories')
    task = (Path(task_root) / 'prompt.txt').read_text()
    if hashlib.sha256(task.encode()).hexdigest() != catalogue['task_sha256']:
        raise ValueError('Catalogue and task input differ')
    config = load_judge_config(config_path)
    def client(stage, response_schema=None):
        return build_client(config, primary_profile or config['primary_stage_profiles'][stage], output / 'judge_cache' / stage,
                            response_schema=response_schema)
    previous_scope = Path(reuse_checks) / 'scope.json' if reuse_checks else output / 'scope.json'
    if not offline and previous_scope.is_file():
        scope = read_json(previous_scope)
        validate_saved_scope(scope, rounds, task)
        write_json(output / 'scope.json', scope)
    else:
        scope = None if offline else locate_scope(rounds, task, client, output, max_input_chars=scope_max_input_chars)
    if scope is not None:
        rounds = scoped_rounds(rounds, scope)
    reused = {}
    for episode in rounds['episodes']:
        previous = Path(reuse_checks) / 'episode_results' / f"{episode['episode_id']}.json" if reuse_checks else None
        if previous and previous.is_file():
            row = validate_saved_episode(read_json(previous), previous.parent.parent, rounds, catalogue)
            if any(read_json(request['input_path']).get('evidence_policy') for request in row['requests']):
                raise ValueError('Reconstructed judgments cannot be reused as original-evidence evaluation')
            if any('metric' not in request for request in row['requests']):
                raise ValueError('Independent metric evaluation cannot reuse historical joint judgments')
            reused[episode['episode_id']] = {**row, 'reused_from': str(previous.resolve())}
    pending = [e for e in rounds['episodes'] if e['episode_id'] not in reused]
    mapping = read_json(reference_map) if reference_map else None
    if not offline and mapping is None and pending:
        mapping = reference_mapping({**rounds, 'episodes': pending}, catalogue, client('annotation'))
        write_json(output / 'reference_mapping.json', mapping)
    if mapping is not None:
        mapping = mapping.get('mapping')
        if not isinstance(mapping, dict):
            raise ValueError('Reference mapping must contain an episode mapping')
        if not {e['episode_id'] for e in pending} <= set(mapping) <= original_episode_ids:
            raise ValueError('Reference mapping belongs to a different episode set')
        for refs in mapping.values():
            if not isinstance(refs, list) or any(not isinstance(r, str) or r not in catalogue['references'] for r in refs):
                raise ValueError('Invalid supplied reference mapping')
    else:
        mapping = {}
    def assess(episode):
        if episode['episode_id'] in reused:
            row = reused[episode['episode_id']]
            write_json(output / 'episode_results' / f"{episode['episode_id']}.json", row)
            print('reuse', episode['episode_id'], flush=True)
            return row
        print('check', episode['episode_id'], flush=True)
        merged, requests = {'targets': {}, 'assessments': {}}, []
        cutoffs = round_cutoffs(rounds, episode)
        for cutoff in sorted(cutoffs):
            packet, images = build_packet(rounds, episode, cutoff, task, catalogue, mapping.get(episode['episode_id'], []), output, assertions,
                                          reconstructed_images=reconstructed)
            packet['previous_target_conditions'] = [{'target': t['target'], 'check_id': t['check_id']} for t in merged['targets'].values()]
            if offline:
                write_json(output / 'inputs' / f"{episode['episode_id']}-{cutoff}.json", packet)
                continue
            prefix, metric_requests = judge_round_prefix(packet, images, catalogue, client, output)
            merge_prefix(merged, prefix)
            requests.extend(metric_requests)
        row = {'episode_id': episode['episode_id'], 'targets': list(merged['targets'].values()),
               'assessments': list(merged['assessments'].values()), 'protocol_version': PROTOCOL_VERSION,
               'status': 'pending' if offline else 'evaluated' if any(merged.values()) else 'excluded', 'requests': requests,
               'relation_annotation_complete': episode['relation_annotation_complete'],
               'repair_event_ids': sorted({eid for link in episode['repair_links'] for eid in link['repair_event_ids']})}
        write_json(output / 'episode_results' / f"{episode['episode_id']}.json", row)
        return row
    if workers < 1:
        raise ValueError('workers must be positive')
    results = []
    if rounds['episodes']:
        results.append(assess(rounds['episodes'][0]))
    pool = ThreadPoolExecutor(max_workers=workers)
    try:
        futures = [pool.submit(assess, ep) for ep in rounds['episodes'][1:]]
        for future in as_completed(futures):
            results.append(future.result())
    finally:
        pool.shutdown(wait=True, cancel_futures=True)
    order = {ep['episode_id']: i for i,ep in enumerate(rounds['episodes'])}
    results.sort(key=lambda row: order[row['episode_id']])
    validate_repair_aliases(results, repairs)
    gaps = unassessed_repairs(rounds, repairs)
    result = {'schema': 'self-verification-evaluation-1', 'case_id': rounds['case_id'],
              'source_rounds': str(Path(rounds_path).resolve()), 'source_run_sha256': rounds['source_run_sha256'],
              'catalogue': str(Path(catalogue_path).resolve()), 'criteria_sha256': catalogue['criteria_sha256'],
              'catalogue_review': catalogue['review'], 'judge_validation': 'not_validated',
              'judgment_method': 'independent_catalogue_observation', 'scope': scope,
              'status': 'offline' if offline else 'pilot' if catalogue['review']['status'] != 'model_reviewed' else 'evaluated',
              'episodes': results, 'repairs': list(repairs), 'repair_gaps': gaps,
              'protocol_version': PROTOCOL_VERSION, 'findings': findings(results, repairs),
              'metrics': summarize(catalogue, results, repairs, gaps, scope=scope)}
    if not offline and any(result['metrics'][m]['status'] == 'incomplete' for m in METRICS):
        result['status'] = 'incomplete'
    if reconstructed_index:
        result.update(evidence_mode='reconstructed_observations', reconstructed_index=str(Path(reconstructed_index).resolve()),
                      reconstructed_index_sha256=hashlib.sha256(Path(reconstructed_index).read_bytes()).hexdigest(),
                      historical_pixel_equivalence='unverified', status=result['status'] if result['status'] in {'offline','incomplete'} else 'reconstructed_pilot')
    write_json(output / 'scores.json', result)
    from .report import write_protocol_report
    write_protocol_report(result, output / 'report.md')
    return result


def finalize_evaluation(check_results, output, repair_results=None):
    """Recompute metrics from validated stored labels; do not call a judge again."""
    saved = read_json(check_results)
    if saved.get('schema') != 'self-verification-evaluation-1' or saved.get('protocol_version') != PROTOCOL_VERSION:
        raise ValueError('Expected current protocol check results; older scores require fresh judgments')
    if saved.get('reconstructed_index') and hashlib.sha256(Path(saved['reconstructed_index']).read_bytes()).hexdigest() != saved['reconstructed_index_sha256']:
        raise ValueError('Reconstructed evidence index changed after evaluation')
    rounds = load_rounds(saved['source_rounds'])
    catalogue = load_catalogue(saved['catalogue'], allow_draft=True)
    if saved['source_run_sha256'] != rounds['source_run_sha256'] or saved['criteria_sha256'] != catalogue['criteria_sha256']:
        raise ValueError('Check labels refer to changed source data or criteria')
    if saved['scope'] is None:
        raise ValueError('Offline inputs are not scored results')
    task = read_json(saved['scope']['response_path'])['prompt']
    # The task hash is checked independently from the boundary response.
    from .scope import PROMPT as SCOPE_PROMPT
    task_text = json.loads(task[len(SCOPE_PROMPT):])['task']
    if hashlib.sha256(task_text.encode()).hexdigest() != catalogue['task_sha256']:
        raise ValueError('Scope and catalogue task differ')
    validate_saved_scope(saved['scope'], rounds, task_text)
    rounds = scoped_rounds(rounds, saved['scope'])
    for episode in saved['episodes']:
        validate_saved_episode(episode, Path(check_results).resolve().parent, rounds, catalogue)
    from .states import load_repair_results
    repairs = load_repair_results(repair_results,saved['source_rounds'],saved['catalogue']) if repair_results else saved['repairs']
    active_edits = {tuple(link['repair_event_ids']) for ep in rounds['episodes'] for link in ep['repair_links']}
    repairs = [r for r in repairs if tuple(r['repair_event_ids']) in active_edits]
    validate_repair_aliases(saved['episodes'], repairs)
    gaps = unassessed_repairs(rounds, repairs)
    result = {**saved, 'source_check_results':str(Path(check_results).resolve()), 'repairs':repairs, 'repair_gaps': gaps,
              'protocol_version': PROTOCOL_VERSION, 'findings': findings(saved['episodes'], repairs),
              'metrics':summarize(catalogue,saved['episodes'],repairs,gaps,scope=saved['scope'])}
    if any(result['metrics'][m]['status'] == 'incomplete' for m in METRICS):
        result['status'] = 'incomplete'
    elif result.get('evidence_mode') == 'reconstructed_observations':
        result['status'] = 'reconstructed_pilot'
    else:
        result['status'] = 'pilot' if catalogue['review']['status'] != 'model_reviewed' else 'evaluated'
    result.pop('human_calibrated', None)
    result['judge_validation'] = saved.get('judge_validation', 'not_validated')
    if repair_results:
        result['source_repair_results'] = str(Path(repair_results).resolve())
        result['repair_evaluation_status'] = read_json(repair_results)['status']
    output=Path(output).resolve()
    output.mkdir(parents=True,exist_ok=True)
    write_json(output/'scores.json',result)
    from .report import write_protocol_report
    write_protocol_report(result,output/'report.md')
    return result
