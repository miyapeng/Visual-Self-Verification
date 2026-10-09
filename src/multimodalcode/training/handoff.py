"""Validated handoffs, audited teacher suffixes, and framework data exports."""
from __future__ import annotations

import copy
import hashlib
import json
import math
from pathlib import Path

from multimodalcode.io import read_json
from multimodalcode.vsv_eval.catalogue import content_hash

KINDS = ('task', 'evidence', 'diagnosis', 'repair')


def image_paths(messages):
    paths = []
    for message in messages:
        content = message.get('content', '') or ''
        if isinstance(content, list):
            for part in content:
                if part.get('type') == 'image':
                    path = Path(part['image']).resolve()
                    if not path.is_file():
                        raise FileNotFoundError(path)
                    paths.append(str(path))
                elif part.get('type') != 'text':
                    raise ValueError('Training messages support text and local images only')
        elif not isinstance(content, str):
            raise ValueError('Message content must be text or a list of content blocks')
    return paths


def validate_messages(messages, *, handoff=False):
    if not isinstance(messages, list) or not messages:
        raise ValueError('A handoff requires the actual visible message history')
    pending, seen = set(), set()
    for message in messages:
        if message.get('role') not in {'system', 'user', 'assistant', 'tool'}:
            raise ValueError('Unsupported message role')
        if message.get('role') == 'assistant' and isinstance(message.get('content'), list):
            raise ValueError('Assistant outputs must be text, with optional native tool_calls')
        if message['role'] != 'tool' and pending:
            raise ValueError('Complete all paired tool returns before continuing the dialogue')
        if message['role'] == 'assistant':
            for call in message.get('tool_calls', []):
                if not call.get('id') or call['id'] in seen:
                    raise ValueError('Native tool calls require unique IDs')
                pending.add(call['id'])
                seen.add(call['id'])
        elif message['role'] == 'tool':
            if message.get('tool_call_id') not in pending:
                raise ValueError('A tool result must pair with an actual preceding call')
            pending.remove(message['tool_call_id'])
    image_paths(messages)
    if handoff and (pending or messages[-1]['role'] not in {'user', 'tool'}):
        raise ValueError('Stop a handoff after a complete user/tool message, not inside an assistant turn')


def make_checkpoint(*, task_id, benchmark, kind, messages, policy_revision, split,
                    environment=None, evaluation=None, source=None):
    if kind not in KINDS or split not in {'train', 'validation', 'test'}:
        raise ValueError('Invalid decision kind or dataset split')
    if not task_id or not policy_revision:
        raise ValueError('Task and source policy identities are required')
    validate_messages(messages, handoff=True)
    if kind != 'diagnosis' and environment is None:
        raise ValueError('Execution-based handoffs require a restorable environment')
    if kind == 'repair':
        indices = (source or {}).get('diagnosis_message_indices', [])
        if (not indices or any(type(i) is not int or not 0 <= i < len(messages)
                               or messages[i]['role'] != 'assistant' or not messages[i].get('content') for i in indices)):
            raise ValueError('Repair handoffs must reference an expressed diagnosis in the visible prefix')
    row = {'schema': 'verification-handoff-1', 'task_id': task_id, 'benchmark': benchmark,
           'kind': kind, 'split': split, 'policy_revision': policy_revision,
           'messages': copy.deepcopy(messages), 'environment': copy.deepcopy(environment),
           'evaluation': copy.deepcopy(evaluation or {}), 'source': copy.deepcopy(source or {})}
    row['image_sha256'] = {p: hashlib.sha256(Path(p).read_bytes()).hexdigest()
                           for p in image_paths(messages)}
    files = []
    for key in ('catalogue', 'diagnosis_packet', 'task_prompt', 'observation_source'):
        if row['evaluation'].get(key):
            path = Path(row['evaluation'][key]).resolve()
            files.append(str(path))
            row['evaluation'][key] = str(path)
            if key == 'catalogue':
                files += list(read_json(path).get('references', {}).values())
            elif key == 'diagnosis_packet':
                files += [v['path'] for v in read_json(path).get('image_order', [])]
    row['evaluation_sha256'] = {str(Path(p).resolve()): hashlib.sha256(Path(p).read_bytes()).hexdigest() for p in files}
    row['id'] = content_hash(row)
    return row


def load_checkpoint(path, *, expected_split=None):
    row = read_json(path)
    if row.get('schema') != 'verification-handoff-1' or row.get('id') != content_hash(
            {k: v for k, v in row.items() if k != 'id'}):
        raise ValueError('Checkpoint contents changed or schema is unsupported')
    validate_messages(row['messages'], handoff=True)
    if expected_split and row['split'] != expected_split:
        raise ValueError('Checkpoint split differs from the requested use')
    for p, digest in {**row['image_sha256'], **row['evaluation_sha256']}.items():
        if hashlib.sha256(Path(p).read_bytes()).hexdigest() != digest:
            raise ValueError('Checkpoint image contents changed')
    return row


def candidate_handoffs(rounds):
    """Locate handoff boundaries using existing rounds; never reconstruct hidden context."""
    events = rounds['events']
    previous = {e['ordinal']: events[i - 1]['ordinal'] if i else None for i, e in enumerate(events)}
    by_id = {e['ordinal']: e for e in events}
    result = []
    for episode in rounds['episodes']:
        core = [by_id[i] for i in episode['core_event_ids']]
        actions = [e['ordinal'] for e in core if e['kind'] == 'action']
        observations = [e['ordinal'] for e in core if e['kind'] == 'observation']
        points = [('evidence', previous[actions[0]])] if actions else []
        if observations:
            points.append(('diagnosis', observations[-1]))
        for link in episode.get('repair_links', []):
            if link['repair_event_ids']:
                points.append(('repair', previous[min(link['repair_event_ids'])]))
        for kind, cutoff in points:
            if cutoff is not None:
                result.append({'episode_id': episode['episode_id'], 'kind': kind,
                               'cutoff_event_id': cutoff})
    return result


