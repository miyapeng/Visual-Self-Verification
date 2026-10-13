"""Six protocol metrics computed from evidence labels, never model scores."""
from __future__ import annotations


PROTOCOL_VERSION = "evidence-diagnosis-repair-20261013"
METRICS = ("VTR", "VC", "EDR", "CAR", "RSR", "CPR")


def check_validity(method_ok, evidence_ok):
    if method_ok is False or evidence_ok is False:
        return False
    return True if method_ok is True and evidence_ok is True else None


def diagnosis_correct(actual_state, agent_state, issue_match):
    """A decidable observation requires a correct, definite diagnosis."""
    if actual_state == "unknown":
        return None
    if actual_state == "pass":
        return agent_state == "pass"
    if agent_state != "fail":
        return False
    return issue_match


def findings(episodes, repairs=()):
    """Traceable instances, not extra headline metrics or preselected conclusions."""
    result = {k: [] for k in ('invalid_method', 'insufficient_observation', 'missing_diagnosis',
                              'false_alarm', 'missed_defect', 'wrong_defect', 'failed_repair', 'regression',
                              'diagnosed_without_repair')}
    linked = {(eid, tid) for r in repairs for eid in r['episode_ids']
              for tid, cids in r.get('target_aliases', {}).items() if cids}
    for episode in episodes:
        for t in episode['targets']:
            ref = {'episode_id': episode['episode_id'], 'target_id': t['target_id']}
            for name, field in (('invalid_method', 'method_ok'), ('insufficient_observation', 'evidence_ok')):
                if t[field] is False:
                    result[name].append(ref)
        for t in episode.get('assessments', []):
            ref = {'episode_id': episode['episode_id'], 'target_id': t['target_id']}
            flags = {'missing_diagnosis': t['agent_state'] in {'absent', 'uncertain'},
                     'false_alarm': t['actual_state'] == 'pass' and t['agent_state'] == 'fail',
                     'missed_defect': t['actual_state'] == 'fail' and t['agent_state'] != 'fail',
                     'wrong_defect': t['actual_state'] == t['agent_state'] == 'fail' and t['issue_match'] is False,
                     'diagnosed_without_repair': t['actual_state'] == t['agent_state'] == 'fail'
                         and t['issue_match'] is True and (episode['episode_id'], t['target_id']) not in linked
                         and not episode.get('repair_event_ids')}
            for name, active in flags.items():
                if active:
                    result[name].append(ref)
    for repair in repairs:
        for row in repair['states']:
            name = ('failed_repair' if row['check_id'] in repair['target_ids'] and row['before'] == 'fail' else
                    'regression' if row['check_id'] not in repair['target_ids'] and row['before'] == 'pass' else None)
            if name and row['after'] == 'fail':
                result[name].append({'repair_event_ids': repair.get('repair_event_ids', []), 'check_id': row['check_id']})
    return result


def rate(values, *, unavailable=0):
    """Point estimate with an explicit opportunity count; no guessed labels."""
    values = list(values)
    unavailable += sum(v is None for v in values)
    success = sum(v is True for v in values)
    failure = sum(v is False for v in values)
    denominator = success + failure
    return {"score": 100 * success / denominator if denominator and not unavailable else None,
            "success_count": success, "failure_count": failure, "denominator": denominator,
            "unassessed_count": unavailable,
            "status": "incomplete" if unavailable else "scored" if denominator else "not_applicable"}


def diagnosis_rate(assessments, state):
    rows = [t for t in assessments if t['actual_state'] == state]
    result = rate(diagnosis_correct(t['actual_state'], t['agent_state'], t['issue_match']) for t in rows)
    result.update(missing_judgment_count=sum(t['agent_state'] == 'absent' for t in rows),
                  inconclusive_judgment_count=sum(t['agent_state'] == 'uncertain' for t in rows))
    return result


