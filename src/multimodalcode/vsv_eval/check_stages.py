"""Separate attempted checks, independent observations, and agent verdicts."""
from __future__ import annotations

import json
from pathlib import Path

from multimodalcode.io import write_json
from .catalogue import content_hash, REQUIREMENT_POLICY
from .judge import rows_schema


PROMPTS = {
    'VC': """Identify conditions the agent actually checked or attempted in core_event_ids.
Use the paired actions and returns, preserving failed attempts. A plan alone is not a check.
Exclude task understanding and unrelated environment exploration. Context may supply a shared
capture, setup, or earlier step of a continuing test, but not a new independent inspection.
Describe each attempted condition at its actual scope. Match an equivalent catalogue condition
or a directly exercised part; otherwise use check_id=null. Keep legitimate unmatched checks.
A non-null catalogue ID occurs at most once. Reuse previous_target_conditions identities when
equivalent. Group observations of the same condition without broadening it to a whole page.
Scope coverage is full, partial, or none. Full requires the entire matched criterion to be
exercised and sufficiently observed; finding an application failure can be full coverage.
A narrower check is partial. Use unknown only when the check's scope cannot be established.
A direct URL visit does not test the link that would navigate there. Static images cannot prove
unexecuted interactions. Textual UI observations can establish behavior they actually test.
Do not infer successful execution from an agent claim, future plan, or screenshot filename.
A generic capture attempt without image receipt does not establish checks of pictured content.
Full coverage must cite actual tool feedback. Reference images specify requirements; they are
not observations of the generated artifact. Preserve image capture versions.
Modality follows THIS condition's cited inputs: visual if evidence_ids includes a recorded image
receipt, otherwise text. Images and text together count as visual. Do not cite unrelated images
to change modality. Missing archived pixels cannot establish visual full coverage.
Each row must cite a current core action or observation. No targets means an empty list.
Return only {"targets":[{"target":"actual attempted condition","evidence_ids":[],
"modality":"text|visual","check_id":null,"coverage":"full|partial|none|unknown"}]}.
Do not assess artifact correctness, agent diagnosis, repair, or scores.
EVIDENCE:
""",
    'CV': """Assess every supplied attempted condition, without changing its scope or identity.
method_ok: could the executed method distinguish satisfaction from violation?
evidence_ok: did the agent receive sufficient feedback for this condition and implementation?
Judge the attempted scope, not a larger catalogue condition or the complete multi-step plan.
Use preceding setup and shared evidence from context; a split does not require repeating setup.
Programmatic UI feedback may establish behavior without images. Visual appearance requires
actual image input. A filename, an exit code, or agent narration alone cannot establish it.
A valid check may expose a failing application; a failed tool with no adequate feedback does
not provide sufficient evidence. Keep failed attempts. Historical captures retain their version
and cannot stand in for observations of a later implementation. Reference images are requirements.
Return true or false when decidable; null is only an evaluation evidence/method gap, not a default
for failed application checks. Cite original operations and observed returns.
Return every supplied target exactly once, with no coverage, diagnosis, repair or score labels.
Return only {"targets":[{"target_id":"supplied ID","method_ok":null,"evidence_ok":null,"evidence_ids":[]}]}.
EVIDENCE:
""",
    'BDA': """Compare the agent's diagnosis with the fixed observation_verdicts for each target.
Do not reassess or rewrite the actual state. Coverage and validity verdicts are withheld.
Use the supplied target's actual scope, not additional requirements suggested by its opaque ID.
Read the last substantive current judgment in eligible_diagnosis_event_ids. A response may
judge preceding evidence and plan the next check; only the former is a current diagnosis.
Return agent_state=pass|fail|absent|uncertain and original diagnosis_ids. absent means no
relevant conclusion and requires []; uncertain means an explicit abstention with a citation.
A broad 'looks good' applies to its stated visual scope, not untested interactions. Do not
manufacture per-target judgments from silence, image-size notices or earlier context statements.
For a failing state and a fail diagnosis, issue_match is true only if the agent identifies the
same object and observable symptom. Root-cause diagnosis is not required. A vague or unrelated
claimed defect is false. Use null when inapplicable or evidence needed for comparison is missing.
Actual state refers to the artifact, not whether the command completed: correctly reporting an
observed error is a fail diagnosis of a failing condition, not a pass/pass pair.
Only evidence available at the diagnosis can support it. Do not use a hypothesis preceding the
observations as a diagnosis of later results. If no subsequent relevant response exists, use
absent. Treat references as requirements and preserve capture-time versions.
Return every supplied target exactly once. Do not create targets, reasons or scores.
Return only {"targets":[{"target_id":"supplied ID","agent_state":"pass|fail|absent|uncertain",
"issue_match":null,"diagnosis_ids":[]}]}.
EVIDENCE:
""",
}


