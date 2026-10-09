import copy
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from multimodalcode.io import read_json, write_json
from multimodalcode.vsv_eval.check_stages import (judge_round_prefix, metric_packet,
                                                 stage_schema, validate_stage)
from multimodalcode.vsv_eval.evaluation import (build_packet, validate_saved_episode,
                                               evaluate_rounds, validate_repair_aliases, round_cutoffs)
from multimodalcode.vsv_eval.metrics import summarize
from multimodalcode.vsv_eval.states import state_metric_packet, evaluate_states, load_repair_results, validate_states
from multimodalcode.vsv_eval.catalogue import content_hash
from multimodalcode.vsv_eval.repair_pilot import digest_tree


def fixture(tmp_path):
    events = [{'ordinal':0, 'kind':'action', 'tool':'Bash', 'tool_call_id':'check'},
              {'ordinal':1, 'kind':'observation', 'payload':{'tool_use_id':'check'}, 'text':'missing'},
              {'ordinal':2, 'kind':'model_text', 'text':'It is missing.'}]
    source = tmp_path/'run.json'; write_json(source, {'timeline':events})
    episode = {'episode_id':'e', 'core_event_ids':[0,1,2], 'context_event_ids':[], 'judgment_event_ids':[2],
               'evidence_states':[], 'relation_annotation_complete':True, 'repair_links':[]}
    rounds = {'source_run':str(source), 'events':events, 'episodes':[episode]}
    checks = [{'check_id':'a','source_ref':'task:0','setup':'Inspect it.', 'criterion':'The item exists.',
               'required_evidence':'Recorded check.', 'reference_ids':[]}]
    catalogue = {'checks':checks, 'references':{}, 'criteria_sha256':'criteria',
                 'task_sha256':hashlib.sha256(b'task').hexdigest()}
    packet, images = build_packet(rounds,episode,2,'task',catalogue,[],tmp_path)
    responses = {
        'text_vc': {'targets':[{'check_id':'a','target':None,'coverage':'full','evidence_ids':[1],'modality':'text'}]},
        'text_cv': {'targets':[{'target_id':'a','method_ok':True,'evidence_ok':True,'evidence_ids':[1]}]},
        'text_bda': {'targets':[{'target_id':'a','actual_state':'fail','actual_issue':{'object':'item','symptom':'missing'},
                               'agent_state':'fail','issue_match':True,'evidence_ids':[1],'diagnosis_ids':[2]}]},
    }
    calls = []
    def client(stage, schema):
        cache = tmp_path/'judge_cache'/stage
        def judge(actual_stage, prompt, supplied_images, **kwargs):
            assert actual_stage == stage
            payload = json.loads(prompt.split('EVIDENCE:\n',1)[1])
            calls.append((stage,payload,schema))
            key = str(len(calls))
            parsed = copy.deepcopy(responses.get(stage, responses.get(stage.replace('visual_', 'text_'), responses['text_bda'])))
            if stage.endswith('_observation'):
                parsed['targets'] = [{k: t[k] for k in ('target_id','actual_state','actual_issue','evidence_ids')}
                                     for t in parsed['targets']]
                parsed.setdefault('additional_defects', [])
            elif stage.endswith('_bda'):
                parsed['targets'] = [{k: t[k] for k in ('target_id','agent_state','issue_match','diagnosis_ids')}
                                     for t in parsed['targets']]
            record = {'stage':stage,'request_sha256':key,'prompt':prompt,'parsed':parsed}
            write_json(cache/f'{key}.json',record)
            return record
        return SimpleNamespace(cache_root=cache,judge=judge)
    return rounds,catalogue,packet,images,responses,calls,client


def test_metric_calls_share_identities_but_no_verdicts_and_preserve_inputs(tmp_path):
    rounds,cat,packet,images,responses,calls,client = fixture(tmp_path)
    original = copy.deepcopy(packet)
    targets,requests = judge_round_prefix(packet,images,cat,client,tmp_path)
    assert [c[0] for c in calls] == ['text_vc','text_cv','text_observation','text_bda']
    assert all(e['kind'] in {'action', 'observation'} for e in calls[2][1]['events'])
    assert 'observation_verdicts' in calls[3][1]
    identity = calls[1][1]['targets'][0]
    assert set(identity) == {'target_id','target','check_id','modality'}
    assert identity['target'] == cat['checks'][0]['criterion']
    assert 'coverage' not in identity and 'method_ok' not in identity
    assert len(calls[0][2]['properties']['targets']['items']['properties']) == 5
    assert len(calls[1][2]['properties']['targets']['items']['properties']) == 4
    assert len(calls[2][2]['properties']['targets']['items']['properties']) == 4
    assert len(calls[3][2]['properties']['targets']['items']['properties']) == 4
    assert packet == original
    row = {'episode_id':'e','targets':targets,'status':'evaluated','requests':requests,
           'relation_annotation_complete':True,'repair_event_ids':[]}
    validate_saved_episode(row,tmp_path,rounds,cat)
    result = summarize(cat,[row])
    assert result['VC']['score'] == result['CV']['score'] == 100
    assert result['BDA']['error']['score'] == 100
    row['targets'][0]['coverage'] = 'none'
    with pytest.raises(ValueError,match='differ'):
        validate_saved_episode(row,tmp_path,rounds,cat)


