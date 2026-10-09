"""Local training rewards backed by the existing verification evaluator."""
from __future__ import annotations

import copy
import hashlib
import json
import math
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

from multimodalcode.io import read_json, write_json
from multimodalcode.vsv_eval.catalogue import content_hash, validate_catalogue
from multimodalcode.vsv_eval.check_stages import PROMPTS, stage_packet, stage_schema, validate_stage
from multimodalcode.vsv_eval.episodes import extract_verification_rounds
from multimodalcode.vsv_eval.evaluation import build_packet, round_cutoffs
from multimodalcode.vsv_eval.judge import build_client, load_judge_config
from multimodalcode.vsv_eval.metrics import check_validity, diagnosis_correct
from multimodalcode.vsv_eval.states import STATE_PROMPT, STATE_SCHEMA, validate_states
from .handoff import image_paths


def evidence_reward(targets, check_ids, covered_ids=()):
    remaining = set(check_ids) - set(covered_ids)
    if not remaining:
        raise ValueError('No uncovered conditions remain at this evidence checkpoint')
    attempted = [t for t in targets if t.get('check_attempted', True)]
    if any(t['coverage'] == 'unknown' or check_validity(t['method_ok'], t['evidence_ok']) is None
           for t in attempted):
        raise ValueError('Resolve incomplete evidence evaluation before training')
    new = {t['check_id'] for t in attempted if t['coverage'] == 'full'
           and check_validity(t['method_ok'], t['evidence_ok']) is True} & remaining
    return {'score': len(new) / len(remaining), 'new_check_ids': sorted(new), 'denominator': len(remaining)}


def diagnosis_reward(targets):
    classes = {}
    for target in targets:
        correct = diagnosis_correct(target['actual_state'], target['agent_state'], target['issue_match'])
        if correct is None:
            raise ValueError('Diagnosis reward requires independently decidable labels')
        classes.setdefault(target['actual_state'], []).append(int(correct))
    if not classes:
        raise ValueError('Diagnosis reward needs at least one condition')
    means = {state: sum(values) / len(values) for state, values in classes.items()}
    return {'score': sum(means.values()) / len(means), 'class_accuracy': means}


def repair_reward(before, after, target_ids, *, edited, regression_weight=1.0):
    if not math.isfinite(regression_weight) or regression_weight < 0:
        raise ValueError('Regression weight must be finite and nonnegative')
    if not target_ids or set(before) != set(after) or not set(target_ids) <= set(before):
        raise ValueError('Repair needs matching fixed conditions before and after')
    if any(s not in {'pass', 'fail'} for s in list(before.values()) + list(after.values())):
        raise ValueError('Repair acceptance is incomplete')
    failed = {cid for cid in target_ids if before[cid] == 'fail'}
    fixed = {cid for cid in failed if after[cid] == 'pass'}
    preserved = {cid for cid, state in before.items() if state == 'pass'}
    regressed = {cid for cid in preserved if after[cid] == 'fail'}
    gain = len(fixed) / len(failed) if failed and edited else float(not failed and not edited)
    penalty = len(regressed) / len(preserved) if preserved else 0.0
    return {'score': gain - regression_weight * penalty, 'target_gain': gain, 'regression_rate': penalty,
            'fixed_check_ids': sorted(fixed), 'regressed_check_ids': sorted(regressed),
            'accepted_without_edit': not failed and not edited}


def objective_weights(kinds, *, local_weight=1.0, local_kinds=('evidence', 'diagnosis', 'repair'), include_task=True):
    """Scale advantages AFTER group normalization, for a sequence-mean actor loss."""
    enabled = set(local_kinds) | ({'task'} if include_task else set())
    if (not kinds or set(kinds) != enabled or len(set(local_kinds)) != len(local_kinds)
            or 'task' in local_kinds or not enabled <= {'task', 'evidence', 'diagnosis', 'repair'}):
        raise ValueError('Each joint update must contain every configured decision kind')
    if not math.isfinite(local_weight) or local_weight < 0:
        raise ValueError('local_weight must be finite and nonnegative')
    return [len(kinds) / kinds.count(kind) * (1.0 if kind == 'task' else local_weight / len(local_kinds))
            for kind in kinds]


