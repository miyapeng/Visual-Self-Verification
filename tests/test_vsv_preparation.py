"""Acceptance preparation, checkpoint provenance and offline end-to-end integration."""
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from multimodalcode.io import read_json, write_json
from multimodalcode.vsv_eval.catalogue import bind_plan_targets, content_hash
from multimodalcode.vsv_eval.preparation import (checkpoint_requests, build_transitions,
                                                load_acceptance_binding, prepare_case_acceptance)
from multimodalcode.vsv_eval.repair_pilot import digest_tree
from multimodalcode.vsv_eval import system


def fixture(tmp):
    task = tmp/'task'; task.mkdir()
    (task/'prompt.txt').write_text('The item must be visible and the service must remain available.')
    events = [
        {'ordinal':0,'kind':'action','tool':'Bash','tool_call_id':'before','payload':{'command':'check'}},
        {'ordinal':1,'kind':'observation','payload':{'tool_use_id':'before'},'text':'Item missing. Service available.'},
        {'ordinal':2,'kind':'model_text','text':'The item is missing; the service works.'},
        {'ordinal':3,'kind':'action','tool':'Edit','tool_call_id':'edit',
         'payload':{'file_path':'/workspace/app/item.txt','old_string':'missing','new_string':'visible'}},
        {'ordinal':4,'kind':'observation','payload':{'tool_use_id':'edit'},'text':'Updated.'},
        {'ordinal':5,'kind':'action','tool':'Bash','tool_call_id':'after','payload':{'command':'check'}},
        {'ordinal':6,'kind':'observation','payload':{'tool_use_id':'after'},'text':'Item visible. Service available.'},
        {'ordinal':7,'kind':'model_text','text':'The item is visible and the service works.'}]
    source = tmp/'fixture/trajectory/run.json'; write_json(source, {'timeline':events})
    episodes = []
    for eid, core in [('before',[0,1,2]), ('after',[5,6,7])]:
        episodes.append({'episode_id':eid,'core_event_ids':core,'context_event_ids':[],
                         'judgment_event_ids':[core[-1]],'evidence_states':[],
                         'relation_annotation_complete':True,'repair_links':[]})
    episodes[0]['repair_links'] = [{'repair_event_ids':[3], 'recheck_episode_ids':['after']}]
    rounds = {'schema':'multimodalcode-verification-rounds-1','case_id':'integration',
              'source_run':str(source),'source_run_sha256':hashlib.sha256(source.read_bytes()).hexdigest(),
              'events':events,'episodes':episodes}
    checks = [{'check_id':cid,'source_ref':'task:0','setup':'Inspect the running service.',
               'criterion':condition,'required_evidence':'Recorded output.','reference_ids':[]}
              for cid,condition in [('a','The item is visible.'),('b','The service is available.')]]
    cat = {'schema':'self-verification-catalogue-1','checks':checks,'source_ids':['task:0'],
           'references':{},'criteria_sha256':content_hash(checks),
           'task_sha256':hashlib.sha256((task/'prompt.txt').read_bytes()).hexdigest(),'review':{'status':'draft'}}
    versions = []
    for i, boundary in enumerate([2,3]):
        app = tmp/f'export/V{i}/app'; app.mkdir(parents=True)
        (app/'item.txt').write_text('visible' if i else 'missing')
        versions.append({'version':f'V{i}','after_event':boundary,'workspace':str(app.parent),
                         'code_manifest':digest_tree(app)})
    write_json(tmp/'versions.json',{'source_run_sha256':rounds['source_run_sha256'],'versions':versions})
    write_json(tmp/'rounds.json',rounds); write_json(tmp/'catalogue.json',cat)
    write_json(tmp/'binding.json',{'fixture':str(tmp/'fixture'),'version_manifest':str(tmp/'versions.json'),
                                  'runtime':{'command':['unused'],'code_subdir':'app'}})
    write_json(tmp/'references.json',{'mapping':{'before':[],'after':[]}})
    write_json(tmp/'config.json',{'schema':'multimodalcode-vsv-judge-config-1'})
    manifest = {'config':'config.json','primary_profile':'test','allow_draft':True,'cases':[
        {'id':'case','model':'test','task_root':'task','catalogue':'catalogue.json',
         'rounds_json':'rounds.json','reference_map':'references.json','acceptance_binding':'binding.json'}]}
    write_json(tmp/'experiment.json',manifest)
    return rounds, cat