def _swift_message(message, loss):
    row = copy.deepcopy(message)
    content = row.get('content', '') or ''
    images = []
    if isinstance(content, list):
        parts = []
        for item in content:
            if item['type'] == 'image':
                parts.append('<image>')
                images.append(str(Path(item['image']).resolve()))
            else:
                if '<image>' in item['text']:
                    raise ValueError('Literal image placeholders are ambiguous in text')
                parts.append(item['text'])
        content = ''.join(parts)
    elif '<image>' in content:
        raise ValueError('Supply image blocks, not unbound image placeholders')
    # Retain the generated assistant body. Native calls are rendered only for SFT;
    # the RL adapter never takes this serialization path.
    calls = row.pop('tool_calls', [])
    if calls and '<tool_call>' in content:
        raise ValueError('Use native tool_calls or rendered tool text, not both')
    for call in calls:
        function = copy.deepcopy(call['function'])
        if isinstance(function['arguments'], str):
            function['arguments'] = json.loads(function['arguments'])
        content += '\n<tool_call>\n' + json.dumps(function, ensure_ascii=False) + '\n</tool_call>'
    row = {'role': row['role'], 'content': content}
    if row['role'] == 'assistant':
        row['loss'] = bool(loss)
    return row, images


def export_swift(checkpoint, suffix, audit, *, tools=()):
    """Supervise complete, explicitly reviewed assistant turns only."""
    validate_messages(checkpoint['messages'], handoff=True)
    validate_messages(suffix)
    if checkpoint['split'] != 'train':
        raise ValueError('Only training tasks may enter distillation')
    if (audit.get('checkpoint_id') != checkpoint['id']
            or audit.get('suffix_sha256') != content_hash(suffix)):
        raise ValueError('Teacher review does not match this handoff and suffix')
    if audit.get('status') != 'evaluated' or not audit.get('evidence_refs'):
        raise ValueError('Teacher turns need completed, evidence-linked evaluation')
    actual_images = {p: hashlib.sha256(Path(p).read_bytes()).hexdigest() for p in image_paths(suffix)}
    if audit.get('image_sha256') != actual_images:
        raise ValueError('Teacher image bytes differ from the reviewed evidence')
    keep = audit.get('approved_message_indices')
    if not isinstance(keep, list) or not keep or len(keep) != len(set(keep)):
        raise ValueError('No unique approved teacher turns')
    if any(type(i) is not int or i < 0 or i >= len(suffix) or suffix[i]['role'] != 'assistant' for i in keep):
        raise ValueError('Only existing assistant messages can be supervised')
    # Do not retain an unreviewed trailing suffix after the last approved decision.
    suffix = suffix[:max(keep) + 1]
    messages, images = [], []
    for message, loss in [(m, False) for m in checkpoint['messages']] + [
            (m, i in keep) for i, m in enumerate(suffix)]:
        row, paths = _swift_message(message, loss)
        if messages and row['role'] == messages[-1]['role'] == 'assistant':
            raise ValueError('Consecutive assistant turns can merge loss masks; choose a tool boundary')
        messages.append(row)
        images.extend(paths)
    result = {'messages': messages, 'images': images}
    if tools:
        result['tools'] = json.dumps(tools, ensure_ascii=False)
    return result


def export_rl(checkpoint_path, *, expected_policy_revision=None):
    checkpoint_path = str(Path(checkpoint_path).resolve())
    checkpoint = load_checkpoint(checkpoint_path)
    if expected_policy_revision and checkpoint['policy_revision'] != expected_policy_revision:
        raise ValueError('Refresh checkpoints from the requested student policy')
    # JSON strings avoid Arrow's inability to store string and list content in one column.
    # The small dataset subclass loads the exact native messages on the worker.
    return {'data_source': 'visual_self_verification', 'agent_name': 'verification',
            'prompt': json.dumps(checkpoint['messages'], ensure_ascii=False),
            'reward_model': {'style': 'rule', 'ground_truth': ''},
            'extra_info': {'checkpoint_path': checkpoint_path, 'checkpoint_id': checkpoint['id'],
                           'kind': checkpoint['kind'], 'task_id': checkpoint['task_id'],
                           'split': checkpoint['split']}}


def validate_splits(rows):
    seen = {}
    for row in rows:
        key = (row['benchmark'], row['task_id'])
        if key in seen and seen[key] != row['split']:
            raise ValueError(f'Task crosses train/evaluation splits: {key}')
        seen[key] = row['split']
    return seen


def validate_native_output(output):
    """Check the verl record without decoding or rebuilding any generated tokens."""
    n = len(output.response_ids)
    if (not n or len(output.response_mask) != n or output.response_logprobs is None
            or len(output.response_logprobs) != n):
        raise ValueError('Enable rollout.calculate_log_probs and retain the native token/mask alignment')
    if not any(output.response_mask) or any(v not in (0, 1) for v in output.response_mask):
        raise ValueError('Invalid native generated-token mask')
    if any(not math.isfinite(p) for p, active in zip(output.response_logprobs, output.response_mask) if active):
        raise ValueError('Nonfinite sampled policy log probabilities')