PROMPTS['OBSERVATION'] = REQUIREMENT_POLICY + """Assess the fixed catalogue from the current raw observations.
The agent's judgments, coverage labels and validity labels are withheld. Examine every
supplied requirement, including satisfied conditions and defects the agent never mentioned.
Return each catalogue ID exactly once. Use pass when the full criterion is supported, fail
when an observed violation establishes failure, and not_observable when the current inspection
cannot decide it. A screenshot does not establish an unexecuted interaction; logs can establish
textual conditions without pixels. Do not treat an irrelevant or unobserved requirement as failed.
Cite actual tool returns from the current core. Context may establish prerequisites and shared
capture sources but cannot create a new assessment of a different page or earlier version.
Preserve capture-time state. Do not infer visual truth from filenames or agent narration.
For fail, actual_issue identifies the object and observable symptom, not a conjectured cause.
For pass and not_observable, actual_issue is null. not_observable requires evidence_ids=[].
Do not use not_observable to avoid deciding an observable condition. Apply the fixed tolerances
consistently to both correct and incorrect implementations. Preserve exact rule_facts.
Return only {"targets":[{"target_id":"catalogue ID","actual_state":"pass|fail|not_observable",
"actual_issue":null,"evidence_ids":[]}]}.
EVIDENCE:
"""

def stage_schema(metric, catalogue, targets=(), packet=None):
    if metric == 'OBSERVATION':
        issue = {'anyOf': [{'type': 'null'}, {'type': 'object',
                 'properties': {k: {'type': 'string', 'minLength': 1} for k in ('object', 'symptom')},
                 'required': ['object', 'symptom'], 'additionalProperties': False}]}
        return rows_schema('targets', {
            'target_id': {'type': 'string', 'enum': [c['check_id'] for c in catalogue['checks']]},
            'actual_state': {'type': 'string', 'enum': ['pass', 'fail', 'not_observable']},
            'actual_issue': issue,
            'evidence_ids': {'type': 'array', 'items': {'type': 'string',
                **({'enum': [str(e['ordinal']) for e in packet['events']]} if packet else {})}}})
    evidence = {'type': 'array', 'items': {'type': 'integer'}}
    if packet is not None:
        evidence = {'type': 'array', 'items': {'type': 'string',
                    'enum': [str(e['ordinal']) for e in packet['events']]}}
    boolean = {'type': ['boolean', 'null']}
    identity = {'type': 'string', **({'enum': [t['target_id'] for t in targets]} if targets else {})}
    if metric == 'VC':
        fields = {'target': {'type': 'string', 'minLength': 1},
                  'evidence_ids': evidence, 'modality': {'type': 'string', 'enum': ['text', 'visual']},
                  'check_id': {'type': ['string', 'null'],
                              'enum': [c['check_id'] for c in catalogue['checks']] + [None]},
                  'coverage': {'type': 'string', 'enum': ['full', 'partial', 'none', 'unknown']}}
    elif metric == 'CV':
        fields = {'target_id': identity, 'method_ok': boolean, 'evidence_ok': boolean, 'evidence_ids': evidence}
    else:
        fields = {'target_id': identity, 'actual_state': {'type': 'string', 'enum': ['pass', 'fail', 'unknown']},
                  'actual_issue': {'anyOf': [{'type': 'null'}, {'type': 'object',
                     'properties': {k: {'type': 'string'} for k in ('object', 'symptom')},
                     'required': ['object', 'symptom'], 'additionalProperties': False}]},
                  'agent_state': {'type': 'string', 'enum': ['pass', 'fail', 'absent', 'uncertain']},
                  'issue_match': boolean, 'evidence_ids': evidence, 'diagnosis_ids': evidence}
        if packet is not None:
            eligible = [str(eid) for eid in packet['eligible_diagnosis_event_ids']]
            fields['diagnosis_ids'] = {'type': 'array', 'items': {'type': 'string',
                                       **({'enum': eligible} if eligible else {})},
                                       **({'maxItems': 0} if not eligible else {})}
    if metric == 'BDA' and packet is not None and 'observation_verdicts' in packet:
        fields = {k: fields[k] for k in ('target_id', 'agent_state', 'issue_match', 'diagnosis_ids')}
    schema = rows_schema('targets', fields)
    if metric != 'VC' and not targets:
        schema['properties']['targets']['maxItems'] = 0
    if packet is None or any(i['role'] == 'agent_observation' for i in packet['image_order']):
        return schema
    # Make the existing pixel-availability constraints explicit before generation.
    # Text observations remain assessable even in a round with omitted images.
    variants = []
    for modality in (('text', 'visual') if packet['recorded_image_event_ids'] else ('text',)):
        ids = [t['target_id'] for t in targets if t['modality'] == modality]
        if metric != 'VC' and not ids:
            continue
        variant = dict(fields)
        if metric == 'VC':
            variant['modality'] = {'type': 'string', 'enum': [modality]}
            if modality == 'visual':
                variant['coverage'] = {'type': 'string', 'enum': ['partial', 'none', 'unknown']}
        else:
            variant['target_id'] = {'type': 'string', 'enum': ids}
            if modality == 'visual':
                if metric == 'CV':
                    variant['evidence_ok'] = {'type': 'null'}
                else:
                    if 'actual_state' in variant:
                        variant['actual_state'] = {'type': 'string', 'enum': ['unknown']}
                        variant['actual_issue'] = {'type': 'null'}
                    if 'issue_match' in variant:
                        variant['issue_match'] = {'type': 'null'}
        variants.append(rows_schema('targets', variant)['properties']['targets']['items'])
    if variants:
        schema['properties']['targets']['items'] = variants[0] if len(variants) == 1 else {'anyOf': variants}
    return schema


