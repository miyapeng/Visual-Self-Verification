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
from .metrics import summarize, findings, PROTOCOL_VERSION
from .repair_pilot import assess_output
from .check_stages import (judge_round_prefix, validate_stage, validate_observations, current_image_ids,
                           metric_packet, merge_stages, stage_packet, align_rechecks, validate_saved_rechecks)
from .checks import _call_id, _results


# Historical joint-response validation; new calls use check_stages.stage_schema.
TARGET_FIELDS = {"target", "check_id", "coverage", "method_ok", "evidence_ok", "actual_state",
                 "actual_issue", "agent_state", "issue_match", "evidence_ids", "diagnosis_ids", "modality"}
TARGET_SCHEMA = rows_schema('targets', {
    'target': {'type':'string'}, 'check_id': {'type':['string','null']},
    'coverage': {'type':'string','enum':['full','partial','none','unknown']},
    **{key:{'type':['boolean','null']} for key in ['method_ok','evidence_ok','issue_match']},
    'actual_state': {'type':'string','enum':['pass','fail','unknown']},
    'actual_issue': {'anyOf':[{'type':'null'}, {'type':'object','properties':{'object':{'type':'string'},'symptom':{'type':'string'}},
                                            'required':['object','symptom'],'additionalProperties':False}]},
    'agent_state': {'type':'string','enum':['pass','fail','absent','uncertain']},
    **{key:{'type':'array','items':{'type':'integer'}} for key in ['evidence_ids','diagnosis_ids']},
    'modality': {'type':'string','enum':['text','visual','mixed']}})
CHECK_PROMPT = """Evaluate this recorded verification round at the target level. Return labels, never scores.
Identify checks actually performed or attempted, including observable errors the agent missed within its inspection scope.
Only core_event_ids define the current check. context_event_ids are background or shared evidence sources, never additional checks in this round.
For a shared batch capture, evaluate only the object inspected by this round's core operations and responses. Do not import other pages' checks or judgments.
Include checks of the generated project's startup, builds/tests, routes, resource loading, console/log health, behavior, and source/asset diagnostics.
Keep these legitimate checks with check_id=null when the catalogue lacks an equivalent goal. Ordinary correctness standards grounded in the supplied task are applicable; the catalogue is not a whitelist.
Do not exclude an actual application check because it failed, lacks an image, lacks an agent diagnosis, or has no catalogue match.
Match equivalent success conditions to catalogue IDs. Keep legitimate unmatched checks grounded in the supplied task; do not invent requirements.
Pure resource preparation or exploration of the host environment is not a check of generated output. Return an empty targets array for an unrelated round.
Evaluate method_ok (could this method distinguish satisfaction from violation?) and evidence_ok (did the agent receive sufficient evidence for this object, version, and state?).
Full coverage requires an exercised target AND sufficient observation; an observed application failure can count as full coverage.
An exit code of zero, screenshot filename, or agent completion claim is not proof of target satisfaction.
For unresolved_check_ids, retain the attempted check but use coverage=unknown, actual_state=unknown,
method_ok=null and evidence_ok=null. Source conflicts are not resolved from the agent's implementation.
Determine actual_state independently from observations. Use actual_issue=null for normal/unknown targets, or {"object":"...","symptom":"..."} for a visible failure.
Use the LAST substantive diagnosis for a target in the supplied round prefix. Cite its original event ID; use absent and [] if no diagnosis was expressed.
diagnosis_ids must come from eligible_diagnosis_event_ids. Tool feedback and earlier context responses are never agent diagnoses in this round.
For fail diagnoses require a matching object and observable symptom, not necessarily a root cause. Missing/uncertain diagnosis is not correct when the actual state is known.
Use only evidence available at or before the diagnosis event; never use a later observation to validate an earlier statement.
Earlier images retain their CAPTURE version, even if read after an edit. Context is support, not a fresh check of a new version.
Reference images are requirements, not observations. Use attached pixels for visual claims; a text ledger image path is not pixels.
If an archived image is missing, preserve the recorded fact that the agent received it; use unknown for claims that need its pixels, not false or pass.
Choose modality by the evidence needed to judge THIS target: text, visual, or mixed. Browser use alone is not a visual check.
Applicable rule_facts are exact results; do not contradict them. Do not infer interactive behavior from a static screenshot.
Return exactly {"targets":[{"target":"concise English condition","check_id":null,"coverage":"full|partial|none|unknown","method_ok":null,"evidence_ok":null,"actual_state":"pass|fail|unknown","actual_issue":null,"agent_state":"pass|fail|absent|uncertain","issue_match":null,"evidence_ids":[],"diagnosis_ids":[],"modality":"text|visual|mixed"}]}.
Each target occurs once in this response. Evidence IDs must include the actual observed feedback for a decidable state or full coverage. A catalogue is not evidence of a performed check.
For an equivalent condition already listed in previous_target_conditions, reuse its exact target text and check_id. These identities contain no prior judge verdicts.
EVIDENCE:
"""


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