def trajectory(checkpoint, suffix, records):
    """Record generated messages and executed tools once, preserving native call IDs."""
    timeline, development = [], []
    by_call = {r['tool_call_id']: r for r in records}
    if len(by_call) != len(records):
        raise ValueError('Duplicate executed tool call IDs')
    origin = datetime.now(timezone.utc)
    message_events = {}

    def append(kind, **values):
        ordinal = len(timeline) + 1
        row = {'ordinal': ordinal, 'kind': kind,
               'timestamp': (origin + timedelta(microseconds=ordinal)).isoformat(), **values}
        timeline.append(row)
        return row

    initial = checkpoint.get('environment', {}).get('code_manifest', {}).get('sha256') if checkpoint.get('environment') else None
    development.append({'type': 'recorder_started', 'payload': {'initial_program_sha256': initial}})
    for index, message in enumerate(suffix):
        role = message['role']
        if role == 'assistant':
            if message.get('content'):
                event = append('model_text', text=message['content'])
                message_events[index] = [event['ordinal']]
            for call in message.get('tool_calls', []):
                name = call['function']['name']
                arguments = call['function']['arguments']
                try:
                    arguments = json.loads(arguments) if isinstance(arguments, str) else arguments
                except ValueError:
                    arguments = {'invalid_arguments': arguments}
                if not isinstance(arguments, dict):
                    arguments = {'invalid_arguments': arguments}
                category = 'browser' if name == 'browser' else 'edit' if name in {'write_file', 'edit_file'} else 'inspect'
                event = append('action', tool=name, tool_call_id=call['id'], category=category,
                               payload={**arguments, **({'file_path': arguments['path']} if 'path' in arguments else {})})
                message_events.setdefault(index, []).append(event['ordinal'])
        elif role == 'tool':
            record = by_call[message['tool_call_id']]
            event = append('observation', tool=record['tool'], tool_call_id=record['tool_call_id'],
                           text=record['text'], is_error=record['is_error'],
                           images=[{'path': p, 'sha256': hashlib.sha256(Path(p).read_bytes()).hexdigest()}
                                   for p in record['images']])
            message_events[index] = [event['ordinal']]
            if record['program_before_sha256'] != record['program_after_sha256']:
                development.append({'type': 'workspace_change', 'timestamp': event['timestamp'],
                    'payload': {'program_sha256': record['program_after_sha256'],
                                'modified': [record['arguments']['path']]}})
    return {'case_id': checkpoint['task_id'], 'timeline': timeline, 'development': {'events': development}}, message_events