def test_transitions_require_exact_boundaries_and_verified_bytes(tmp_path):
    rounds, _ = fixture(tmp_path)
    assert checkpoint_requests(rounds) == [{'version':'V0','after_event':2},{'version':'V1','after_event':3}]
    binding = load_acceptance_binding(tmp_path/'binding.json', rounds)
    manifest = read_json(binding['version_manifest'])
    transitions = build_transitions(rounds,manifest)
    assert transitions[0]['before'] == 'V0' and transitions[0]['after'] == 'V1'
    manifest['versions'][0]['after_event'] = 1
    with pytest.raises(ValueError,match='exact'):
        build_transitions(rounds,manifest)
    (tmp_path/'export/V1/app/item.txt').write_text('tampered')
    with pytest.raises(ValueError,match='bytes'):
        load_acceptance_binding(tmp_path/'binding.json', rounds)


def test_aliases_use_current_targets_not_old_ids_or_another_group():
    groups = [{'id':'repair-3','repair_event_ids':[3],'targets':[{'target_id':'current'}]}]
    transitions = [{'repair_event_ids':[3]}]
    value = {'checks':[{'check_id':'a'}],'repair_checks':[], 'repair_bindings':[
        {'id':'repair-3','target_ids':['a'],'target_aliases':[{'target_id':'current','check_ids':['a']}]}]}
    assert bind_plan_targets(value,groups,transitions)[0]['target_aliases'] == {'current':['a']}
    for alias in [{'target_id':'old','check_ids':['a']}, {'target_id':'current','check_ids':['invented']}]:
        altered = copy.deepcopy(value); altered['repair_bindings'][0]['target_aliases'] = [alias]
        with pytest.raises(ValueError,match='alias'):
            bind_plan_targets(altered,groups,transitions)
    value['repair_bindings'] = []
    with pytest.raises(ValueError,match='omitted'):
        bind_plan_targets(value,groups,transitions)


def test_shell_mutation_between_linked_edits_cannot_be_hidden(tmp_path):
    rounds, _ = fixture(tmp_path)
    rounds['events'].insert(3,{'ordinal':25,'kind':'action','tool':'Bash'})
    rounds['episodes'][0]['repair_links'][0]['repair_event_ids'] = [3,30]
    rounds['events'].append({'ordinal':30,'kind':'action','tool':'Edit'})
    manifest=read_json(tmp_path/'versions.json')
    manifest['versions'][1].update(after_event=30,edits=[{'ordinal':25,'kind':'shell_mutation'}])
    transition = build_transitions(rounds,manifest)[0]
    assert transition['repair_event_ids'] == [3,30]
    assert transition['context_edit_event_ids'] == [25]
    from multimodalcode.vsv_eval.states import validate_transition
    transition.pop('context_edit_event_ids')
    with pytest.raises(ValueError,match='unrelated'):
        validate_transition(transition,{v['version']:v for v in manifest['versions']},
                            {e['ordinal']:e for e in rounds['events']},
                            {e['episode_id']:e for e in rounds['episodes']},set())


def test_worker_checkpoint_exports_bytes_without_dependency_trees(tmp_path,monkeypatch):
    script=Path(__file__).parents[1]/'scripts/vision2web/archive_replay_worker.py'
    spec=importlib.util.spec_from_file_location('checkpoint_worker',script)
    worker=importlib.util.module_from_spec(spec); spec.loader.exec_module(worker)
    workspace=tmp_path/'workspace'; (workspace/'app/node_modules').mkdir(parents=True)
    (workspace/'app/item.txt').write_text('original')
    (workspace/'app/node_modules/large.bin').write_text('dependency')
    output=tmp_path/'out'; output.mkdir()
    monkeypatch.setattr(worker,'WORKSPACE',workspace);monkeypatch.setattr(worker,'OUT',output)
    row=worker.save_checkpoint({'version':'V0','after_event':2},set())
    root=output/row['workspace']/'app'
    assert (root/'app/item.txt').read_text() == 'original'
    assert not (root/'app/node_modules').exists()
    assert digest_tree(root) == row['code_manifest']
    with pytest.raises(FileExistsError):
        worker.save_checkpoint({'version':'V0','after_event':2},set())