def validate_targets(value, packet, catalogue):
    if not isinstance(value, dict) or set(value) != {'targets'} or not isinstance(value['targets'], list):
        raise ValueError('Judge must return only a targets array')
    events = {e['ordinal']: e for e in packet['events']}
    goals = {c['check_id'] for c in catalogue['checks']}
    targets, seen = [], set()
    facts = {f['check_id']: f for f in packet['rule_facts']}
    for target in value['targets']:
        if not isinstance(target, dict) or set(target) != TARGET_FIELDS:
            raise ValueError('Invalid target fields')
        if not isinstance(target['target'], str) or not target['target'].strip():
            raise ValueError('A target must describe a condition')
        if target['check_id'] is not None and target['check_id'] not in goals:
            raise ValueError('Unknown catalogue target')
        if target['check_id'] in catalogue.get('unresolved_check_ids', []) and (
                target['coverage'] != 'unknown' or target['actual_state'] != 'unknown'
                or target['method_ok'] is not None or target['evidence_ok'] is not None):
            raise ValueError('An unresolved criterion cannot receive a decidable check label')
        for field in ('method_ok', 'evidence_ok', 'issue_match'):
            if target[field] is not None and type(target[field]) is not bool:
                raise ValueError(f'{field} must be boolean or null')
        for field, choices in [('coverage', {'full','partial','none','unknown'}),
                               ('actual_state', {'pass','fail','unknown'}),
                               ('agent_state', {'pass','fail','absent','uncertain'}),
                               ('modality', {'text','visual','mixed'})]:
            if target[field] not in choices:
                raise ValueError(f'Invalid {field}')
        for field in ('evidence_ids', 'diagnosis_ids'):
            ids = target[field]
            if not isinstance(ids, list) or any(type(eid) is not int or eid not in events for eid in ids) or len(ids) != len(set(ids)):
                raise ValueError('Target references nonexistent, duplicate, or future events')
        diagnosis = target['diagnosis_ids']
        if any(events[eid]['kind'] not in {'model_text','reasoning'} or eid not in packet['core_event_ids'] for eid in diagnosis):
            raise ValueError('Diagnosis must cite original model responses in this round')
        if target['agent_state'] in {'pass','fail','uncertain'} and not diagnosis:
            raise ValueError('An expressed conclusion needs a diagnosis reference')
        if target['agent_state'] == 'absent' and diagnosis:
            raise ValueError('Absent diagnosis cannot cite an expressed diagnosis')
        if diagnosis and any(eid > max(diagnosis) for eid in target['evidence_ids']):
            raise ValueError('Evidence occurs after the diagnosis it is used to assess')
        feedback = [events[eid] for eid in target['evidence_ids'] if events[eid]['kind'] == 'observation']
        if (target['coverage'] == 'full' or target['actual_state'] != 'unknown') and not feedback:
            raise ValueError('Full coverage and decidable state require observed feedback')
        if target['coverage'] == 'full' and (target['method_ok'] is not True or target['evidence_ok'] is not True):
            raise ValueError('Full coverage requires valid method and evidence')
        issue = target['actual_issue']
        if issue is not None and (not isinstance(issue, dict) or set(issue) != {'object','symptom'}
                                  or any(not isinstance(v, str) or not v.strip() for v in issue.values())):
            raise ValueError('actual_issue must identify an object and observable symptom')
        if target['actual_state'] == 'fail' and issue is None:
            raise ValueError('Known failure requires observable issue evidence')
        if target['actual_state'] != 'fail' and issue is not None:
            raise ValueError('Normal or unknown state cannot assert a known issue')
        if target['modality'] != 'text' and (target['actual_state'] != 'unknown' or target['coverage'] == 'full'):
            available = {i['event_id'] for i in packet['image_order'] if i['role'] == 'agent_observation'}
            if not set(target['evidence_ids']) & available:
                raise ValueError('A visual state requires actual attached agent-observation pixels')
        if target['check_id'] in facts and target['actual_state'] != facts[target['check_id']]['state']:
            raise ValueError('Judge contradicted an applicable objective assertion')
        identity = target['check_id'] or content_hash(target['target'])[:16]
        if identity in seen:
            raise ValueError('Duplicate target in one round prefix')
        seen.add(identity)
        targets.append({**target, 'target_id': identity})
    return targets


