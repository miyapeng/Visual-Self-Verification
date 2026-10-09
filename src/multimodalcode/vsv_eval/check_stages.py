"""Scoped check judgments and constrained recheck alignment."""
from __future__ import annotations

import json
from pathlib import Path

from multimodalcode.io import write_json
from .catalogue import content_hash, REQUIREMENT_POLICY
from .judge import rows_schema


PROMPTS = {
    'VC': """Evaluate verification coverage only. Return labels, never scores.
Identify conditions actually inspected or attempted in core_event_ids, including observable
defects in the inspected scope. Context provides shared sources, not extra independent checks.
First identify the actual operations and their intended conditions; only then consult the catalogue.
Return only attempted conditions, not one row per catalogue entry. Omit unattempted goals,
including unresolved goals. The program accounts for absent catalogue goals separately.
Each target must cite a core action or observation in evidence_ids, including failed attempts.
An overall plan or concluding summary does not turn past or planned operations into current checks.
Use context for setup, shared captures and earlier steps of a continuing test. Splitting a long
plan into rounds does not require repeating its setup or completing the entire plan in each round.
Inspect the current raw response for each distinct condition the agent actually examined,
including defects it describes that have no catalogue match. Do not stop after mapping the
known catalogue goals: an observed appearance defect can be an unmatched target even when
other conditions on the same page match catalogue entries. Do not substitute an unrelated
nearby catalogue condition for that defect. Prior target identities never expand current scope.
Keep failed, text-only, source/asset diagnostics and unmatched legitimate output checks.
Within an image-reading round, retain text checks actually executed in the core as well,
such as console errors or HTTP results. Image inspection does not replace those checks.
Before returning, verify that every distinct artifact defect explicitly reported in the
current response has a target for that object; use check_id=null when it has no equivalent
catalogue entry. Do not absorb a reported defect into an unrelated nearby catalogue goal.
Exclude task understanding and unrelated environment exploration. Return [] for no checks.
Always describe the actual attempted condition in target, even when it matches a catalogue goal.
Match equivalent conditions, or a directly exercised part of a condition, to catalogue IDs.
Each non-null check_id may occur at most once in this response. Combine evidence for the
same catalogue condition in one row. Distinct conditions without an equivalent catalogue
entry keep check_id=null and distinct target text; do not reuse a nearby catalogue ID.
For a narrower condition keep its actual scope and give only partial catalogue coverage.
Shared subject matter alone is not a match: availability is not navigation, asset diagnostics
are not carousel behavior, and a static view is not an attempted interactive test.
Opening a URL directly can check route loading and page appearance, but it does not test
the link or button that would navigate there. Do not create a link-navigation target for it.
For a static inspection, describe the visible property actually inspected (for example,
navigation appearance), not an unexecuted behavior mentioned in the catalogue. Do not add
a separate interaction target solely because that property belongs to an interactive component.
Keep a genuinely attempted interaction even when it fails to produce feedback. A command
that requests an image but returns only text is an attempted capture, not an inspection of
the image contents. Completed subtests in a timed-out batch retain their own recorded results.
Do not invent particular visual-property targets from a generic screenshot command. When
the image has not been consumed and no text observation inspects those properties, there
is no basis to claim that individual catalogue elements were checked. A failed attempt at
a condition explicitly targeted before the action still remains a target. Preserve the raw
capture operation in the input; do not turn capture preparation into fabricated content checks.
A trailing response can judge the current result and announce the next inspection. Its
future-tense plan is not evidence that the announced inspection occurred in this round.
Use check_id=null for legitimate checks without an equivalent or directly exercised catalogue
condition. Preserve these checks, including interactions not listed in the catalogue.
Do not invent requirements or broaden a condition to a whole page.
Full coverage requires the condition to have been exercised and sufficiently observed.
For a matched goal, coverage refers to the catalogue criterion, not just the narrower target.
Catalogue setup and required_evidence describe an acceptance approach, not an exclusive tool
sequence. Equivalent observations can support behavioral conditions; visual appearance still
requires pixels. Do not infer unexecuted steps of a longer plan from its intended completion.
An observed failure can be full coverage. Static screenshots do not prove interaction behavior.
Use partial for incomplete checking and unknown when necessary observations or the scope of
the required check cannot be established. Reference pictures are requirements, not observations.
Preserve image capture versions. unresolved_check_ids flags ambiguous acceptance criteria,
not missing inspections. A source conflict about the expected text/value does not prevent full
coverage when the required object and properties were actually inspected with sufficient evidence.
Do not resolve which conflicting value is correct. Merely appearing somewhere in a screenshot
does not establish an attempted check; use the actual inspection scope and responses.
Evidence IDs must cite actual observed feedback. Visual full coverage needs attached agent pixels.
Full coverage must cite at least one event with kind=observation. A model_text or reasoning
event claiming a successful check is not tool feedback, even when it describes console output.
If no observation supports that condition, do not assign full coverage.
Choose modality from the evidence actually cited for this target: visual if any evidence ID
is in recorded_image_event_ids, otherwise text. Image and text together count as visual.
Apply this rule separately to each row, not to the whole round or intended test type.
A failed capture with no cited image receipt is text even if another target in this round
uses an image. Never add an unrelated image ID just to classify an attempt as visual.
Recorded image receipt remains visual when its archived file is missing. Reference images,
image filenames, tool metadata and agent narration do not establish image receipt.
If image_order contains no agent_observation attachment, visual targets cannot have full
coverage. When missing archived pixels prevent assessing coverage, use unknown, not none:
missing evidence is not evidence that the agent skipped the inspection.
For an equivalent previous_target_conditions entry, reuse its exact target text and check_id.
These entries supply identities only, never prior verdicts.
Do not assess artifact pass/fail, diagnosis correctness, repair, or any other metric.
Write target and evidence_ids before the catalogue mapping. For a static inspection,
explicitly say appearance or visible content in target; copy a catalogue criterion only
when the actual operation exercises the whole criterion, including its behaviors.
Return only {"targets":[{"target":"actual attempted condition","evidence_ids":[],
"modality":"text|visual","check_id":null,"coverage":"full|partial|none|unknown"}]}.
EVIDENCE:
""",
    'CV': """Evaluate check validity only for every supplied target_id.
Judge method_ok: could these actual operations distinguish satisfaction from violation?
Judge evidence_ok: did the agent receive sufficient evidence for this condition, version and state?
Programmatic UI observations (DOM values, URLs, counts and assertions) can be sufficient for
behavioral conditions. Do not require pixels for a condition fully observable in those returns.
Counts alone do not prove content correctness; assess what the actual assertion establishes.
Judge the supplied target's actual scope. Its catalogue match is a coverage reference, not
permission to replace that target with a broader condition. A valid limited check need not
complete an entire task, catalogue criterion or multi-step plan.
Consider necessary setup and earlier steps from context together with the current operation.
Do not invalidate a check because the extractor put its setup in another round. Conversely,
future planned actions supply no evidence and historical checks are not new attempts here.
Use true, false or null for unavailable evidence or an ambiguous check method. Tool failures are retained;
a failed application test can still be a valid check. A screenshot alone cannot test an interaction.
unresolved_check_ids does not automatically invalidate the method or observation. Conflicting
expected text can still be inspected; use null only when the actual check's adequacy is undecidable.
Use original feedback IDs for the supporting operations/results, preserving capture-time versions.
Reference images are requirements, not observations. Core events anchor this inspection;
context may supply prerequisites and relevant evidence, preserving its original state/version.
For visual targets with no attached agent_observation pixels, evidence_ok is null. Recorded
image receipt alone does not establish what the returned image showed or whether it sufficed.
Return every supplied target exactly once. Do not create targets or assess coverage, artifact state,
agent diagnosis, repair, or any other metric. No other model's judgments are supplied.
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


# Judge existing checks and scan current image evidence for overlooked defects.
# These are model observations, not independently calibrated ground-truth labels.
PROMPTS['OBSERVATION'] = REQUIREMENT_POLICY + """Judge the supplied target conditions from tool actions,
actual tool returns, task sources and attached observation/reference images. The agent's
narrative and reasoning are withheld. A target describes what to examine, not its verdict.
Use pass or fail when the observations decide that condition. Do not demand proof of unrelated
requirements, hidden root causes or unattempted behaviors. A static image cannot prove an
interaction. A failed test command alone does not prove the application is defective.
Keep properties distinct: a visible heading does not specify browser-tab metadata, and
asset availability does not establish its visual appearance. Source examples do not require
verbatim values unless explicitly mandated. For minor conflicts between text and prototype,
accept either supplied variant when required meaning and functionality are preserved.
Use unknown only if required evidence is genuinely unavailable or materially contradictory.
For a failure identify the observed object and symptom, not a speculative cause.
Respect capture-time versions, original evidence IDs and exact rule_facts. Visual judgments
require attached observation pixels. References define requirements, not observed state.
Return every supplied target exactly once. Also inspect the current agent-observation images
against the task and catalogue for material visible defects missing from the supplied targets.
Do not limit this scan to objects named in the targets. Do not infer correctness from the
agent's choice of checks. Additional defects need sufficient, directly visible evidence in
a core image event; shared captures, reference images and historical context alone do not qualify.
Do not infer untested behavior, off-screen defects, hidden causes or pixel-level styling
preferences. Use the same requirement tolerances as for supplied targets. Preserve image
capture versions; an old image does not establish the current implementation's condition.
For additional_defects describe the expected condition in target, not an agent judgment.
Use an equivalent catalogue check_id, or null for a task-grounded unmatched condition.
An existing target or catalogue check_id must not be repeated as an additional defect:
put failures within an existing target's scope in its normal targets row. Do not broaden
a narrower target to include unobserved behavior. Group symptoms of the same condition.
Reuse equivalent previous_target_conditions identities; these contain no prior verdicts.
If no additional defect is established, return additional_defects=[]. Missing pixels are
not a clean bill of health: keep unknown supplied targets, and do not invent extra defects.
Do not assess agent diagnosis, coverage or scores.
Return only {"targets":[{"target_id":"supplied ID","actual_state":"pass|fail|unknown",
"actual_issue":null,"evidence_ids":[]}],"additional_defects":[{"target":"expected condition",
"check_id":null,"actual_issue":{"object":"...","symptom":"..."},"evidence_ids":[]}]}.
EVIDENCE:
"""

def stage_schema(metric, catalogue, targets=(), packet=None):
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
    if metric == 'OBSERVATION':
        fields = {k: fields[k] for k in ('target_id', 'actual_state', 'actual_issue', 'evidence_ids')}
    elif metric == 'BDA' and packet is not None and 'observation_verdicts' in packet:
        fields = {k: fields[k] for k in ('target_id', 'agent_state', 'issue_match', 'diagnosis_ids')}
    schema = rows_schema('targets', fields)
    if metric != 'VC' and not targets:
        schema['properties']['targets']['maxItems'] = 0
    if metric == 'OBSERVATION':
        defect_fields = {
            'target': {'type': 'string', 'minLength': 1},
            'check_id': {'type': ['string', 'null'], 'enum': [c['check_id'] for c in catalogue['checks']
                if c['check_id'] not in {t['check_id'] for t in targets}] + [None]},
            'actual_issue': fields['actual_issue']['anyOf'][1], 'evidence_ids': evidence,
        }
        schema['properties']['additional_defects'] = rows_schema('additional_defects', defect_fields)['properties']['additional_defects']
        schema['required'].append('additional_defects')
        if packet is not None and not current_image_ids(packet):
            schema['properties']['additional_defects']['maxItems'] = 0
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
        if metric in {'OBSERVATION', 'BDA'}:
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
            if diagnosis and any(eid > max(diagnosis) for eid in raw['evidence_ids']):
                raise ValueError('BDA evidence occurs after the diagnosis')
        rows.append(row)
    if metric != 'VC' and seen != set(identities):
        raise ValueError(f'{metric} omitted supplied targets')
    return rows


def current_image_ids(packet):
    return {i['event_id'] for i in packet['image_order'] if i['role'] == 'agent_observation'
            and i['event_id'] in packet['core_event_ids']}


def validate_observations(value, packet, catalogue):
    """Add only evidenced defects; the model does not assign their IDs or coverage."""
    if not isinstance(value, dict) or set(value) != {'targets', 'additional_defects'}:
        raise ValueError('Observation audit requires targets and additional_defects; rerun older judgments')
    rows = validate_stage('OBSERVATION', {'targets': value['targets']}, packet, catalogue)
    if not isinstance(value['additional_defects'], list):
        raise ValueError('additional_defects must be a list')
    existing = packet['targets']
    seen = {t['target_id'] for t in existing}
    conditions = {t['target'].strip().casefold() for t in existing}
    goals = {c['check_id'] for c in catalogue['checks']}
    events = {e['ordinal']: e for e in packet['events']}
    original_ids = {str(eid): eid for eid in events}
    defects = []
    for raw in value['additional_defects']:
        if not isinstance(raw, dict) or set(raw) != {'target', 'check_id', 'actual_issue', 'evidence_ids'}:
            raise ValueError('Invalid additional defect fields')
        condition, cid = raw['target'], raw['check_id']
        if (not isinstance(condition, str) or not condition.strip()
                or cid is not None and (not isinstance(cid, str) or cid not in goals)):
            raise ValueError('Additional defect needs a condition and a valid catalogue reference')
        tid = cid or content_hash(condition)[:16]
        if tid in seen or condition.strip().casefold() in conditions:
            raise ValueError('Duplicate additional defect or existing target')
        ids = raw['evidence_ids']
        if not isinstance(ids, list):
            raise ValueError('Additional defect evidence_ids must be a list')
        ids = [original_ids.get(eid, eid) if isinstance(eid, str) else eid for eid in ids]
        if (any(type(eid) is not int or eid not in events for eid in ids)
                or len(ids) != len(set(ids))):
            raise ValueError('Additional defect cites unknown, duplicate or future evidence')
        if not current_image_ids(packet).intersection(ids):
            raise ValueError('Additional defect requires attached pixels from a current core observation')
        identity = {'target_id': tid, 'target': condition, 'check_id': cid, 'modality': 'visual'}
        observation = {'target_id': tid, 'actual_state': 'fail',
                       'actual_issue': raw['actual_issue'], 'evidence_ids': ids}
        # Reuse state, assertion and observable-issue validation for discovered defects.
        validate_stage('OBSERVATION', {'targets': [observation]}, {**packet, 'targets': [identity]}, catalogue)
        defects.append({**identity, **observation, 'check_attempted': False})
        seen.add(tid)
        conditions.add(condition.strip().casefold())
    return {'targets': rows, 'additional_defects': defects}


def metric_packet(packet, targets):
    """Share target identities and raw evidence, without previous verdicts."""
    # Catalogue conditions define coverage, not the scope of each actual attempt.
    # The original task/references still establish whether that attempt is meaningful.
    return {**{k: v for k, v in packet.items() if k not in {'previous_target_conditions', 'catalogue'}},
            'targets': [{k: t[k] for k in ('target_id', 'target', 'check_id', 'modality')} for t in targets]}


def stage_packet(metric, packet, targets, observations=()):
    supplied = metric_packet(packet, targets)
    if metric == 'OBSERVATION':
        supplied['events'] = [e for e in supplied['events'] if e['kind'] in {'action', 'observation'}]
        supplied['catalogue'] = packet['catalogue']
        supplied['previous_target_conditions'] = packet.get('previous_target_conditions', [])
    elif metric == 'BDA' and observations:
        supplied = metric_packet(packet, targets + observations['additional_defects'])
        supplied['observation_verdicts'] = observations['targets'] + [
            {k: t[k] for k in ('target_id', 'actual_state', 'actual_issue', 'evidence_ids')}
            for t in observations['additional_defects']]
    return supplied


def merge_stages(coverage, validity, diagnoses, observations=None):
    valid = {t['target_id']: t for t in validity}
    diagnosis = {t['target_id']: t for t in diagnoses}
    rows = [{**t, 'method_ok': valid[t['target_id']]['method_ok'],
             'evidence_ok': valid[t['target_id']]['evidence_ok'],
             **{k: v for k, v in diagnosis[t['target_id']].items()
                if k not in {'target_id', 'evidence_ids'}}} for t in coverage]
    for t in (observations or {}).get('additional_defects', []):
        rows.append({**t, **diagnosis[t['target_id']], 'coverage': 'none',
                     'method_ok': None, 'evidence_ok': None})
    return rows


def judge_round_prefix(packet, images, catalogue, client, output):
    """One metric per call; all targets in this prefix remain batched."""
    modality = 'visual' if packet['recorded_image_event_ids'] else 'text'
    results, requests = {}, []
    for metric in ('VC', 'CV', 'OBSERVATION', 'BDA'):
        if metric != 'VC' and not results['VC'] and not current_image_ids(packet):
            break
        if metric == 'CV' and not results['VC']:
            continue
        if metric == 'BDA' and not results['VC'] and not results['OBSERVATION']['additional_defects']:
            break
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
    targets = merge_stages(results['VC'], results.get('CV', []), results['BDA'], results['OBSERVATION']) if 'BDA' in results else []
    return targets, requests


RECHECK_PROMPT = """Align an earlier failed condition with later checks linked by the recorded repair chain.
This is condition identity, not a judgment of repair success. Different wording or IDs can
refer to the same condition. Match the same object, property and page/state; shared components
or a common inspection plan do not make different pages interchangeable. A later full catalogue
check can encompass a narrower earlier condition. Match attempted rechecks even if they fail.
Use only supplied source and later target IDs. Return all supported matches, or none when the
later checks concern different conditions. Do not invent missing checks, rewrite boundaries,
infer unrecorded interactions or provide reasons, states or scores.
Return only {"matches":[{"episode_id":"source ID","target_id":"source target ID",
"recheck_episode_id":"later ID","recheck_target_id":"later target ID"}]}.
EVIDENCE:
"""


def recheck_candidates(episodes, rounds):
    """Existing round links bound alignment; the model cannot search unrelated history."""
    scored = {e['episode_id']: e for e in episodes}
    events = {e['ordinal']: e for e in rounds['events']}
    candidates = []
    for raw in rounds['episodes']:
        source = scored[raw['episode_id']]
        targets = [t for t in source['targets'] if t['actual_state'] == 'fail']
        later_ids = sorted({eid for link in raw['repair_links'] for eid in link.get('recheck_episode_ids', [])})
        if not targets or not later_ids:
            continue
        def identity(t):
            return {k: t[k] for k in ('target_id', 'target', 'check_id', 'modality', 'coverage')}
        candidates.append({'id': source['episode_id'],
            'targets': [{**identity(t), 'observed_issue': t['actual_issue']} for t in targets],
            'repair_events': [events[eid] for eid in sorted({eid for link in raw['repair_links'] for eid in link['repair_event_ids']})],
            'rechecks': [{'episode_id': eid, 'targets': [identity(t) for t in scored[eid]['targets']
                                                       if t.get('check_attempted', True)]}
                         for eid in later_ids]})
    return candidates


def validate_recheck_matches(value, candidates):
    fields = {'episode_id', 'target_id', 'recheck_episode_id', 'recheck_target_id'}
    if not isinstance(value, dict) or set(value) != {'matches'} or not isinstance(value['matches'], list):
        raise ValueError('Recheck alignment must return only matches')
    allowed = {(c['id'], t['target_id'], r['episode_id'], u['target_id'])
               for c in candidates for t in c['targets'] for r in c['rechecks'] for u in r['targets']}
    seen = set()
    for row in value['matches']:
        if not isinstance(row, dict) or set(row) != fields or any(not isinstance(v, str) for v in row.values()):
            raise ValueError('Invalid recheck alignment fields')
        key = tuple(row[k] for k in ('episode_id', 'target_id', 'recheck_episode_id', 'recheck_target_id'))
        if key not in allowed or key in seen:
            raise ValueError('Unknown or duplicate recheck alignment')
        seen.add(key)
    return value['matches']


def align_rechecks(episodes, rounds, client, output, *, max_input_chars=120000):
    from .judge import _verification_batches
    candidates = recheck_candidates(episodes, rounds)
    matches, requests = [], []
    fields = {k: {'type': 'string'} for k in ('episode_id', 'target_id', 'recheck_episode_id', 'recheck_target_id')}
    for index, batch in enumerate(_verification_batches(candidates, max_input_chars)):
        judge = client('recheck_alignment', rows_schema('matches', fields))
        path = Path(output) / 'inputs' / f'recheck-alignment-{index}.json'
        write_json(path, batch)
        record = judge.judge('recheck_alignment', RECHECK_PROMPT + json.dumps(batch, ensure_ascii=False, separators=(',', ':')), [],
                             context={'input_path': str(path.resolve())})
        if record.get('error'):
            raise ValueError(f'Recheck alignment failed: {record["error"]}')
        matches.extend(validate_recheck_matches(record['parsed'], batch))
        requests.append({'input_path': str(path.resolve()), 'request_sha256': record['request_sha256'],
                         'response_path': str((Path(judge.cache_root) / (record['request_sha256'] + '.json')).resolve())})
    return {'matches': matches, 'requests': requests}


def validate_saved_rechecks(alignment, episodes, rounds):
    from multimodalcode.io import read_json
    candidates = {c['id']: c for c in recheck_candidates(episodes, rounds)}
    seen, matches = set(), []
    for request in alignment['requests']:
        batch = read_json(request['input_path'])
        for item in batch:
            if item != candidates.get(item['id']) or item['id'] in seen:
                raise ValueError('Stored recheck alignment input differs from scored conditions')
            seen.add(item['id'])
        record = read_json(request['response_path'])
        if (record.get('error') or record.get('stage') != 'recheck_alignment'
                or record['request_sha256'] != request['request_sha256']
                or not record['prompt'].endswith(json.dumps(batch, ensure_ascii=False, separators=(',', ':')))):
            raise ValueError('Invalid stored recheck alignment response')
        matches.extend(validate_recheck_matches(record['parsed'], batch))
    if seen != set(candidates) or matches != alignment['matches']:
        raise ValueError('Stored recheck matches are incomplete or changed')
    return matches