def validate_stage(metric, value, packet, catalogue):
    targets = packet.get('targets', [])
    fields = stage_schema(metric, catalogue, targets)['properties']['targets']['items']['properties']
    fixed_observations = {t['target_id']: t for t in packet.get('observation_verdicts', [])}
    if metric == 'BDA' and 'observation_verdicts' in packet:
        if set(fixed_observations) != {t['target_id'] for t in targets}:
            raise ValueError('Observation verdicts must cover the exact target set')
        fields = {k: fields[k] for k in ('target_id', 'agent_state', 'issue_match', 'diagnosis_ids')}
    if not isinstance(value, dict) or set(value) != {'targets'} or not isinstance(value['targets'], list):
        raise ValueError(f'{metric} must return only a targets list')
    events = {e['ordinal']: e for e in packet['events']}
    goals = {c['check_id']: c for c in catalogue['checks']}
    identities = {t['target_id']: t for t in targets}
    pixels = {i['event_id'] for i in packet['image_order'] if i['role'] == 'agent_observation'}
    rows, seen = [], set()
    for raw in value['targets']:
        if not isinstance(raw, dict) or set(raw) != set(fields):
            raise ValueError(f'Invalid {metric} fields')
        raw = dict(raw)
        if metric == 'BDA' and fixed_observations:
            if raw['target_id'] not in fixed_observations:
                raise ValueError('BDA refers to an unknown observation target')
            raw = {**raw, **fixed_observations[raw['target_id']]}
        original_ids = {str(eid): eid for eid in events}
        for key in ('evidence_ids', 'diagnosis_ids'):
            if isinstance(raw.get(key), list):
                raw[key] = [original_ids.get(eid, eid) if isinstance(eid, str) else eid for eid in raw[key]]
        for key, definition in fields.items():
            if 'enum' in definition and raw[key] not in definition['enum']:
                raise ValueError(f'Invalid {metric} {key}')
        for key in ('method_ok', 'evidence_ok', 'issue_match'):
            if key in raw and raw[key] is not None and type(raw[key]) is not bool:
                raise ValueError(f'{metric} {key} must be boolean or null')
        for key in ('evidence_ids', 'diagnosis_ids'):
            if key not in raw:
                continue
            ids = raw[key]
            if (not isinstance(ids, list) or any(type(eid) is not int or eid not in events for eid in ids)
                    or len(ids) != len(set(ids))):
                raise ValueError(f'{metric} cites nonexistent, duplicate or future events')
        if metric == 'VC':
            cid = raw['check_id']
            if cid is not None and raw['target'] is None:
                condition = goals[cid]['criterion']
            elif isinstance(raw['target'], str) and raw['target'].strip():
                condition = raw['target']
            else:
                raise ValueError('A VC target needs an attempted condition')
            if not any(eid in packet['core_event_ids'] and events[eid]['kind'] in {'action', 'observation'}
                       for eid in raw['evidence_ids']):
                # History and absent catalogue goals are not new attempts in this round.
                # Failed attempts still cite their current call, even without a result.
                continue
            tid = cid or content_hash(condition)[:16]
            row = {**raw, 'target': condition, 'target_id': tid}
            needs_observation = raw['coverage'] == 'full'
            modality = raw['modality']
            expected_modality = 'visual' if set(raw['evidence_ids']).intersection(packet['recorded_image_event_ids']) else 'text'
            if modality != expected_modality:
                raise ValueError('VC modality differs from its recorded evidence')
        else:
            tid = raw['target_id']
            row = raw
            identity = identities[tid]
            modality = identity['modality']
            needs_observation = (raw['evidence_ok'] is True if metric == 'CV' else raw['actual_state'] != 'unknown')
        if tid in seen:
            raise ValueError(f'Duplicate {metric} target')
        seen.add(tid)
        if needs_observation and not any(events[eid]['kind'] == 'observation' for eid in raw['evidence_ids']):
            raise ValueError(f'{metric} requires actual observed feedback')
        if needs_observation and modality != 'text' and not pixels.intersection(raw['evidence_ids']):
            raise ValueError(f'{metric} requires actual attached agent pixels')
        if metric == 'BDA':
            issue = raw['actual_issue']
            if issue is not None and (not isinstance(issue, dict) or set(issue) != {'object', 'symptom'}
                                      or any(not isinstance(v, str) or not v.strip() for v in issue.values())):
                raise ValueError('Invalid BDA observable issue')
            if (raw['actual_state'] == 'fail') != (issue is not None):
                raise ValueError('Only known BDA failures require an observable issue')
            facts = {f['check_id']: f for f in packet['rule_facts']}
            cid = identities[tid]['check_id']
            if cid in facts and raw['actual_state'] != facts[cid]['state']:
                raise ValueError('BDA contradicted an exact assertion')
        if metric == 'BDA':
            diagnosis = raw['diagnosis_ids']
            if any(eid not in packet['eligible_diagnosis_event_ids'] for eid in diagnosis):
                raise ValueError('BDA diagnosis must cite a current original response')
            if (raw['agent_state'] == 'absent') != (not diagnosis):
                raise ValueError('BDA diagnosis presence differs from its references')
            if raw['actual_state'] == raw['agent_state'] == 'fail' and type(raw['issue_match']) is not bool:
                raise ValueError('Decidable failure diagnoses require a definite issue match')
            if diagnosis and any(eid > max(diagnosis) for eid in raw['evidence_ids']):
                raise ValueError('BDA evidence occurs after the diagnosis')
        rows.append(row)
    if metric != 'VC' and seen != set(identities):
        raise ValueError(f'{metric} omitted supplied targets')
    return rows