def test_missing_pixel_schema_preserves_text_labels_and_blocks_visual_certainty(tmp_path):
    _, cat, packet, *_ = fixture(tmp_path)
    packet['recorded_image_event_ids'] = [1]
    targets = [{'target_id': 'text-check', 'modality': 'text'},
               {'target_id': 'visual-check', 'modality': 'visual'}]
    for metric in ('VC', 'CV', 'BDA'):
        schema = stage_schema(metric, cat, targets, packet)
        text, visual = [v['properties'] for v in schema['properties']['targets']['items']['anyOf']]
        if metric == 'VC':
            assert 'full' in text['coverage']['enum']
            assert 'full' not in visual['coverage']['enum']
        elif metric == 'CV':
            assert text['evidence_ok']['type'] == ['boolean', 'null']
            assert visual['evidence_ok'] == {'type': 'null'}
        else:
            assert text['actual_state']['enum'] == ['pass', 'fail', 'unknown']
            assert visual['actual_state']['enum'] == ['unknown']
    packet['image_order'] = [{'role': 'agent_observation', 'event_id': 1}]
    properties = stage_schema('VC', cat, targets, packet)['properties']['targets']['items']['properties']
    assert 'full' in properties['coverage']['enum']
    assert properties['modality']['enum'] == ['text', 'visual']


def test_wire_ids_restore_original_integers_without_accepting_unknown_ids(tmp_path):
    _, cat, packet, images, responses, _, client = fixture(tmp_path)
    for result in responses.values():
        for target in result['targets']:
            for key in ('evidence_ids', 'diagnosis_ids'):
                if key in target:
                    target[key] = list(map(str, target[key]))
    targets, _ = judge_round_prefix(packet, images, cat, client, tmp_path)
    assert targets[0]['evidence_ids'] == [1]
    assert targets[0]['diagnosis_ids'] == [2]
    responses['text_vc']['targets'][0]['evidence_ids'] = ['999']
    with pytest.raises(ValueError, match='nonexistent'):
        judge_round_prefix(packet, images, cat, client, tmp_path)


def test_independent_validity_disagreement_does_not_rewrite_coverage(tmp_path):
    _,cat,packet,images,responses,_,client = fixture(tmp_path)
    responses['text_cv']['targets'][0]['method_ok'] = False
    targets,_ = judge_round_prefix(packet,images,cat,client,tmp_path)
    result = summarize(cat,[{'episode_id':'e','targets':targets,'status':'evaluated',
                             'relation_annotation_complete':True,'repair_event_ids':[]}])
    assert targets[0]['coverage']=='full' and targets[0]['method_ok'] is False
    assert result['VC']['score']==100 and result['CV']['score']==0


@pytest.mark.parametrize('stage,fault', [('text_vc','invented_id'),('text_cv','omitted'),
                                       ('text_observation','future'),('text_cv','extra_field')])
def test_bad_metric_outputs_fail_without_fill_in_or_correction(tmp_path,stage,fault):
    _,cat,packet,images,responses,calls,client = fixture(tmp_path)
    if stage == 'text_observation': responses[stage] = copy.deepcopy(responses['text_bda'])
    if fault=='invented_id': responses[stage]['targets'][0]['check_id']='invented'
    elif fault=='omitted': responses[stage]['targets']=[]
    elif fault=='future': responses[stage]['targets'][0]['evidence_ids']=[10]
    else: responses[stage]['targets'][0]['coverage']='full'
    with pytest.raises(ValueError):
        judge_round_prefix(packet,images,cat,client,tmp_path)
    assert len([c for c in calls if c[0]==stage])==1


def test_no_checks_needs_only_the_coverage_call(tmp_path):
    _,cat,packet,images,responses,calls,client=fixture(tmp_path)
    responses['text_vc']['targets']=[]
    targets,requests=judge_round_prefix(packet,images,cat,client,tmp_path)
    assert targets==[] and len(calls)==len(requests)==1


@pytest.mark.parametrize('has_attempt', [True, False])
def test_visible_defect_audit_does_not_depend_on_agent_targets_or_add_checks(tmp_path, has_attempt):
    from PIL import Image
    rounds, cat, _, _, responses, calls, client = fixture(tmp_path)
    image = tmp_path / 'observed.png'
    Image.new('RGB', (16, 16), 'white').save(image)
    rounds['events'][1]['images'] = [{'path': str(image)}]
    rounds['events'][2]['text'] = 'The page looks good.'
    write_json(rounds['source_run'], {'timeline': rounds['events']})
    cat['checks'].append({**cat['checks'][0], 'check_id': 'b', 'criterion': 'Required content is visible.'})
    packet, images = build_packet(rounds, rounds['episodes'][0], 2, 'task', cat, [], tmp_path)
    original = copy.deepcopy(rounds)
    responses['visual_vc'] = {'targets': [{**responses['text_vc']['targets'][0], 'modality': 'visual'}] if has_attempt else []}
    responses['visual_observation'] = {
        'targets': [{'target_id': 'a', 'actual_state': 'pass', 'actual_issue': None, 'evidence_ids': [1]}] if has_attempt else [],
        'additional_defects': [{'target': 'Required content is visible.', 'check_id': 'b',
                               'actual_issue': {'object': 'content', 'symptom': 'missing'}, 'evidence_ids': ['1']}]}
    responses['visual_bda'] = {'targets': [
        {'target_id': cid, 'agent_state': 'pass', 'issue_match': None, 'diagnosis_ids': [2]}
        for cid in (['a', 'b'] if has_attempt else ['b'])]}
    targets, requests = judge_round_prefix(packet, images, cat, client, tmp_path)
    row = {'episode_id': 'e', 'targets': targets, 'status': 'evaluated', 'requests': requests,
           'relation_annotation_complete': True, 'repair_event_ids': []}
    assert validate_saved_episode(row, tmp_path, rounds, cat)['targets'] == targets
    assert len(calls) == (4 if has_attempt else 3)
    observation = next(c[1] for c in calls if c[0] == 'visual_observation')
    assert observation['catalogue'] == cat['checks']
    assert all(e['kind'] in {'action', 'observation'} for e in observation['events'])
    assert 'looks good' not in json.dumps(observation)
    result = summarize(cat, [row])
    assert result['VC']['score'] == (50 if has_attempt else 0)
    assert result['CV']['sample_count'] == int(has_attempt)
    assert result['CV']['unknown_count'] == 0
    assert result['BDA-V']['error']['failure_count'] == 1
    assert targets[-1]['target_id'] == 'b' and targets[-1]['check_attempted'] is False
    assert rounds == original


