#!/usr/bin/env python3
"""Bounded, sequential 1-GPU/CPU-worker diagnostic. Never starts extra servers."""
import json
import os
from pathlib import Path
import subprocess
import time
from urllib.request import build_opener, ProxyHandler

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / os.environ.get('VSV_PROBE_OUTPUT', 'runs/vsv_eval/confounder-probe-0912/model-experiment')
SERVER = os.environ.get('VSV_PROBE_SERVER_JOB', 'mmc-q35-vsv-probe-0912')
READY = ROOT / 'runs/agent_smoke/servers' / os.environ.get('VSV_PROBE_SERVER_RUN', 'qwen35-9b-vsv-probe-0912') / 'ready.json'
CONDITIONS = os.environ.get('VSV_PROBE_CONDITIONS', 'infrastructure,checkpoint_baseline,checkpoint_guided').split(',')
JOB_PREFIX = os.environ.get('VSV_PROBE_JOB_PREFIX', 'mmc-vsv-probe-0912')
SOURCE = ROOT / 'runs/vision2web_generation/qwen35-9b-claude-code-official-full-0904/agents/Qwen3.5-9B/vision2web/claude_code/official/webpage__classic-clashes/workspace'
ENV = {k:v for k,v in os.environ.items() if k.lower() not in {'http_proxy','https_proxy','all_proxy'}}


def cluster(*args):
    r = subprocess.run(['clusterx', *args], env=ENV, capture_output=True, text=True, timeout=60)
    if r.returncode:
        raise RuntimeError(r.stdout[-1500:] + r.stderr[-1500:])
    return r.stdout


def image_received(folder):
    for raw in folder.glob('claude.events.attempt-*.jsonl'):
        seen = False
        for line in raw.read_text().splitlines():
            event = json.loads(line)
            if seen and event.get('type') == 'assistant':
                return True
            for b in event.get('message', {}).get('content', []):
                content = b.get('content', [])
                if b.get('type') == 'tool_result' and isinstance(content, list):
                    seen |= any(c.get('type') == 'image' for c in content if isinstance(c, dict))
    return False


def main():
    OUT.mkdir(parents=True, exist_ok=False)
    state = {'server': SERVER, 'workers': [], 'status': 'waiting_for_model'}
    def save():
        (OUT/'state.json').write_text(json.dumps(state, indent=2))
    save()
    active = None
    try:
        deadline = time.monotonic() + 1200
        while time.monotonic() < deadline:
            if READY.exists():
                ready = json.loads(READY.read_text())
                try:
                    with build_opener(ProxyHandler({})).open(ready['endpoint']+'/models', timeout=5) as response:
                        models = json.load(response)
                    if any(m['id'] == 'Qwen3.5-9B' for m in models['data']):
                        break
                except OSError:
                    pass
            time.sleep(10)
        else:
            raise RuntimeError('Model not ready within 20 minutes; no worker submitted')
        state['server_config'] = ready
        for index, condition in enumerate(CONDITIONS):
            active = f'{JOB_PREFIX}-{index}'
            output = OUT / condition
            cluster('run', '--job-name', active, '--num-nodes', '1', '--gpus-per-task', '0',
                    '--cpus-per-task', '4', '--memory-per-task', '16', '--shm-size-gib', '2',
                    '--no-env', '-e', f'PYTHONPATH={ROOT}/src', '--image',
                    'registry.pjlab.org.cn/ccr-t-llm-frontier/vision2web:official-577f939',
                    'timeout', '--kill-after=30s', '1500', 'python3.12',
                    str(ROOT/'scripts/vision2web/probe_vsv_confounders.py'),
                    '--condition', condition, '--source', str(SOURCE), '--output', str(output),
                    '--base-url', ready['endpoint'].removesuffix('/v1'))
            state['workers'].append({'job': active, 'condition': condition, 'output': str(output)})
            state['status'] = 'running_' + condition
            save()
            deadline = time.monotonic() + 1800
            while time.monotonic() < deadline:
                detail = cluster('get-job', active, '--no-verbose')
                if any('JobStatus.'+s in detail for s in ['SUCCEEDED','FAILED','STOPPED']):
                    break
                time.sleep(15)
            else:
                raise RuntimeError(f'Worker deadline: {active}')
            active = None
            if not (output/'result.json').exists():
                raise RuntimeError(f'Worker produced no result: {condition}')
            result = json.loads((output/'result.json').read_text())
            if condition == 'infrastructure':
                passed = result.get('browser_status') == 'passed' and image_received(output)
                state['image_tool_result_followed_by_assistant'] = passed
                save()
                if not passed:
                    raise RuntimeError('Image-context gate failed; behavior comparison not started')
        state['status'] = 'completed_pending_behavior_audit'
    except Exception as exc:
        state.update(status='blocked', error=str(exc))
    finally:
        for job in [active, SERVER]:
            if job:
                try:
                    cluster('stop', job, '--no-confirm')
                    state.setdefault('released_jobs', []).append(job)
                except Exception as exc:
                    state.setdefault('cleanup_errors', []).append(str(exc))
        save()


if __name__ == '__main__':
    main()