def test_full_runner_prepares_targets_executes_acceptance_and_emits_six_scores(tmp_path,monkeypatch):
    rounds,cat=fixture(tmp_path)
    calls=[]
    def client(config,profile,cache,response_schema=None):
        cache=Path(cache)
        def judge(stage,prompt,images,**kwargs):
            calls.append(stage)
            if stage=='plan_review':
                packet=json.loads(prompt.split('MATERIALS:\n',1)[1])
                assert packet['checks']==cat['checks']
                value={'checks':cat['checks'],'repair_checks':[],'unresolved_check_ids':[],
                       'repair_bindings':[{'id':'repair-3','target_ids':['a'],
                                           'target_aliases':[{'target_id':'a','check_ids':['a']},{'target_id':'b','check_ids':[]}]}],
                       'acceptance_workflows':[{'workflow_id':'accept','setup':{'route':'/','viewport':{'width':800,'height':600}},
                         'nodes':[{'source_ref':'task:0','actions':[], 'checks':[
                             {'check_id':cid,'assertions':[{'type':'text_visible','value':cid}]} for cid in ['a','b']]}]}]}
            elif stage=='recheck_alignment':
                value={'matches':[{'episode_id':'before','target_id':'a','recheck_episode_id':'after','recheck_target_id':'a'}]}
            else:
                packet=json.loads(prompt.split('EVIDENCE:\n',1)[1]); before=packet['episode_id']=='before'
                obs,diag=(1,2) if before else (6,7)
                if stage.endswith('_vc'):
                    value={'targets':[{'target':c['criterion'],'check_id':c['check_id'],'coverage':'full',
                                       'evidence_ids':[obs],'modality':'text'} for c in cat['checks']]}
                elif stage.endswith('_cv'):
                    value={'targets':[{'target_id':c,'method_ok':True,'evidence_ok':True,'evidence_ids':[obs]} for c in ['a','b']]}
                elif stage.endswith('_observation'):
                    value={'targets':[{'target_id':c,'actual_state':'fail' if before and c=='a' else 'pass',
                                       'actual_issue':{'object':'item','symptom':'missing'} if before and c=='a' else None,
                                       'evidence_ids':[obs]} for c in ['a','b']], 'additional_defects':[]}
                elif stage.endswith('_bda'):
                    value={'targets':[{'target_id':c,'agent_state':'fail' if before and c=='a' else 'pass',
                                       'issue_match':True if before and c=='a' else None,'diagnosis_ids':[diag]} for c in ['a','b']]}
                else:
                    pytest.fail(f'Unexpected model stage: {stage}')
            key=content_hash([stage,prompt]);record={'stage':stage,'model':'test','prompt':prompt,
                                                   'request_sha256':key,'parsed':value}
            write_json(cache/f'{key}.json',record);return record
        return SimpleNamespace(cache_root=cache,judge=judge,profile=SimpleNamespace(
            model='test',base_url='offline',generation_settings=lambda:{}))
    for module in ['evaluation','preparation','states']:
        monkeypatch.setattr(f'multimodalcode.vsv_eval.{module}.build_client',client)
    def replay(version,spec,output,port,**kwargs):
        workflows=[]
        for w in spec['acceptance_workflows']:
            nodes=[]
            for i,n in enumerate(w['nodes']):
                nodes.append({'evidence_id':f"{version['version']}:workflow:{w['workflow_id']}:node:{i}",
                    'run_status':'completed','check_ids':list(n['checks']),'actions':[],
                    'checks':[{'check_id':cid,'assertions':[{'rule':r,'passed':version['version']=='V1' or cid=='b'} for r in rules]}
                              for cid,rules in n['checks'].items()]})
            workflows.append({'workflow_id':w['workflow_id'],'setup':w['setup'],'nodes':nodes})
        value={'routes':{},'checks':[],'workflows':workflows}
        write_json(output/'result.json',value);return value
    monkeypatch.setattr('multimodalcode.vsv_eval.states.replay_version',replay)
    assert system.run_experiment(tmp_path/'experiment.json',tmp_path/'out') == 0
    result=read_json(tmp_path/'out/summary.json')['cases'][0]
    assert result['status']=='completed'
    assert all(result['metrics'][m]['score']==100 for m in system.METRICS)
    assert calls.count('plan_review')==1 and calls.count('recheck_alignment')==1
    spec=read_json(tmp_path/'out/case/acceptance/plan/state_spec.json')
    assert spec['transitions'][0]['target_aliases']=={'a':['a'],'b':[]}
    count=len(calls)
    # Re-enter preparation from current labels without another model call.
    prepare_case_acceptance(tmp_path/'rounds.json',tmp_path/'catalogue.json',tmp_path/'out/case/checks/scores.json',
                            tmp_path/'binding.json',tmp_path/'task',tmp_path/'config.json',tmp_path/'out/case/acceptance',
                            primary_profile='test')
    assert len(calls)==count