def validate_observations(value, packet, catalogue):
    """Truth opportunities come from the fixed catalogue, not the agent's claims."""
    if not isinstance(value, dict) or set(value) != {'targets'} or not isinstance(value['targets'], list):
        raise ValueError('Observation audit must return one targets list')
    goals = {c['check_id']: c for c in catalogue['checks']}
    events = {e['ordinal']: e for e in packet['events']}
    ids = {str(eid): eid for eid in events}
    facts = {f['check_id']: f for f in packet['rule_facts']}
    pixels = {i['event_id'] for i in packet['image_order'] if i['role'] == 'agent_observation'}
    reconstructed = {i['event_id'] for i in packet['image_order']
                     if i['role'] == 'agent_observation' and i.get('origin') == 'reconstructed'}
    rows, seen = [], set()
    for raw in value['targets']:
        if not isinstance(raw, dict) or set(raw) != {'target_id', 'actual_state', 'actual_issue', 'evidence_ids'}:
            raise ValueError('Invalid independent observation fields')
        cid, state, issue = raw['target_id'], raw['actual_state'], raw['actual_issue']
        if cid not in goals or cid in seen or state not in {'pass', 'fail', 'not_observable'}:
            raise ValueError('Unknown or duplicate observation condition/state')
        seen.add(cid)
        if not isinstance(raw['evidence_ids'], list):
            raise ValueError('Observation evidence must be a list')
        evidence = [ids.get(eid, eid) if isinstance(eid, str) else eid for eid in raw['evidence_ids']]
        if (any(type(eid) is not int or eid not in events for eid in evidence)
                or len(evidence) != len(set(evidence))):
            raise ValueError('Observation cites unknown, duplicate or future evidence')
        if (state == 'fail') != (issue is not None) or issue is not None and (
                not isinstance(issue, dict) or set(issue) != {'object', 'symptom'}
                or any(not isinstance(v, str) or not v.strip() for v in issue.values())):
            raise ValueError('A failing observation needs an object and symptom')
        if cid in facts and state != facts[cid]['state']:
            raise ValueError('Observation contradicted an exact assertion')
        if state == 'not_observable':
            if evidence:
                raise ValueError('Non-observable conditions cannot claim observed evidence')
            continue
        if not any(eid in packet['core_event_ids'] and events[eid]['kind'] == 'observation' for eid in evidence):
            raise ValueError('A diagnosis opportunity requires current observed feedback')
        visual = bool(set(evidence) & set(packet['recorded_image_event_ids']))
        if visual and not pixels.intersection(evidence).intersection(packet['core_event_ids']):
            raise ValueError('Visual truth requires attached agent pixels from this round')
        rows.append({**raw, 'evidence_ids': evidence, 'check_id': cid,
                     'target': goals[cid]['criterion'], 'modality': 'visual' if visual else 'text',
                     'evidence_origin': 'reconstructed' if reconstructed.intersection(evidence) else 'original'})
    if seen != set(goals):
        raise ValueError('Independent observation audit omitted catalogue conditions')
    return {'targets': rows, 'not_observable_ids': [cid for cid in goals if cid not in {r['target_id'] for r in rows}]}