@pytest.mark.parametrize('fault', ['missing_pixels', 'context_only', 'future', 'duplicate', 'unknown_goal'])
def test_additional_defects_require_unique_current_visual_evidence(tmp_path, fault):
    from multimodalcode.vsv_eval.check_stages import stage_packet, validate_observations
    _, cat, packet, _, responses, _, _ = fixture(tmp_path)
    cat['checks'].append({**cat['checks'][0], 'check_id': 'b'})
    checked = validate_stage('VC', responses['text_vc'], packet, cat)
    supplied = stage_packet('OBSERVATION', packet, checked)
    supplied['image_order'] = [{'role': 'agent_observation', 'event_id': 1}]
    supplied['recorded_image_event_ids'] = [1]
    raw = {'targets': [{k: t[k] for k in ('target_id', 'actual_state', 'actual_issue', 'evidence_ids')}
                        for t in responses['text_bda']['targets']],
           'additional_defects': [{'target': 'The content is readable.', 'check_id': 'b',
                                  'actual_issue': {'object': 'content', 'symptom': 'clipped'}, 'evidence_ids': [1]}]}
    if fault == 'missing_pixels': supplied['image_order'] = []
    elif fault == 'context_only': supplied['core_event_ids'] = [0]
    elif fault == 'future': raw['additional_defects'][0]['evidence_ids'] = [99]
    elif fault == 'duplicate': raw['additional_defects'][0]['check_id'] = 'a'
    else: raw['additional_defects'][0]['check_id'] = 'nonexistent'
    with pytest.raises(ValueError):
        validate_observations(raw, supplied, cat)


def test_metric_schema_restricts_existing_ids_and_visual_claims_need_pixels(tmp_path):
    _,cat,packet,_,responses,_,_=fixture(tmp_path)
    assert stage_schema('VC',cat)['properties']['targets']['items']['properties']['check_id']['enum']==['a',None]
    assert stage_schema('VC',cat)['properties']['targets']['items']['properties']['modality']['enum']==['text','visual']
    packet['recorded_image_event_ids']=[1]
    response=responses['text_vc']; response['targets'][0]['modality']='visual'
    with pytest.raises(ValueError,match='pixels'):
        validate_stage('VC',response,packet,cat)


@pytest.mark.parametrize('received_image', [False, True])
def test_modality_follows_cited_image_receipt_with_text_and_missing_pixels(tmp_path, received_image):
    _,cat,packet,_,responses,_,_=fixture(tmp_path)
    packet['events'][1]['text']='Observed output; screenshot filename: /tmp/observation.png'
    if received_image:
        packet['events'][1]['images']=[{'path':'missing.png'}]
        packet['recorded_image_event_ids']=[1]
    # Task reference pixels are not evidence of an application image received by the agent.
    packet['image_order']=[{'role':'reference', 'reference_id':'prototype'}]
    response=responses['text_vc']
    row=response['targets'][0]
    row.update(coverage='unknown', modality='visual' if received_image else 'text')
    original=copy.deepcopy(packet)
    labels=validate_stage('VC',response,packet,cat)
    assert labels[0]['modality']==row['modality'] and packet==original
    row['modality']='text' if received_image else 'visual'
    with pytest.raises(ValueError,match='modality differs'):
        validate_stage('VC',response,packet,cat)
    row['modality']='mixed'
    with pytest.raises(ValueError,match='Invalid VC modality'):
        validate_stage('VC',response,packet,cat)


def test_image_in_packet_does_not_make_an_unrelated_text_target_visual(tmp_path):
    _,cat,packet,_,responses,_,_=fixture(tmp_path)
    packet['events'].append({'ordinal':3,'kind':'observation','images':[{'path':'missing.png'}]})
    packet['recorded_image_event_ids']=[3]
    assert validate_stage('VC',responses['text_vc'],packet,cat)[0]['modality']=='text'


def test_saved_metric_packet_cannot_include_other_judge_verdicts(tmp_path):
    rounds,cat,packet,images,_,_,client=fixture(tmp_path)
    targets,requests=judge_round_prefix(packet,images,cat,client,tmp_path)
    cv=requests[1]; supplied=read_json(cv['input_path']); supplied['targets'][0]['coverage']='full'
    write_json(cv['input_path'],supplied)
    response=read_json(cv['response_path'])
    response['prompt']='EVIDENCE:\n'+json.dumps(supplied,ensure_ascii=False)
    write_json(cv['response_path'],response)
    row={'episode_id':'e','targets':targets,'requests':requests,'status':'evaluated',
         'relation_annotation_complete':True,'repair_event_ids':[]}
    with pytest.raises(ValueError,match='verdict'):
        validate_saved_episode(row,tmp_path,rounds,cat)


def test_rs_and_cp_receive_distinct_target_sets_without_mutating_execution():
    checks=[{'check_id':c,'reference_ids':[]} for c in ['necessary','extra_repair']]
    packet={'checks':checks,'rule_facts':[],'evidence':[{'evidence_id':'v:actual'}],'image_order':[]}
    spec={'transitions':[{'before':'v','after':'w','target_ids':['extra_repair']}]}
    original=copy.deepcopy(packet)
    rs=state_metric_packet(packet,spec,{'checks':checks[:1]},'v','RS')
    cp=state_metric_packet(packet,spec,{'checks':checks[:1]},'v','CP')
    assert rs['checks']==checks[1:] and cp['checks']==checks[:1]
    assert packet==original
    result=summarize({'checks':[{'check_id':'a'},{'check_id':'b'}]},[],[
        {'episode_ids':[],'target_ids':['a'],'states':[{'check_id':'a','before':'fail','after':'pass'}],
         'preservation_states':[{'check_id':'a','before':'fail','after':'pass'},
                                {'check_id':'b','before':'pass','after':'fail'}]}])
    assert result['RS']['score']==100 and result['CP']['score']==0


