"""Command-based acceptance for rendered objects, games and repository tests.

The binding supplies an environment launcher and fixed probes. The Judge may
assign criterion IDs to probes, but cannot generate or modify executable code.
"""
from __future__ import annotations

import hashlib
import os
import re
import shutil
import signal
import subprocess
from pathlib import Path

from multimodalcode.io import read_json, write_json


def validate_runtime(runtime):
    if runtime.get('kind') != 'command':
        raise ValueError('Expected command acceptance runtime')
    if not isinstance(runtime.get('command'), list) or not runtime['command'] or any(not isinstance(x,str) for x in runtime['command']):
        raise ValueError('Runtime command requires an argument list')
    if type(runtime.get('timeout', 120)) is not int or runtime.get('timeout',120) <= 0:
        raise ValueError('Command timeout must be positive')
    probes = runtime.get('probes')
    if not isinstance(probes,list) or not probes:
        raise ValueError('Command acceptance needs fixed probes')
    ids = set()
    for p in probes:
        if set(p) - {'probe_id','description','args','images','reports'} or not {'probe_id','description','args','images'} <= set(p):
            raise ValueError('Probe requires probe_id, description, args and images')
        if not re.fullmatch(r'[A-Za-z0-9_-]+',p['probe_id']) or p['probe_id'] in ids:
            raise ValueError('Probe IDs must be unique safe names')
        ids.add(p['probe_id'])
        if not isinstance(p['description'],str) or not p['description'].strip():
            raise ValueError('Probe description is required')
        for key in ('args','images','reports'):
            if not isinstance(p.get(key,[]),list) or any(not isinstance(s,str) for s in p.get(key,[])):
                raise ValueError('Probe arguments and images must be string lists')
        for name in p['images'] + p.get('reports',[]):
            if Path(name).is_absolute() or '..' in Path(name).parts:
                raise ValueError('Probe images must stay inside its output directory')
    return {p['probe_id']:p for p in probes}


def assignment_schema():
    return {'type':'array','items':{'type':'object','properties':{
        'probe_id':{'type':'string'}, 'check_ids':{'type':'array','items':{'type':'string'},'minItems':1}},
        'required':['probe_id','check_ids'],'additionalProperties':False}}


def validate_assignments(assignments, runtime, catalogue):
    probes = validate_runtime(runtime)
    goals = {c['check_id'] for c in catalogue['checks']}
    seen, used = set(), set()
    for row in assignments:
        if set(row) != {'probe_id','check_ids'} or row['probe_id'] not in probes or row['probe_id'] in used:
            raise ValueError('Unknown or duplicate acceptance probe')
        ids = row['check_ids']
        if not isinstance(ids,list) or not ids or any(not isinstance(c,str) for c in ids):
            raise ValueError('Probe assignments require check IDs')
        if len(ids)!=len(set(ids)) or set(ids)&seen or not set(ids)<=goals:
            raise ValueError('Duplicate or unknown probe criterion')
        seen.update(ids);used.add(row['probe_id'])
    unresolved = set(catalogue.get('unresolved_check_ids',[]))
    if seen & unresolved or seen | unresolved != goals:
        raise ValueError('Every acceptance criterion must be assigned or explicitly unresolved')
    return seen


PROBE_PLAN_PROMPT = """Prepare a task catalogue and acceptance assignments for a non-browser artifact.
Use only supplied task and reference sources. Do not infer requirements from solutions or scores.
Preserve supplied task check IDs. Use concise English and the supplied criterion schema,
including requirement_types for new criteria.
The runtime contains fixed executable probes. Assign each criterion to the one probe that can
observe it. A probe may test several criteria. An exit code or a successful render alone does not
prove visual or interactive correctness. A static render cannot establish animation or interaction.
Use acceptance_workflows=[{\"probe_id\":\"existing_id\",\"check_ids\":[\"criterion_id\"]}].
Do not generate shell commands, actions, tests or image paths. Return only the specified JSON fields.
If no probe observes a required condition, keep that criterion in unresolved_check_ids; do not
claim the acceptance configuration is complete. With no probes (catalogue-only preparation),
return acceptance_workflows=[] and unresolved_check_ids=[] unless task sources themselves conflict.
"""