def validate_saved_episode(row, root, rounds, catalogue):
    """Reuse original labels only after checking their recorded inputs and responses."""
    episode = next(ep for ep in rounds['episodes'] if ep['episode_id'] == row['episode_id'])
    original = {e['ordinal']: e for e in rounds['events']}
    cutoffs = round_cutoffs(rounds, episode)
    separated = bool(row['requests'] and 'metric' in row['requests'][0])
    recorded_cutoffs = [r['cutoff'] for r in row['requests'] if r.get('metric') == 'VC'] if separated else [r['cutoff'] for r in row['requests']]
    if (recorded_cutoffs != cutoffs
            or row['relation_annotation_complete'] != episode['relation_annotation_complete']
            or row['repair_event_ids'] != sorted({eid for link in episode['repair_links'] for eid in link['repair_event_ids']})):
        raise ValueError('Stored check boundaries or repair relations differ from extraction')
    merged, requests, stages, prefix_packet = {}, [], {}, None
    for request in row['requests']:
        packet = read_json(request['input_path'])
        if (packet['criteria_sha256'] != catalogue['criteria_sha256']
                or hashlib.sha256(packet['task'].encode()).hexdigest() != catalogue['task_sha256']
                or packet['episode_id'] != episode['episode_id']
                or packet['observation_cutoff'] != request['cutoff']
                or packet['core_event_ids'] != [eid for eid in episode['core_event_ids'] if eid <= request['cutoff']]
                or any(e['ordinal'] > request['cutoff'] or original.get(e['ordinal']) != e for e in packet['events'])):
            raise ValueError('Stored check input differs from the current source or criteria')
        eligible = sorted(set([eid for eid in episode['core_event_ids'] + trailing_responses(rounds, episode)
                               if eid <= request['cutoff'] and original[eid]['kind'] in {'model_text', 'reasoning'}]))
        if packet['eligible_diagnosis_event_ids'] != eligible:
            raise ValueError('Stored diagnosis eligibility differs from the original response boundaries')
        for image in packet['image_order']:
            if (hashlib.sha256(Path(image['path']).read_bytes()).hexdigest() != image['sha256']
                    or hashlib.sha256(Path(image['source_path']).read_bytes()).hexdigest() != image['source_sha256']):
                raise ValueError('Stored image evidence changed after evaluation')
        response_path = Path(request.get('response_path') or Path(root) / 'judge_cache' / request['stage'] / (request['request_sha256']+'.json'))
        record = read_json(response_path)
        if record.get('error') or record['request_sha256'] != request['request_sha256']:
            raise ValueError('Invalid stored judge response')
        if not record['prompt'].endswith(json.dumps(packet, ensure_ascii=False)):
            raise ValueError('Scored input differs from the actual judge prompt')
        if separated:
            metric = request.get('metric')
            if metric == 'VC':
                if stages:
                    raise ValueError('Stored metric calls omitted a stage')
                prefix_packet = packet
            elif metric not in {'CV', 'OBSERVATION', 'BDA'} or prefix_packet is None or 'VC' not in stages:
                raise ValueError('Invalid stored metric call order')
            if (metric in stages or (metric in {'OBSERVATION', 'BDA'} and stages['VC'] and 'CV' not in stages)
                    or (metric == 'CV' and (not stages['VC'] or 'OBSERVATION' in stages))
                    or (metric == 'BDA' and 'OBSERVATION' not in stages)):
                raise ValueError('Duplicate or unordered metric call')
            if metric != 'VC':
                expected = stage_packet(metric, prefix_packet, stages['VC'], stages.get('OBSERVATION'))
                if packet != expected and packet != {**expected, 'catalogue': prefix_packet['catalogue']}:
                    raise ValueError('Metric input contains changed identities or another metric verdict')
            expected_stage = ('visual' if packet['recorded_image_event_ids'] else 'text') + '_' + metric.lower()
            if request['stage'] != expected_stage or record.get('stage', expected_stage) != expected_stage:
                raise ValueError('Stored response belongs to a different metric stage')
            stages[metric] = (validate_observations(record['parsed'], packet, catalogue) if metric == 'OBSERVATION'
                              else validate_stage(metric, record['parsed'], packet, catalogue))
            if metric == 'BDA':
                targets = merge_stages(stages['VC'], stages.get('CV', []), stages['BDA'], stages['OBSERVATION'])
                stages = {}
            elif (metric == 'VC' and not stages['VC'] and not current_image_ids(packet)
                  or metric == 'OBSERVATION' and not stages['VC'] and not stages['OBSERVATION']['additional_defects']):
                targets, stages = [], {}
            else:
                targets = []
        else:
            targets = validate_targets(record['parsed'], packet, catalogue)
        for target in targets:
            previous = merged.get(target['target_id'])
            if previous is None or target['diagnosis_ids'] or not previous['diagnosis_ids']:
                merged[target['target_id']] = target
        requests.append({**request, 'response_path': str(response_path.resolve())})
    if stages or list(merged.values()) != row['targets'] or row['status'] == 'pending':
        raise ValueError('Stored labels are incomplete or differ from their judge responses')
    if separated and row['status'] != ('evaluated' if merged else 'excluded'):
        raise ValueError('Stored metric status differs from its target labels')
    return {**row, 'requests': requests}


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
    by_episode = {e['episode_id']: {t['target_id'] for t in e['targets']} for e in episodes}
    for repair in repairs:
        known = {tid for eid in repair['episode_ids'] for tid in by_episode.get(eid, set())}
        if not set(repair.get('target_aliases', {})) <= known:
            raise ValueError('Repair target aliases do not match the current check labels')