def test_metric_inputs_keep_actual_targets_task_and_raw_evidence_without_catalogue(tmp_path):
    _,cat,packet,_,_,_,_=fixture(tmp_path)
    packet['catalogue'].append({**cat['checks'][0], 'check_id':'unrelated'})
    packet['previous_target_conditions']=[{'check_id':'a','target':'identity'}]
    targets=[{'target_id':'a','check_id':'a','target':'The item exists.','modality':'text'},
             {'target_id':'unmatched','check_id':None,'target':'Server responds.','modality':'text'}]
    original=copy.deepcopy(packet)
    supplied=metric_packet(packet,targets)
    assert 'catalogue' not in supplied
    assert supplied['task'] == packet['task']
    assert supplied['events']==packet['events'] and supplied['targets']==targets
    assert 'previous_target_conditions' not in supplied and packet==original


def test_pending_state_scope_removes_exact_goals_but_keeps_workflow_baseline():
    checks=[{'check_id':c,'reference_ids':[c]} for c in ['exact','pending','later']]
    evidence=[{'evidence_id':'baseline','workflow_id':'shared','check_ids':['exact']},
              {'evidence_id':'current','workflow_id':'shared','check_ids':['pending']},
              {'evidence_id':'later','workflow_id':'shared','check_ids':['later']},
              {'evidence_id':'other','workflow_id':'other','check_ids':['exact']}]
    views=[{'role':'evaluator_observation','evidence_id':e['evidence_id'],'path':e['evidence_id']} for e in evidence]
    views += [{'role':'reference','reference_id':c['check_id'],'path':c['check_id']} for c in checks]
    packet={'checks':checks,'evidence':evidence,'image_order':views,
            'rule_facts':[{'check_id':'exact','state':'pass','evidence_ids':['baseline']}]}
    original=copy.deepcopy(packet)
    supplied=state_metric_packet(packet,{'transitions':[]},{'checks':checks},'v','CP',check_ids=['pending'])
    assert [c['check_id'] for c in supplied['checks']]==['pending']
    assert [e['evidence_id'] for e in supplied['evidence']]==['baseline','current']
    assert [v['path'] for v in supplied['image_order']]==['baseline','current','pending']
    assert [v['attachment_index'] for v in supplied['image_order']]==[1,2,3]
    assert supplied['rule_facts']==[] and packet==original
    with pytest.raises(ValueError,match='outside its metric'):
        state_metric_packet(packet,{'transitions':[]},{'checks':checks},'v','CP',check_ids=['invented'])


def test_partial_reuse_annotates_only_missing_rounds(tmp_path,monkeypatch):
    rounds,cat,_,_,_,calls,client=fixture(tmp_path)
    rounds.update(schema='multimodalcode-verification-rounds-1',case_id='test',
                  source_run_sha256=hashlib.sha256(Path(rounds['source_run']).read_bytes()).hexdigest())
    cat.update(schema='self-verification-catalogue-1',source_ids=['task:0'],review={'status':'draft'},
               criteria_sha256=content_hash(cat['checks']))
    write_json(tmp_path/'rounds.json',rounds);write_json(tmp_path/'cat.json',cat)
    write_json(tmp_path/'config.json',{'schema':'multimodalcode-vsv-judge-config-1'})
    (tmp_path/'prompt.txt').write_text('task')
    write_json(tmp_path/'map.json',{'mapping':{'e':[]}})
    monkeypatch.setattr('multimodalcode.vsv_eval.evaluation.build_client',
                        lambda config,profile,cache,response_schema=None:client(cache.name,response_schema))
    evaluate_rounds(tmp_path/'rounds.json',tmp_path/'cat.json',tmp_path,tmp_path/'config.json',tmp_path/'out',
                    primary_profile='test',allow_draft=True,reference_map=tmp_path/'map.json')
    rounds['episodes'].append({**rounds['episodes'][0],'episode_id':'new'})
    write_json(tmp_path/'rounds.json',rounds)
    annotated=[]
    def annotate(subset,catalogue,judge):
        annotated.extend(e['episode_id'] for e in subset['episodes'])
        return {'mapping':{'new':[]}}
    monkeypatch.setattr('multimodalcode.vsv_eval.evaluation.reference_mapping',annotate)
    result=evaluate_rounds(tmp_path/'rounds.json',tmp_path/'cat.json',tmp_path,tmp_path/'config.json',tmp_path/'reuse',
                           primary_profile='test',allow_draft=True,reuse_checks=tmp_path/'out')
    assert annotated==['new'] and len(calls)==8
    assert 'reused_from' in result['episodes'][0]
    assert not (tmp_path/'reuse/inputs/new-2.json').exists()


def test_repair_aliases_cannot_silently_reuse_old_target_ids():
    episodes=[{'episode_id':'e','targets':[{'target_id':'current'}]}]
    repair={'episode_ids':['e'],'target_aliases':{'current':'repair_check'}}
    validate_repair_aliases(episodes,[repair])
    repair['target_aliases']={'old':'repair_check'}
    with pytest.raises(ValueError,match='current check labels'):
        validate_repair_aliases(episodes,[repair])