def replay_commands(version, spec, output):
    runtime = spec['runtime']; probes = validate_runtime(runtime)
    external_files = {str(Path(arg).resolve()): hashlib.sha256(Path(arg).read_bytes()).hexdigest()
                      for arg in runtime['command'] + [a for p in probes.values() for a in p['args']]
                      if Path(arg).is_absolute() and '{workspace}' not in arg and '{output}' not in arg and Path(arg).is_file()}
    output = Path(output).resolve(); result_path = output/'result.json'
    if result_path.exists():
        result = read_json(result_path)
        if (result.get('spec') != spec or result.get('code_sha256') != version['code_manifest']['sha256']
                or result.get('external_file_sha256') != external_files):
            raise ValueError('Command acceptance inputs changed')
        for path,digest in result['image_sha256'].items():
            if hashlib.sha256(Path(path).read_bytes()).hexdigest()!=digest:
                raise ValueError('Command acceptance image changed')
        return result
    output.mkdir(parents=True, exist_ok=True)
    workflows, hashes = [], {}
    for assignment in spec['acceptance_workflows']:
        probe = probes[assignment['probe_id']]
        directory = output/probe['probe_id'];directory.mkdir()
        # A separate copy per probe prevents one test from changing another's input.
        workspace = directory/'workspace'
        shutil.copytree(version['workspace'], workspace, symlinks=False)
        evidence = directory/'observations'; evidence.mkdir()
        replacements = {'{workspace}':str(workspace),'{output}':str(evidence)}
        def expand(arg):
            for old,new in replacements.items():arg=arg.replace(old,new)
            return arg
        command = [expand(a) for a in runtime['command']+probe['args']]
        cwd = (workspace/runtime.get('cwd','.')).resolve()
        if not cwd.is_relative_to(workspace):
            raise ValueError('Runtime cwd escapes the copied workspace')
        # A container/sandbox launcher belongs in runtime.command for external code.
        env = {k:v for k,v in os.environ.items() if k in {'PATH','LANG','LC_ALL','DISPLAY','XAUTHORITY'}}
        env['HOME']=str(directory/'home');Path(env['HOME']).mkdir()
        with (directory/'stdout.txt').open('w') as stdout, (directory/'stderr.txt').open('w') as stderr:
            process = subprocess.Popen(command,cwd=cwd,env=env,stdout=stdout,stderr=stderr,start_new_session=True)
            try:
                code=process.wait(timeout=runtime.get('timeout',120))
            except subprocess.TimeoutExpired:
                os.killpg(process.pid,signal.SIGTERM)
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid,signal.SIGKILL);process.wait()
                raise ValueError(f"Acceptance probe timed out: {probe['probe_id']}")
        paths, missing_images, reports = [], [], {}
        for relative in probe['images']:
            path=(evidence/relative).resolve()
            if not path.is_relative_to(evidence):raise ValueError('Image link escapes probe output')
            if not path.is_file():
                missing_images.append(relative)
                continue
            paths.append(str(path));hashes[str(path)]=hashlib.sha256(path.read_bytes()).hexdigest()
        for relative in probe.get('reports',[]):
            path=(evidence/relative).resolve()
            if not path.is_relative_to(evidence):raise ValueError('Report link escapes probe output')
            reports[relative]=path.read_text() if path.is_file() else None
        observation={'stdout':(directory/'stdout.txt').read_text(), 'stderr':(directory/'stderr.txt').read_text(),
                     'returncode':code, 'screenshots':paths, 'missing_images':missing_images, 'reports':reports}
        eid=f"{version['version']}:probe:{probe['probe_id']}"
        workflows.append({'workflow_id':probe['probe_id'],'setup':{'description':probe['description']},
                          'nodes':[{'evidence_id':eid,'run_status':'completed','check_ids':assignment['check_ids'],
                                    'checks':[{'check_id':cid,'assertions':[]} for cid in assignment['check_ids']],
                                    'observation':observation,'command':command}]})
    result={'routes':{},'checks':[],'workflows':workflows,'spec':spec,'external_file_sha256':external_files,
            'code_sha256':version['code_manifest']['sha256'],'image_sha256':hashes}
    write_json(result_path,result)
    return result
