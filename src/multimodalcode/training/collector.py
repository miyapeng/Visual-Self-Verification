"""Collect native continuations and audit teacher turns before suffix distillation."""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

from multimodalcode.io import write_json
from multimodalcode.vsv_eval.catalogue import content_hash
from .environment import AsyncWebEnvironment, TOOL_SCHEMAS
from .handoff import image_paths, make_checkpoint
from .reward import VerificationReward


def tool_message(record):
    # Match verl's ToolAgentLoop ordering, including real image inputs.
    content = [{'type': 'image', 'image': p} for p in record['images']]
    content.append({'type': 'text', 'text': record['text']})
    return {'role': 'tool', 'tool_call_id': record['tool_call_id'], 'content': content}


async def collect(checkpoint, backend, output, *, max_turns=8, max_tokens=4096,
                  temperature=0.7, evaluate=True, collect_boundaries=False, policy_revision=None):
    """The same collector can query a student endpoint or a teacher endpoint.

    Collected boundary records are eligible for evidence/repair selection by the
    existing round extractor. They are not relabelled as valid repair handoffs.
    """
    if collect_boundaries and not policy_revision:
        raise ValueError('Specify the actual student policy revision when collecting new handoffs')
    if max_turns < 1 or max_tokens < 1:
        raise ValueError('Generation budgets must be positive')
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=False)
    environment = AsyncWebEnvironment() if checkpoint['environment'] else None
    suffix, responses, records, boundaries = [], [], [], []
    reward = VerificationReward(checkpoint, output / 'evaluation') if evaluate else None
    tools = [] if checkpoint['kind'] == 'diagnosis' else TOOL_SCHEMAS
    try:
        if environment:
            await environment.restore(checkpoint['environment'], output / 'environment')
        if reward:
            await reward.begin(environment)
        for _ in range(max_turns):
            visible = checkpoint['messages'] + suffix
            if collect_boundaries and environment:
                snapshot = await environment.call('snapshot', output / 'boundaries' / str(len(boundaries)))
                boundary = make_checkpoint(task_id=checkpoint['task_id'], benchmark=checkpoint['benchmark'],
                    kind='evidence', messages=visible, policy_revision=policy_revision, split=checkpoint['split'],
                    environment=snapshot, source={'parent_checkpoint_id': checkpoint['id'],
                    'suffix_message_count': len(suffix)}, evaluation={})
                path = output / 'boundaries' / f'{len(boundaries)}.json'
                write_json(path, boundary)
                boundaries.append(str(path))
            response = backend.generate_messages(visible, tools=tools, max_tokens=max_tokens, temperature=temperature)
            responses.append(response)
            write_json(output / 'raw_responses.json', responses)
            choice = response['choices'][0]
            message = copy.deepcopy(choice['message'])
            message['content'] = message.get('content') or ''
            if choice.get('finish_reason') == 'length':
                raise ValueError('Continuation reached the generation limit; increase the budget')
            if message.get('role') != 'assistant':
                raise ValueError('Expected an assistant continuation')
            suffix.append(message)
            calls = message.get('tool_calls', [])
            if not calls:
                break
            if not environment or not tools:
                raise ValueError('Diagnosis continuations cannot request new evidence')
            for call in calls:
                arguments = call['function']['arguments']
                try:
                    arguments = json.loads(arguments) if isinstance(arguments, str) else arguments
                except ValueError:
                    arguments = {'invalid_arguments': arguments}
                record = await environment.call('execute', call['function']['name'], arguments, call['id'])
                records.append(record)
                suffix.append(tool_message(record))
            write_json(output / 'suffix.json', suffix)
        else:
            raise ValueError('Continuation reached the turn limit without a final answer')
        write_json(output / 'suffix.json', suffix)
        write_json(output / 'tool_records.json', records)
        result = await reward.score(suffix, records, environment) if reward else None
        write_json(output / 'collection.json', {'checkpoint_id': checkpoint['id'], 'model': backend.model,
                   'suffix': str(output / 'suffix.json'), 'reward': str(output / 'evaluation/reward.json') if result else None,
                   'boundaries': boundaries})
        return suffix, records, result
    finally:
        if environment:
            await environment.close()


AUDIT_PROMPT = """Review teacher assistant messages for suffix distillation. The student prefix is
context only. Use the independently evaluated evidence, recorded actions and actual tool results.
Approve a complete assistant message only when its claims and actions are supported. Never approve
every message merely because a final task score is high. A failed overall task can contain a valid
local demonstration. Correct acceptance and avoiding unnecessary edits are valid demonstrations.
Evidence acquisition must actually obtain useful feedback. Diagnosis must match the independently
assessed state. Repair actions need target improvement without measured regressions; a later passing
homepage does not validate a different page. Do not approve false claims mixed with valid actions.
When evidence is insufficient, omit that message. Review whole messages; do not rewrite them.
Return only {"approved_message_indices":[existing zero-based assistant message indices]}.
EVIDENCE:
"""


def audit_teacher(checkpoint, suffix, records, result, judge, output):
    """Use a separate reviewer call; save the exact labels that determine the SFT mask."""
    if (result.get('status') != 'evaluated' or result['checkpoint_id'] != checkpoint['id']
            or result['suffix_sha256'] != content_hash(suffix)):
        raise ValueError('Teacher audit must use the evaluation of this exact suffix')
    indices = [i for i, m in enumerate(suffix) if m['role'] == 'assistant']
    evidence = {'decision_kind': checkpoint['kind'], 'prefix': checkpoint['messages'],
                'suffix': suffix, 'tool_records': records, 'evaluation': result,
                'assistant_message_indices': indices}
    paths = list(dict.fromkeys(image_paths(checkpoint['messages'] + suffix)))
    # Include independent acceptance packets when reviewing a repair, not just scalar reward.
    output = Path(output).resolve()
    evaluation_output = output.parent / 'evaluation'
    for label in ('before', 'after'):
        path = evaluation_output / f'{label}-acceptance.json'
        if path.is_file():
            from multimodalcode.io import read_json
            evidence[label + '_acceptance'] = read_json(path)
            paths.extend(v['path'] for v in evidence[label + '_acceptance']['packet']['image_order'])
    paths = list(dict.fromkeys(paths))
    evidence['image_order'] = paths
    response = judge.judge('teacher_audit', AUDIT_PROMPT + json.dumps(evidence, ensure_ascii=False), paths,
                           context={'checkpoint_id': checkpoint['id']})
    value = response.get('parsed')
    if response.get('error') or not isinstance(value, dict) or set(value) != {'approved_message_indices'}:
        raise ValueError('Teacher reviewer returned invalid annotations')
    approved = value['approved_message_indices']
    if (not isinstance(approved, list) or any(type(i) is not int or i not in indices for i in approved)
            or len(approved) != len(set(approved))):
        raise ValueError('Teacher reviewer must reference unique existing assistant messages')
    result = {'status': 'evaluated', 'checkpoint_id': checkpoint['id'], 'suffix_sha256': content_hash(suffix),
              'approved_message_indices': approved, 'image_sha256': {
                  p: hashlib.sha256(Path(p).read_bytes()).hexdigest() for p in image_paths(suffix)},
              'evidence_refs': [str(Path(judge.cache_root) / (response['request_sha256'] + '.json'))]}
    write_json(output, result)
    return result