def test_active_round_entry_uses_separate_calls_and_reuses_validated_responses(tmp_path,monkeypatch):
    rounds,cat,_,_,_,calls,client=fixture(tmp_path)
    rounds.update(schema='multimodalcode-verification-rounds-1',case_id='test',
                  source_run_sha256=hashlib.sha256(Path(rounds['source_run']).read_bytes()).hexdigest())
    cat.update(schema='self-verification-catalogue-1',source_ids=['task:0'],review={'status':'draft'},
               criteria_sha256=content_hash(cat['checks']))
    write_json(tmp_path/'rounds.json',rounds);write_json(tmp_path/'cat.json',cat)
    write_json(tmp_path/'config.json',{'schema':'multimodalcode-vsv-judge-config-1'})
    (tmp_path/'prompt.txt').write_text('task')
    write_json(tmp_path/'map.json',{'mapping':{'e':[]}})
    monkeypatch.setattr('multimodalcode.vsv_eval.evaluation.build_client',
                        lambda config,profile,cache,response_schema=None:client(cache.name,response_schema))
    result=evaluate_rounds(tmp_path/'rounds.json',tmp_path/'cat.json',tmp_path,tmp_path/'config.json',tmp_path/'out',
                           primary_profile='test',allow_draft=True,reference_map=tmp_path/'map.json')
    assert len(calls)==4 and result['judgment_method']=='observation_with_visual_defect_audit'
    assert result['metrics']['VC']['score']==100
    again=evaluate_rounds(tmp_path/'rounds.json',tmp_path/'cat.json',tmp_path,tmp_path/'config.json',tmp_path/'reuse',
                          primary_profile='test',allow_draft=True,reuse_checks=tmp_path/'out')
    assert len(calls)==4 and again['metrics']==result['metrics']


def test_state_entry_calls_rs_and_cp_separately_and_validates_the_merge(tmp_path,monkeypatch):
    events=[{'ordinal':0,'kind':'action','tool':'Bash'}, {'ordinal':1,'kind':'observation'},
            {'ordinal':2,'kind':'action','tool':'Edit'}]
    source=tmp_path/'trajectory/run.json';write_json(source,{'timeline':events})
    ep={'episode_id':'e','core_event_ids':[0,1],'context_event_ids':[],'judgment_event_ids':[],
        'evidence_states':[],'relation_annotation_complete':True,'repair_links':[{'repair_event_ids':[2]}]}
    digest=hashlib.sha256(source.read_bytes()).hexdigest()
    rounds={'schema':'multimodalcode-verification-rounds-1','source_run':str(source),
            'source_run_sha256':digest,'events':events,'episodes':[ep]}
    write_json(tmp_path/'rounds.json',rounds)
    checks=[{'check_id':c,'source_ref':'task:0','setup':'Inspect.','criterion':c,
             'required_evidence':'Observed output.','reference_ids':[]} for c in ['a','b']]
    cat={'schema':'self-verification-catalogue-1','checks':checks,'criteria_sha256':content_hash(checks),
         'source_ids':['task:0'],'references':{},'review':{'status':'draft'}}
    write_json(tmp_path/'cat.json',cat)
    versions=[]
    for name,eid in [('V0',1),('V1',2)]:
        app=tmp_path/name/'app';app.mkdir(parents=True);(app/'app.js').write_text(name)
        versions.append({'version':name,'workspace':str(app.parent),'after_event':eid,'code_manifest':digest_tree(app)})
    write_json(tmp_path/'manifest.json',{'source_run_sha256':digest,'versions':versions})
    spec={'fixture':str(tmp_path),'version_source':{'kind':'manifest','path':str(tmp_path/'manifest.json')},
          'runtime':{'code_subdir':'app','command':['node','app.js']},'routes':[{'route':'/'}],
          'transitions':[{'before':'V0','after':'V1','episode_ids':['e'],'repair_event_ids':[2],'target_ids':['a']}]}
    write_json(tmp_path/'spec.json',spec)
    write_json(tmp_path/'config.json',{'schema':'multimodalcode-vsv-judge-config-1'})
    def replay(version,spec,destination,port):
        value={'routes':{'/':{'status':'executed','text':'observed'}},'checks':[]}
        write_json(destination/'result.json',value);return value
    monkeypatch.setattr('multimodalcode.vsv_eval.states.replay_version',replay)
    calls=[]
    def client(config,profile,cache,response_schema=None):
        def judge(stage,prompt,images, **kwargs):
            packet=json.loads(prompt.split('EVIDENCE:\n',1)[1]);calls.append((stage,packet))
            rows=[{'check_id':c['check_id'],
                   'state':('fail' if c['check_id']=='a' else 'pass') if packet['version']=='V0'
                           else ('pass' if c['check_id']=='a' else 'fail'),
                   'evidence_ids':[packet['version']+':route:/']} for c in packet['checks']]
            key=packet['version']+stage
            record={'stage':stage,'request_sha256':key,'prompt':prompt,'parsed':{'checks':rows}}
            write_json(cache/f'{key}.json',record);return record
        return SimpleNamespace(judge=judge,_key=lambda *args:'request-key')
    monkeypatch.setattr('multimodalcode.vsv_eval.states.build_client',client)
    out=tmp_path/'out'
    evaluate_states(tmp_path/'rounds.json',tmp_path/'cat.json',tmp_path/'spec.json',tmp_path/'config.json',out,
                    primary_profile='test',allow_draft=True)
    assert [stage for stage,_ in calls]==['text_rs','text_cp','text_rs','text_cp']
    assert [c['check_id'] for c in calls[0][1]['checks']]==['a']
    assert [c['check_id'] for c in calls[1][1]['checks']]==['a','b']
    assert 'metric_results' not in calls[1][1] and 'state' not in calls[1][1]['checks'][0]
    repairs=load_repair_results(out/'repair_results.json',tmp_path/'rounds.json',tmp_path/'cat.json')
    result=summarize(cat,[],repairs)
    assert result['RS']['score']==100 and result['CP']['score']==0
    repairs[0]['preservation_states'][0]['after_evidence_ids']=['invented']
    saved=read_json(out/'repair_results.json');saved['repairs']=repairs;write_json(out/'repair_results.json',saved)
    with pytest.raises(ValueError,match='independent version state'):
        load_repair_results(out/'repair_results.json',tmp_path/'rounds.json',tmp_path/'cat.json')