def test_checkpoint_planning_runs_without_images_and_keeps_review_blocks(tmp_path):
    from multimodalcode.vsv_eval.archive_replay import plan_replay
    run={'timeline':[{'ordinal':1,'kind':'action','tool':'Write','tool_call_id':'w',
                      'payload':{'file_path':'/workspace/app/a.txt','content':'a'}},
                     {'ordinal':2,'kind':'observation','payload':{'tool_use_id':'w'}},
                     {'ordinal':3,'kind':'action','tool':'Bash','tool_call_id':'b',
                      'payload':{'command':'rm -rf /workspace/app'}},
                     {'ordinal':4,'kind':'observation','payload':{'tool_use_id':'b'}}]}
    result=plan_replay(run,tmp_path,checkpoint_events=[{'version':'V0','after_event':3}])
    assert result['cutoff']==3 and result['checkpoint_events'][0]['after_event']==3
    assert result['status']=='blocked' and result['blocks'][0]['event_id']==3


def test_explicit_unaddressed_target_is_not_an_unassessed_repair():
    from test_vsv_protocol import catalogue,episode,target
    from multimodalcode.vsv_eval.metrics import summarize
    repair={'episode_ids':['e'],'repair_event_ids':[4],'target_ids':['a'],
            'target_aliases':{'a':['a'],'b':[]},
            'states':[{'check_id':'a','before':'fail','after':'pass'}],
            'preservation_states':[{'check_id':'a','before':'fail','after':'pass'},
                                   {'check_id':'b','before':'fail','after':'fail'}]}
    result=summarize(catalogue('a','b'),[episode(target('a',actual='fail'),target('b',actual='fail'),
                                             repair_event_ids=[4])],[repair])
    assert result['RS']['score']==100 and result['RS']['denominator']==1
    assert result['VCS']['failure_count']==1 and result['VCS']['unknown_count']==0


def test_published_replay_checkpoints_feed_the_existing_manifest_loader(tmp_path,monkeypatch):
    from multimodalcode.vsv_eval.preparation import publish_checkpoints
    rounds,_=fixture(tmp_path)
    script=Path(__file__).parents[1]/'scripts/vision2web/archive_replay_worker.py'
    spec=importlib.util.spec_from_file_location('export_worker',script)
    worker=importlib.util.module_from_spec(spec); spec.loader.exec_module(worker)
    workspace=tmp_path/'replayed'; (workspace/'app/resources').mkdir(parents=True)
    (workspace/'app/item.txt').write_text('missing')
    (workspace/'app/resources/generated.svg').write_text('<svg/>')
    attempt=tmp_path/'attempt'; output=attempt/'output'; output.mkdir(parents=True)
    monkeypatch.setattr(worker,'WORKSPACE',workspace);monkeypatch.setattr(worker,'OUT',output)
    before=worker.save_checkpoint({'version':'V0','after_event':2},set())
    (workspace/'app/item.txt').write_text('visible')
    after=worker.save_checkpoint({'version':'V1','after_event':3},set())
    binding=read_json(tmp_path/'binding.json'); binding['version_manifest']=str(tmp_path/'published.json')
    write_json(tmp_path/'export_binding.json',binding)
    (tmp_path/'task/resources').mkdir()
    result={'checkpoints':[before,after]}
    manifest=publish_checkpoints(attempt,result,rounds,tmp_path/'task',tmp_path/'export_binding.json')
    verified=load_acceptance_binding(tmp_path/'export_binding.json',rounds)
    assert Path(verified['version_manifest'])==manifest
    exported=read_json(manifest)['versions']
    assert (Path(exported[0]['workspace'])/'app/app/item.txt').read_text()=='missing'
    assert (Path(exported[1]['workspace'])/'app/app/resources/generated.svg').is_file()
    assert len(build_transitions(rounds,read_json(manifest)))==1
    with pytest.raises(ValueError,match='all requested'):
        publish_checkpoints(attempt,{'checkpoints':[before]},rounds,tmp_path/'task',tmp_path/'export_binding.json')