def summarize(catalogue, episodes, repairs=(), repair_gaps=(), *, scope=None):
    """Six measures over fixed requirements and independently observed conditions.

    Attempts determine acquisition. Independently decidable catalogue conditions
    determine diagnosis opportunities, including conditions the agent omitted.
    Acceptance labels are shared across repair and preservation.
    """
    goals = [c['check_id'] for c in catalogue['checks']]
    attempts = [t for e in episodes for t in e['targets'] if t.get('check_attempted', True)]
    assessments = [t for e in episodes for t in e.get('assessments', [])]
    if any(t['actual_state'] not in {'pass', 'fail'} for t in assessments):
        raise ValueError('Diagnosis opportunities must be independently decidable')
    pending = sum(e['status'] == 'pending' for e in episodes)
    coverage = {}
    for goal in goals:
        rows = [t for t in attempts if t['check_id'] == goal]
        valid = [t for t in rows if check_validity(t['method_ok'], t['evidence_ok']) is True]
        if any(t['coverage'] == 'full' for t in valid):
            coverage[goal] = 'full'
        elif pending or any((t['coverage'] == 'unknown' and check_validity(t['method_ok'], t['evidence_ok']) is not False) or
                            (t['coverage'] == 'full' and check_validity(t['method_ok'], t['evidence_ok']) is None)
                            for t in rows):
            coverage[goal] = 'unassessed'
        else:
            coverage[goal] = 'partial' if any(t['coverage'] == 'partial' for t in valid) else 'none'
    def covered(ids):
        labels = [coverage[cid] for cid in ids]
        return {**rate(None if v == 'unassessed' else v == 'full' for v in labels),
                'partial_count': labels.count('partial')}
    vc = {**covered(goals), 'goals': coverage, 'requirement_count': len(goals)}
    vc['unclassified_check_ids'] = [c['check_id'] for c in catalogue['checks'] if 'requirement_types' not in c]
    vc['by_requirement_type'] = {}
    for kind in ('visual', 'interactive', 'other'):
        ids = [c['check_id'] for c in catalogue['checks'] if 'requirement_types' in c
               and (not c['requirement_types'] if kind == 'other' else kind in c['requirement_types'])]
        row = covered(ids)
        row['classification_complete'] = not vc['unclassified_check_ids']
        if not row['classification_complete']:
            row.update(score=None, status='unclassified')
        vc['by_requirement_type'][kind] = row

    if scope is None:
        vtr = rate([], unavailable=1)
    elif scope['initial_implementation_event_id'] is None:
        vtr = {**rate([]), 'no_initial_implementation_count': 1}
    else:
        vtr = rate([bool(attempts)]) if not pending else rate([], unavailable=pending)
    if scope is not None and scope['initial_implementation_event_id'] is None:
        vc = {**vc, **rate([]), 'not_applicable_reason': 'no_initial_implementation'}
        for row in vc['by_requirement_type'].values():
            row.update(rate([]))

    repaired, preserved, excluded = [], [], 0
    transitions, joint = [], {k: 0 for k in ('local_success_preserved', 'local_success_regressed',
        'local_failure_preserved', 'local_failure_regressed', 'incomplete', 'not_applicable')}
    seen = set()
    for repair in repairs:
        key = (repair.get('before'), repair.get('after'), tuple(repair.get('repair_event_ids', [])))
        if key in seen:
            raise ValueError('Duplicate repair transition; store it once and share references')
        seen.add(key)
        states = {r['check_id']: r for r in repair['states']}
        # Both views must reference the same acceptance label if a goal occurs in both.
        for row in repair.get('preservation_states', []):
            old = states.get(row['check_id'])
            if old and (old['before'], old['after']) != (row['before'], row['after']):
                raise ValueError('Repair and preservation disagree on the same acceptance state')
            states[row['check_id']] = row
        target_ids = set(repair['target_ids'])
        local, elsewhere, regressed = [], [], []
        for cid in sorted(target_ids | set(goals)):
            row = states.get(cid, {})
            before, after = row.get('before', 'unknown'), row.get('after', 'unknown')
            if before not in {'pass', 'fail', 'unknown'} or after not in {'pass', 'fail', 'unknown'}:
                raise ValueError('Invalid acceptance label')
            if cid in target_ids:
                if before == 'pass':
                    excluded += 1
                else:
                    local.append(None if before == 'unknown' or after == 'unknown' else after == 'pass')
            elif before != 'fail':
                elsewhere.append(None if before == 'unknown' or after == 'unknown' else after == 'pass')
                if before == 'pass' and after == 'fail':
                    regressed.append(cid)
        repaired.extend(local)
        preserved.extend(elsewhere)
        complete = None not in local + elsewhere
        success = all(local) if local and complete else None
        regression = bool(regressed) if elsewhere and complete else None
        group = ('incomplete' if not complete else 'not_applicable' if not local or not elsewhere else
                 f"local_{'success' if success else 'failure'}_{'regressed' if regression else 'preserved'}")
        joint[group] += 1
        transitions.append({**{k: repair[k] for k in ('before', 'after', 'repair_event_ids') if k in repair},
                            'local_repair_success': success, 'elsewhere_regression': regression,
                            'regressed_check_ids': regressed})
    rsr = {**rate(repaired, unavailable=len(repair_gaps)), 'excluded_baseline_pass_count': excluded}
    cpr = {**rate(preserved, unavailable=len(repair_gaps)),
           'joint_outcomes': joint, 'transitions': transitions}
    result = {'VTR': vtr, 'VC': vc,
              'EDR': diagnosis_rate(assessments, 'fail'), 'CAR': diagnosis_rate(assessments, 'pass'),
              'RSR': rsr, 'CPR': cpr}
    for metric, state in (('EDR', 'fail'), ('CAR', 'pass')):
        result[metric]['unassessed_count'] += pending
        if pending:
            result[metric].update(score=None, status='incomplete')
        for suffix, modality in (('V', 'visual'), ('T', 'text')):
            result[f'{metric}-{suffix}'] = diagnosis_rate([t for t in assessments if t['modality'] == modality], state)
            if pending:
                result[f'{metric}-{suffix}'].update(score=None, status='incomplete', unassessed_count=pending)
    return result


def macro_average(tasks):
    """Equal task weights, retaining support and incomplete-evaluation counts."""
    result = {}
    for metric in (*METRICS, 'EDR-V', 'EDR-T', 'CAR-V', 'CAR-T'):
        rows = [task[metric] for task in tasks]
        scores = [r['score'] for r in rows if r['score'] is not None]
        unavailable = sum(r.get('unassessed_count', 0) for r in rows)
        result[metric] = {'score': sum(scores) / len(scores) if scores and not unavailable else None,
                          'task_count': len(scores), 'na_task_count': sum(r['status'] == 'not_applicable' for r in rows),
                          'opportunity_count': sum(r.get('denominator', r.get('opportunity_count', 0)) for r in rows),
                          'unassessed_count': unavailable,
                          'status': 'incomplete' if unavailable else 'scored' if scores else 'not_applicable'}
    return result