def evaluate_rounds(rounds_path, catalogue_path, task_root, config_path, output, *,
                    primary_profile=None, allow_draft=False, offline=False, reference_map=None,
                    assertions=(), repairs=(), workers=1, reuse_checks=None, reconstructed_index=None):
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    rounds = load_rounds(rounds_path)
    reconstructed = None
    if reconstructed_index:
        if reuse_checks:
            raise ValueError('Reconstructed evidence requires fresh judgments; resume using the same output cache')
        from .archive_replay import load_reconstructed_images
        reconstructed = load_reconstructed_images(reconstructed_index, rounds)
    catalogue = load_catalogue(catalogue_path, allow_draft=allow_draft)
    task = (Path(task_root) / 'prompt.txt').read_text()
    if hashlib.sha256(task.encode()).hexdigest() != catalogue['task_sha256']:
        raise ValueError('Catalogue and task input differ')
    config = load_judge_config(config_path)
    def client(stage, response_schema=None):
        return build_client(config, primary_profile or config['primary_stage_profiles'][stage], output / 'judge_cache' / stage,
                            response_schema=response_schema)
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
        if not {e['episode_id'] for e in pending} <= set(mapping) <= {e['episode_id'] for e in rounds['episodes']}:
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
        merged, requests = {}, []
        cutoffs = round_cutoffs(rounds, episode)
        for cutoff in sorted(cutoffs):
            packet, images = build_packet(rounds, episode, cutoff, task, catalogue, mapping.get(episode['episode_id'], []), output, assertions,
                                          reconstructed_images=reconstructed)
            packet['previous_target_conditions'] = [{'target': t['target'], 'check_id': t['check_id']} for t in merged.values()]
            if offline:
                write_json(output / 'inputs' / f"{episode['episode_id']}-{cutoff}.json", packet)
                continue
            targets, metric_requests = judge_round_prefix(packet, images, catalogue, client, output)
            for target in targets:
                # Later relevant diagnoses replace earlier hypotheses; never multiply the denominator.
                previous = merged.get(target['target_id'])
                if previous is None or target['diagnosis_ids'] or not previous['diagnosis_ids']:
                    merged[target['target_id']] = target
            requests.extend(metric_requests)
        row = {'episode_id': episode['episode_id'], 'targets': list(merged.values()),
               'status': 'pending' if offline else 'evaluated' if merged else 'excluded', 'requests': requests,
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
    alignment = None if offline else align_rechecks(results, rounds, client, output)
    result = {'schema': 'self-verification-evaluation-1', 'case_id': rounds['case_id'],
              'source_rounds': str(Path(rounds_path).resolve()), 'source_run_sha256': rounds['source_run_sha256'],
              'catalogue': str(Path(catalogue_path).resolve()), 'criteria_sha256': catalogue['criteria_sha256'],
              'catalogue_review': catalogue['review'], 'judge_validation': 'not_validated',
              'judgment_method': ('observation_with_visual_defect_audit' if all(
                  not e['targets'] or any(r.get('metric') == 'OBSERVATION' for r in e['requests'])
                  for e in results) else 'independent_metric_calls'),
              'status': 'offline' if offline else 'pilot' if catalogue['review']['status'] != 'model_reviewed' else 'evaluated',
              'episodes': results, 'repairs': list(repairs), 'repair_gaps': gaps,
              'protocol_version': PROTOCOL_VERSION, 'findings': findings(results, repairs), 'recheck_alignment': alignment,
              'metrics': summarize(catalogue, results, repairs, gaps, rounds, alignment['matches'] if alignment is not None else None)}
    if reconstructed_index:
        result.update(evidence_mode='reconstructed_observations', reconstructed_index=str(Path(reconstructed_index).resolve()),
                      reconstructed_index_sha256=hashlib.sha256(Path(reconstructed_index).read_bytes()).hexdigest(),
                      historical_pixel_equivalence='unverified', status='offline' if offline else 'reconstructed_pilot')
    write_json(output / 'scores.json', result)
    from .report import write_protocol_report
    write_protocol_report(result, output / 'report.md')
    return result


def finalize_evaluation(check_results, output, repair_results=None):
    """Recompute metrics from validated stored labels; do not call a judge again."""
    saved = read_json(check_results)
    if saved.get('schema') != 'self-verification-evaluation-1':
        raise ValueError('Expected protocol check results')
    if saved.get('reconstructed_index') and hashlib.sha256(Path(saved['reconstructed_index']).read_bytes()).hexdigest() != saved['reconstructed_index_sha256']:
        raise ValueError('Reconstructed evidence index changed after evaluation')
    rounds = load_rounds(saved['source_rounds'])
    catalogue = load_catalogue(saved['catalogue'], allow_draft=True)
    if saved['source_run_sha256'] != rounds['source_run_sha256'] or saved['criteria_sha256'] != catalogue['criteria_sha256']:
        raise ValueError('Check labels refer to changed source data or criteria')
    for episode in saved['episodes']:
        validate_saved_episode(episode, Path(check_results).resolve().parent, rounds, catalogue)
    from .states import load_repair_results
    repairs = load_repair_results(repair_results,saved['source_rounds'],saved['catalogue']) if repair_results else saved['repairs']
    validate_repair_aliases(saved['episodes'], repairs)
    gaps = unassessed_repairs(rounds, repairs)
    alignment = saved.get('recheck_alignment')
    matches = validate_saved_rechecks(alignment, saved['episodes'], rounds) if alignment is not None else None
    result = {**saved, 'source_check_results':str(Path(check_results).resolve()), 'repairs':repairs, 'repair_gaps': gaps,
              'protocol_version': PROTOCOL_VERSION, 'findings': findings(saved['episodes'], repairs),
              'metrics':summarize(catalogue,saved['episodes'],repairs,gaps,rounds,matches)}
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