def metric_packet(packet, targets):
    """Share target identities and raw evidence, without previous verdicts."""
    # Catalogue conditions define coverage, not the scope of each actual attempt.
    # The original task/references still establish whether that attempt is meaningful.
    return {**{k: v for k, v in packet.items() if k not in {'previous_target_conditions', 'catalogue'}},
            'targets': [{k: t[k] for k in ('target_id', 'target', 'check_id', 'modality')} for t in targets]}


def stage_packet(metric, packet, targets, observations=None):
    supplied = metric_packet(packet, targets)
    if metric == 'OBSERVATION':
        supplied = {k: v for k, v in packet.items() if k not in {
            'previous_target_conditions', 'targets', 'diagnosis_event_ids', 'eligible_diagnosis_event_ids'}}
        supplied['events'] = [e for e in packet['events'] if e['kind'] in {'action', 'observation'}]
        # Do not pass another judge's scope choices or the agent's conclusions.
    elif metric == 'BDA':
        supplied = metric_packet(packet, observations['targets'])
        supplied['observation_verdicts'] = [
            {k: t[k] for k in ('target_id', 'actual_state', 'actual_issue', 'evidence_ids')}
            for t in observations['targets']]
    return supplied


def merge_stages(coverage, validity, diagnoses, observations):
    valid = {t['target_id']: t for t in validity}
    diagnosis = {t['target_id']: t for t in diagnoses}
    checks = [{**t, 'method_ok': valid[t['target_id']]['method_ok'],
               'evidence_ok': valid[t['target_id']]['evidence_ok']} for t in coverage]
    assessments = [{**t, **diagnosis[t['target_id']]} for t in observations['targets']]
    return {'targets': checks, 'assessments': assessments,
            'not_observable_ids': observations['not_observable_ids']}


def judge_round_prefix(packet, images, catalogue, client, output):
    """Batch each role; observation truth is independent of attempted-check labels."""
    if packet['missing_images']:
        raise ValueError('Recover missing observation/reference images before judging this prefix')
    modality = 'visual' if packet['recorded_image_event_ids'] else 'text'
    results, requests = {'VC': [], 'CV': [], 'BDA': []}, []
    for metric in ('VC', 'CV', 'OBSERVATION', 'BDA'):
        if metric == 'CV' and not results['VC']:
            continue
        if metric == 'BDA' and not results['OBSERVATION']['targets']:
            continue
        supplied = packet if metric == 'VC' else stage_packet(metric, packet, results['VC'], results.get('OBSERVATION'))
        stage = f'{modality}_{metric.lower()}'
        judge = client(stage, stage_schema(metric, catalogue, supplied.get('targets', []), supplied))
        input_path = Path(output) / 'inputs' / f"{packet['episode_id']}-{packet['observation_cutoff']}-{metric}.json"
        write_json(input_path, supplied)
        record = judge.judge(stage, PROMPTS[metric] + json.dumps(supplied, ensure_ascii=False), images,
                             context={'episode_id': packet['episode_id'], 'cutoff': packet['observation_cutoff'],
                                      'input_path': str(input_path.resolve())})
        if record.get('error'):
            raise ValueError(f'{metric} failed: {record["error"]}')
        results[metric] = (validate_observations(record['parsed'], supplied, catalogue) if metric == 'OBSERVATION'
                           else validate_stage(metric, record['parsed'], supplied, catalogue))
        requests.append({'metric': metric, 'stage': stage, 'cutoff': packet['observation_cutoff'],
                         'input_path': str(input_path.resolve()), 'request_sha256': record['request_sha256'],
                         'response_path': str((Path(judge.cache_root) / (record['request_sha256'] + '.json')).resolve())})
    return merge_stages(results['VC'], results['CV'], results['BDA'], results['OBSERVATION']), requests