def test_scoped_target_keeps_partial_coverage_without_invalidating_its_method(tmp_path):
    rounds, cat, packet, images, responses, calls, client = fixture(tmp_path)
    condition = 'The Engineering filter returns only Engineering jobs.'
    cat['checks'][0]['criterion'] = 'Jobs can be filtered by Engineering, Sales and Marketing.'
    responses['text_vc']['targets'][0].update(target=condition, coverage='partial')
    targets, _ = judge_round_prefix(packet, images, cat, client, tmp_path)
    assert calls[1][1]['targets'][0]['target'] == condition
    assert 'catalogue' not in calls[1][1]
    assert calls[0][1]['catalogue'][0]['criterion'] != condition
    scores = summarize(cat, [{'episode_id': 'e', 'targets': targets, 'status': 'evaluated',
                              'relation_annotation_complete': True, 'repair_event_ids': []}])
    assert scores['VC']['score'] == 0 and scores['CV']['score'] == 100


def test_absent_and_context_only_goals_are_omitted_but_failed_attempts_remain(tmp_path):
    _, cat, packet, _, responses, _, _ = fixture(tmp_path)
    row = responses['text_vc']['targets'][0]
    row.update(target='The app can be inspected.', coverage='none', evidence_ids=[])
    assert validate_stage('VC', responses['text_vc'], packet, cat) == []
    packet['events'].append({'ordinal': -1, 'kind': 'observation', 'text': 'Previous check.'})
    packet['context_event_ids'] = [-1]
    row['evidence_ids'] = [-1]
    assert validate_stage('VC', responses['text_vc'], packet, cat) == []
    packet['events'][1].update(is_error=True, text='Browser could not start.')
    row['evidence_ids'] = [0, 1]
    assert validate_stage('VC', responses['text_vc'], packet, cat)[0]['coverage'] == 'none'
    row.update(coverage='unknown', evidence_ids=[0])
    assert validate_stage('VC', responses['text_vc'], packet, cat)[0]['coverage'] == 'unknown'


def test_split_plan_keeps_context_pairing_without_importing_future_or_other_results(tmp_path):
    events = [
        {'ordinal': 0, 'kind': 'action', 'tool_call_id': 'setup', 'payload': {'command': 'open app'}},
        {'ordinal': 1, 'kind': 'action', 'tool_call_id': 'other'},
        {'ordinal': 2, 'kind': 'observation', 'payload': {'tool_use_id': 'other'}, 'text': 'unrelated'},
        {'ordinal': 3, 'kind': 'observation', 'payload': {'tool_use_id': 'setup'}, 'text': 'ready'},
        {'ordinal': 4, 'kind': 'model_text', 'text': 'Next test filtering, then navigation.'},
        {'ordinal': 5, 'kind': 'action', 'tool_call_id': 'filter'},
        {'ordinal': 6, 'kind': 'observation', 'payload': {'tool_use_id': 'filter'}, 'text': 'three matching rows'},
        {'ordinal': 7, 'kind': 'model_text', 'text': 'Filtering works.'},
        {'ordinal': 8, 'kind': 'observation', 'payload': {'tool_use_id': 'filter'}, 'text': 'future'},
    ]
    ep = {'episode_id': 'filter', 'core_event_ids': [5, 6, 7], 'context_event_ids': [3],
          'judgment_event_ids': [7], 'evidence_states': []}
    rounds = {'events': events, 'episodes': [ep], 'source_run': str(tmp_path/'run.json')}
    cat = {'checks': [], 'references': {}, 'criteria_sha256': 'c'}
    original = copy.deepcopy(rounds)
    packet, _ = build_packet(rounds, ep, 7, 'task', cat, [], tmp_path)
    assert packet['core_event_ids'] == [5, 6, 7]
    assert packet['context_event_ids'] == [0, 3, 4]
    assert [e['ordinal'] for e in packet['events']] == [0, 3, 4, 5, 6, 7]
    assert packet['eligible_diagnosis_event_ids'] == [7]
    assert rounds == original


def test_metric_merge_keeps_target_evidence_and_original_cv_bda_references(tmp_path):
    rounds, cat, packet, images, responses, _, client = fixture(tmp_path)
    responses['text_vc']['targets'][0]['evidence_ids'] = [0, 1]
    responses['text_cv']['targets'][0]['evidence_ids'] = [0, 1]
    targets, requests = judge_round_prefix(packet, images, cat, client, tmp_path)
    assert targets[0]['evidence_ids'] == [0, 1]
    assert read_json(requests[1]['response_path'])['parsed']['targets'][0]['evidence_ids'] == [0, 1]
    assert read_json(requests[2]['response_path'])['parsed']['targets'][0]['evidence_ids'] == [1]
    validate_saved_episode({'episode_id': 'e', 'targets': targets, 'requests': requests,
                            'status': 'evaluated', 'relation_annotation_complete': True,
                            'repair_event_ids': []}, tmp_path, rounds, cat)


def test_catalogue_omission_affects_coverage_not_conditional_metric_denominators(tmp_path):
    _, cat, packet, images, responses, calls, client = fixture(tmp_path)
    cat['checks'].append({**cat['checks'][0], 'check_id': 'unattempted'})
    responses['text_vc']['targets'].append({'check_id': 'unattempted', 'target': 'Another condition.',
                                          'coverage': 'none', 'evidence_ids': [], 'modality': 'text'})
    targets, _ = judge_round_prefix(packet, images, cat, client, tmp_path)
    assert [t['target_id'] for t in calls[1][1]['targets']] == ['a']
    row = {'episode_id': 'e', 'targets': targets, 'status': 'evaluated',
           'relation_annotation_complete': True, 'repair_event_ids': []}
    scores = summarize(cat, [row])
    assert scores['VC']['denominator'] == 2 and scores['VC']['score'] == 50
    assert scores['CV']['denominator'] == 1 and scores['CV']['score'] == 100