class VerificationReward:
    """Evaluate fresh suffixes; unavailable supervision raises instead of supplying a score."""
    def __init__(self, checkpoint, output, client_factory=None):
        self.checkpoint, self.spec = checkpoint, checkpoint['evaluation']
        self.output = Path(output).resolve()
        self.output.mkdir(parents=True, exist_ok=True)
        self.requests = []
        self.before = None
        self.client_factory = client_factory

    def _catalogue(self):
        catalogue = validate_catalogue(read_json(self.spec['catalogue']), allow_draft=self.spec.get('allow_draft', False))
        if catalogue.get('unresolved_check_ids'):
            raise ValueError('Training reward needs resolved, fixed requirements')
        return catalogue

    def _judge(self, stage, prompt, packet, images, schema):
        if self.client_factory:
            client = self.client_factory(stage, schema)
        else:
            config = load_judge_config(self.spec['judge_config'])
            profile = self.spec.get('primary_profile') or config['primary_stage_profiles'][stage]
            client = build_client(config, profile, self.output / 'judge_cache' / stage, response_schema=schema)
        path = self.output / 'inputs' / f'{len(self.requests):04d}-{stage}.json'
        write_json(path, packet)
        response = client.judge(stage, prompt + json.dumps(packet, ensure_ascii=False), images,
                                context={'checkpoint_id': self.checkpoint['id'], 'input_path': str(path)})
        if response.get('error'):
            raise ValueError(response['error'])
        self.requests.append({'stage': stage, 'input_path': str(path),
                              'request_sha256': response['request_sha256'],
                              'response_path': str(Path(client.cache_root) / (response['request_sha256'] + '.json'))})
        return response['parsed']

    async def acceptance(self, environment, label):
        catalogue = self._catalogue()
        probes = self.spec['acceptance_probes']
        if {p['check_id'] for p in probes} != {c['check_id'] for c in catalogue['checks']} or len(probes) != len(catalogue['checks']):
            raise ValueError('Acceptance probes must cover the fixed catalogue exactly once')
        packet = {'checks': catalogue['checks'], 'evidence': [], 'rule_facts': [], 'image_order': []}
        for rid, path in catalogue['references'].items():
            packet['image_order'].append({'role': 'reference', 'reference_id': rid, 'path': path})
        for i, probe in enumerate(probes):
            row = await environment.call('probe', probe, f'{label}-{i}')
            eid = f'{label}:{probe["check_id"]}'
            packet['evidence'].append({'evidence_id': eid, 'check_ids': [probe['check_id']],
                                      'run_status': 'completed', **row})
            packet['image_order'].append({'role': 'evaluator_observation', 'evidence_id': eid,
                                           'path': row['observation']['screenshot']})
            if row['assertions']:
                packet['rule_facts'].append({'check_id': probe['check_id'],
                    'state': 'pass' if all(a['passed'] for a in row['assertions']) else 'fail', 'evidence_ids': [eid]})
        for i, view in enumerate(packet['image_order']):
            view['attachment_index'] = i + 1
        exact = {r['check_id']: r for r in packet['rule_facts']}
        if len(exact) == len(probes):
            states = list(exact.values())
        else:
            images = [v['path'] for v in packet['image_order']]
            states = validate_states(self._judge('visual_rs', STATE_PROMPT, packet, images, STATE_SCHEMA), packet)
        if any(r['state'] == 'unknown' for r in states):
            raise ValueError('Acceptance lacks decisive evidence for this checkpoint')
        write_json(self.output / f'{label}-acceptance.json', {'packet': packet, 'states': states})
        return {r['check_id']: r['state'] for r in states}

    async def begin(self, environment):
        if self.checkpoint['kind'] == 'repair':
            self.before = await self.acceptance(environment, 'before')

    def _diagnosis(self, suffix):
        packet = copy.deepcopy(read_json(self.spec['diagnosis_packet']))
        catalogue = self._catalogue()
        if not packet.get('observation_verdicts') or not self.spec.get('observation_source'):
            raise ValueError('Diagnosis checkpoints require independent observation labels and their source')
        if any(t['actual_state'] not in {'pass', 'fail'} for t in packet['observation_verdicts']):
            raise ValueError('Resolve observation truth before sampling a diagnosis group')
        visible_hashes = {hashlib.sha256(Path(p).read_bytes()).hexdigest() for p in image_paths(self.checkpoint['messages'])}
        for view in packet['image_order']:
            if view['role'] == 'agent_observation':
                source_path = view.get('source_path', view['path'])
                if hashlib.sha256(Path(source_path).read_bytes()).hexdigest() not in visible_hashes:
                    raise ValueError('Diagnosis reward uses an observation absent from the student prefix')
                if view.get('sha256') and hashlib.sha256(Path(view['path']).read_bytes()).hexdigest() != view['sha256']:
                    raise ValueError('Derived image view changed after observation evaluation')
        cutoff = self.checkpoint.get('source', {}).get('cutoff_event_id')
        if cutoff is not None and any(e['ordinal'] > cutoff for e in packet['events'] if e['kind'] in {'action', 'observation'}):
            raise ValueError('Diagnosis packet contains future evidence')
        # Remove any historical answer. All generated responses receive new original IDs.
        packet['events'] = [e for e in packet['events'] if e['kind'] in {'action', 'observation'}]
        next_id = max(e['ordinal'] for e in packet['events']) + 1
        packet['eligible_diagnosis_event_ids'] = []
        for message in suffix:
            if message['role'] != 'assistant' or message.get('tool_calls'):
                raise ValueError('Fixed-evidence diagnosis rollouts must produce a judgment without tools')
            packet['events'].append({'ordinal': next_id, 'kind': 'model_text', 'text': message.get('content', '')})
            packet['eligible_diagnosis_event_ids'].append(next_id)
            next_id += 1
        packet['diagnosis_event_ids'] = packet['eligible_diagnosis_event_ids']
        packet['observation_cutoff'] = next_id - 1
        images = [v['path'] for v in packet['image_order']]
        stage = 'visual_bda' if packet['recorded_image_event_ids'] else 'text_bda'
        parsed = self._judge(stage, PROMPTS['BDA'], packet, images,
                             stage_schema('BDA', catalogue, packet['targets'], packet))
        targets = validate_stage('BDA', parsed, packet, catalogue)
        return {**diagnosis_reward(targets), 'targets': targets}

    def _evidence(self, suffix, records):
        run, message_events = trajectory(self.checkpoint, suffix, records)
        path = self.output / 'run.json'
        write_json(path, run)
        if self.client_factory:
            judge = self.client_factory('verification_filter', None)
        else:
            config = load_judge_config(self.spec['judge_config'])
            judge = build_client(config, self.spec.get('primary_profile') or config['primary_stage_profiles']['verification_filter'],
                                 self.output / 'judge_cache' / 'verification_filter')
        rounds = extract_verification_rounds(path, judge)
        write_json(self.output / 'verification_rounds.json', rounds)
        catalogue = self._catalogue()
        task = Path(self.spec['task_prompt']).read_text()
        if hashlib.sha256(task.encode()).hexdigest() != catalogue['task_sha256']:
            raise ValueError('Training task and catalogue differ')
        targets = []
        for episode in rounds['episodes']:
            for cutoff in round_cutoffs(rounds, episode):
                packet, images = build_packet(rounds, episode, cutoff, task, catalogue,
                                               list(catalogue['references']), self.output)
                packet['prior_policy_context'] = [m for m in self.checkpoint['messages'] if m['role'] == 'assistant']
                modality = 'visual' if packet['recorded_image_event_ids'] else 'text'
                coverage = validate_stage('VC', self._judge(modality + '_vc', PROMPTS['VC'], packet, images,
                                          stage_schema('VC', catalogue, packet=packet)), packet, catalogue)
                if coverage:
                    supplied = stage_packet('CV', packet, coverage)
                    valid = validate_stage('CV', self._judge(modality + '_cv', PROMPTS['CV'], supplied, images,
                                           stage_schema('CV', catalogue, coverage, supplied)), supplied, catalogue)
                    by_id = {t['target_id']: t for t in valid}
                    targets.extend({**t, **{k: by_id[t['target_id']][k] for k in ('method_ok', 'evidence_ok')}}
                                   for t in coverage)
        return {**evidence_reward(targets, [c['check_id'] for c in catalogue['checks']],
                                  self.spec.get('covered_check_ids', [])), 'targets': targets,
                'message_event_ids': message_events}

    async def score(self, suffix, records, environment=None):
        kind = self.checkpoint['kind']
        if kind == 'diagnosis':
            result = self._diagnosis(suffix)
        elif kind == 'evidence':
            result = self._evidence(suffix, records)
        elif kind == 'repair':
            if self.before is None:
                raise ValueError('Evaluate the restored baseline before generating a repair')
            after = await self.acceptance(environment, 'after')
            result = repair_reward(self.before, after, self.spec['target_ids'],
                edited=any(r['program_before_sha256'] != r['program_after_sha256'] for r in records),
                regression_weight=self.spec.get('regression_weight', 1.0))
            result.update(before=self.before, after=after)
        else:
            # The command is configured by the researcher and is never part of the agent prompt.
            completed = subprocess.run(self.spec['official_command'], input=json.dumps({
                'workspace': str(environment.env.workspace), 'task_id': self.checkpoint['task_id']}),
                text=True, capture_output=True, timeout=self.spec.get('official_timeout', 600), check=True)
            raw = json.loads(completed.stdout)
            if (not math.isfinite(float(raw['max_score'])) or not 0 < float(raw['max_score'])
                    or not 0 <= float(raw['score']) <= float(raw['max_score'])):
                raise ValueError('Official runner must return finite score and positive max_score')
            result = {'score': float(raw['score']) / float(raw['max_score']), 'official_result': raw}
        if not math.isfinite(result['score']):
            raise ValueError('Reward must be finite')
        result.update(checkpoint_id=self.checkpoint['id'], kind=kind, suffix_sha256=content_hash(suffix),
                      requests=self.requests, status='evaluated')
        write_json(self.output / 'reward.json', result)
        return result