def test_direct_response_is_shared_without_importing_the_next_operation(tmp_path):
    rounds, cat, _, _, _, _, _ = fixture(tmp_path)
    ep = rounds['episodes'][0]
    ep.update(core_event_ids=[0, 1], judgment_event_ids=[])
    rounds['events'][2]['text'] = 'The check passed. Next I will inspect the screenshot.'
    rounds['events'] += [{'ordinal': 3, 'kind': 'action', 'tool': 'Edit'},
                         {'ordinal': 4, 'kind': 'model_text', 'text': 'A later conclusion.'}]
    original = copy.deepcopy(rounds)
    assert round_cutoffs(rounds, ep) == [2]
    packet, _ = build_packet(rounds, ep, 2, 'task', cat, [], tmp_path)
    assert packet['core_event_ids'] == [0, 1]
    assert packet['context_event_ids'] == packet['eligible_diagnosis_event_ids'] == [2]
    assert [e['ordinal'] for e in packet['events']] == [0, 1, 2]
    assert rounds == original
    packet, _ = build_packet(rounds, ep, 1, 'task', cat, [], tmp_path)
    assert packet['eligible_diagnosis_event_ids'] == []
    rounds['events'][2]['attempt'] = 2
    assert round_cutoffs(rounds, ep) == [1]


def test_ambiguous_expected_value_does_not_erase_observed_coverage(tmp_path):
    _, cat, packet, _, responses, _, _ = fixture(tmp_path)
    cat['unresolved_check_ids'] = packet['unresolved_check_ids'] = ['a']
    vc = validate_stage('VC', responses['text_vc'], packet, cat)
    supplied = metric_packet(packet, vc)
    assert validate_stage('CV', responses['text_cv'], supplied, cat)[0]['evidence_ok'] is True
    bda = responses['text_bda']['targets'][0]
    bda.update(actual_state='unknown', actual_issue=None, issue_match=None)
    assert validate_stage('BDA', responses['text_bda'], supplied, cat)[0]['actual_state'] == 'unknown'
    merged = {**vc[0], **responses['text_cv']['targets'][0], **bda}
    result = summarize(cat, [{'episode_id':'e', 'targets':[merged], 'status':'evaluated',
                              'relation_annotation_complete':True, 'repair_event_ids':[]}])
    assert result['VC']['score'] == 100
    assert result['BDA']['unknown_count'] == 1
    # A catalogue source conflict must not override an observed, scoped verdict.
    bda.update(actual_state='pass', actual_issue=None)
    assert validate_stage('BDA', responses['text_bda'], supplied, cat)[0]['actual_state'] == 'pass'
    bda.update(actual_state='fail', actual_issue={'object':'item', 'symptom':'missing'})
    assert validate_stage('BDA', responses['text_bda'], supplied, cat)[0]['actual_state'] == 'fail'
    bda['evidence_ids'] = []
    with pytest.raises(ValueError, match='observed feedback'):
        validate_stage('BDA', responses['text_bda'], supplied, cat)


def test_bda_tolerance_retains_pixels_and_exact_observation_guards(tmp_path):
    _, cat, packet, _, responses, _, _ = fixture(tmp_path)
    cat['unresolved_check_ids'] = packet['unresolved_check_ids'] = ['a']
    targets = validate_stage('VC', responses['text_vc'], packet, cat)
    supplied = metric_packet(packet, targets)
    bda = responses['text_bda']['targets'][0]
    supplied['rule_facts'] = [{'check_id': 'a', 'state': 'fail'}]
    bda.update(actual_state='pass', actual_issue=None)
    with pytest.raises(ValueError, match='exact assertion'):
        validate_stage('BDA', responses['text_bda'], supplied, cat)
    supplied['rule_facts'] = []
    supplied['targets'][0]['modality'] = 'visual'
    with pytest.raises(ValueError, match='attached agent pixels'):
        validate_stage('BDA', responses['text_bda'], supplied, cat)
    # Archive loss still cannot be mislabeled as an observed application failure.
    bda.update(actual_state='unknown', actual_issue=None)
    assert validate_stage('BDA', responses['text_bda'], supplied, cat)[0]['actual_state'] == 'unknown'


def test_cp_ambiguous_presentation_reuses_recorded_captures_without_assuming_pass():
    checks = [{'check_id': cid, 'reference_ids': [ref]} for cid, ref in
              [('ambiguous', 'page'), ('visible', 'page'), ('unrelated', 'other')]]
    cat = {'checks': checks, 'unresolved_check_ids': ['ambiguous']}
    evidence = [{'evidence_id': cid, 'check_ids': [cid], 'run_status': 'completed',
                 'workflow_id': cid, 'observation': {'screenshot': cid+'.png'}}
                for cid in ['visible', 'unrelated']]
    packet = {'checks': checks, 'evidence': evidence, 'image_order': [
        {'role': 'evaluator_observation', 'evidence_id': e['evidence_id'], 'path': e['evidence_id']+'.png'}
        for e in evidence], 'rule_facts': [{'check_id': 'ambiguous', 'state': 'unknown', 'evidence_ids': []}]}
    original = copy.deepcopy(packet)
    supplied = state_metric_packet(packet, {'transitions': []}, cat, 'V0', 'CP', check_ids=['ambiguous'])
    assert supplied['evidence_assignments'] == {'ambiguous': ['visible']}
    assert supplied['rule_facts'] == [] and packet == original
    assert [e['evidence_id'] for e in supplied['evidence']] == ['visible']
    row = {'check_id': 'ambiguous', 'state': 'pass', 'evidence_ids': ['visible']}
    assert validate_states({'checks': [row]}, supplied) == [row]
    row['evidence_ids'] = []
    with pytest.raises(ValueError, match='actual execution evidence'):
        validate_states({'checks': [row]}, supplied)
    row.update(state='unknown')
    assert validate_states({'checks': [row]}, supplied) == [row]
    spec = {'transitions': [{'before': 'V0', 'after': 'V1', 'target_ids': ['ambiguous']}]}
    rs = state_metric_packet(packet, spec, cat, 'V0', 'RS')
    assert rs['rule_facts'] == packet['rule_facts'] and 'acceptance_policy' not in rs


def test_observation_precedes_diagnosis_and_ignores_an_agent_false_alarm(tmp_path):
    _, cat, packet, images, responses, calls, client = fixture(tmp_path)
    responses['text_observation'] = {'targets': [{'target_id': 'a', 'actual_state': 'pass',
                                                  'actual_issue': None, 'evidence_ids': [1]}]}
    responses['text_bda']['targets'][0].update(agent_state='fail', issue_match=None)
    targets, _ = judge_round_prefix(packet, images, cat, client, tmp_path)
    assert targets[0]['actual_state'] == 'pass' and targets[0]['agent_state'] == 'fail'
    assert all(e['kind'] != 'model_text' for e in calls[2][1]['events'])
    assert calls[3][1]['observation_verdicts'][0]['actual_state'] == 'pass'
    supplied = calls[3][1]
    invented = {'targets': [{'target_id': 'a', 'agent_state': 'pass', 'issue_match': None,
                             'diagnosis_ids': [2], 'actual_state': 'fail'}]}
    with pytest.raises(ValueError, match='fields'):
        validate_stage('BDA', invented, supplied, cat)


def test_saved_observation_input_cannot_reintroduce_agent_narration(tmp_path):
    rounds, cat, packet, images, _, _, client = fixture(tmp_path)
    targets, requests = judge_round_prefix(packet, images, cat, client, tmp_path)
    request = requests[2]
    supplied = read_json(request['input_path'])
    supplied['events'] = packet['events']
    write_json(request['input_path'], supplied)
    response = read_json(request['response_path'])
    response['prompt'] = 'EVIDENCE:\n' + json.dumps(supplied, ensure_ascii=False)
    write_json(request['response_path'], response)
    row = {'episode_id': 'e', 'targets': targets, 'status': 'evaluated', 'requests': requests,
           'relation_annotation_complete': True, 'repair_event_ids': []}
    with pytest.raises(ValueError, match='verdict'):
        validate_saved_episode(row, tmp_path, rounds, cat)


def test_recheck_alignment_handles_changed_ids_and_preserves_provenance(tmp_path):
    from multimodalcode.vsv_eval.check_stages import align_rechecks, validate_saved_rechecks, recheck_candidates
    from multimodalcode.vsv_eval.metrics import recheck_status
    from test_vsv_protocol import episode, target
    source = episode(target('old', actual='fail', target='Wordmark is recognizable.'), repair_event_ids=[4])
    later = episode(target('new', target='The brand logo renders correctly.', evidence_ids=[7]), eid='later')
    later['targets'].append(target('unattempted', actual='fail', target='The footer remains visible.', check_attempted=False))
    rounds = {'events':[{'ordinal':4, 'kind':'action', 'tool':'Edit'}], 'episodes':[
        {'episode_id':'e', 'core_event_ids':[1,2,3], 'relation_annotation_complete':True,
         'repair_links':[{'repair_event_ids':[4], 'recheck_episode_ids':['later']}]},
        {'episode_id':'later', 'core_event_ids':[6,7,8], 'repair_links':[]}]}
    matches = [{'episode_id':'e', 'target_id':'old', 'recheck_episode_id':'later', 'recheck_target_id':'new'}]
    assert [t['target_id'] for t in recheck_candidates([source,later],rounds)[0]['rechecks'][0]['targets']] == ['new']
    calls = []
    def client(stage, schema):
        def judge(actual_stage, prompt, images, **kwargs):
            calls.append(actual_stage)
            record = {'stage':stage, 'request_sha256':'key', 'prompt':prompt, 'parsed':{'matches':matches}}
            write_json(tmp_path/'cache/key.json',record)
            return record
        return SimpleNamespace(cache_root=tmp_path/'cache', judge=judge)
    result = align_rechecks([source,later], rounds, client, tmp_path)
    assert calls == ['recheck_alignment']
    assert validate_saved_rechecks(result,[source,later],rounds) == matches
    repair = {'episode_ids':['e'], 'repair_event_ids':[4], 'target_ids':['old']}
    assert recheck_status(source, source['targets'][0], [source,later], rounds, [repair], matches) is True
    bad = copy.deepcopy(result)
    bad['matches'][0]['recheck_target_id'] = 'invented'
    with pytest.raises(ValueError, match='changed'):
        validate_saved_rechecks(bad,[source,later],rounds)


def test_recheck_alignment_rejects_unrelated_or_duplicate_targets():
    from multimodalcode.vsv_eval.check_stages import validate_recheck_matches
    batch = [{'id':'source','targets':[{'target_id':'a'}],
              'rechecks':[{'episode_id':'later','targets':[{'target_id':'b'}]}]}]
    match = {'episode_id':'source','target_id':'a','recheck_episode_id':'later','recheck_target_id':'b'}
    assert validate_recheck_matches({'matches':[match]},batch) == [match]
    for rows in ([match,match], [{**match,'recheck_episode_id':'unrelated'}]):
        with pytest.raises(ValueError, match='Unknown or duplicate'):
            validate_recheck_matches({'matches':rows},batch)
