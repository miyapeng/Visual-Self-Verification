#!/usr/bin/env python3
"""Small checkpoint diagnostic; native Claude/Playwright, no evaluator access."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import time
from urllib.request import build_opener, ProxyHandler

GUIDANCE = """Before submitting, inspect this implementation visually. Read start.sh and
start the application using it; wait until localhost:3000 is accessible. Use
playwright-cli to open the application, take a screenshot, and use Read on the
PNG so you actually see it. Compare the deployed page with the supplied prototype.
Check one user-visible requirement at a time: state the expected result, perform
short relevant browser actions, and read the resulting screenshot and runtime
feedback. Modify code only when the evidence warrants it. After a meaningful
change, recheck the affected behavior and preserve previously correct behavior.
You decide what to check next and when to submit. Do not merely describe checks
without executing them."""

COMMAND_EXAMPLES = """
Tool usage: playwright-cli is already installed. Do not run npm install or
install another browser. After the application is accessible, use Bash:
    playwright-cli open http://localhost:3000
    playwright-cli snapshot
    playwright-cli screenshot --filename=/workspace/check.png
Then call the Read tool on /workspace/check.png to receive the actual image.
snapshot returns textual page structure, not an image. These are command
examples, not evidence that a check has already been performed.
"""


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--source', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--case-id', default='webpage/classic-clashes')
    p.add_argument('--condition', choices=['infrastructure', 'checkpoint_baseline', 'checkpoint_guided', 'checkpoint_guided_commands'], required=True)
    p.add_argument('--base-url')
    p.add_argument('--model', default='Qwen3.5-9B')
    args = p.parse_args()
    workspace = Path('/workspace')
    if workspace.exists() and any(workspace.iterdir()):
        raise RuntimeError('Requires a fresh task container with empty /workspace')
    args.output.mkdir(parents=True, exist_ok=False)
    # Generated workspace only, never the benchmark data/evaluator directory.
    if any(args.source.rglob('workflow.json')):
        raise RuntimeError('Source contains evaluator-only workflow')
    shutil.copytree(args.source, workspace, dirs_exist_ok=True)
    hashes = {str(f.relative_to(workspace)): hashlib.sha256(f.read_bytes()).hexdigest()
              for f in sorted(workspace.rglob('*')) if f.is_file()}
    (args.output/'initial_files.json').write_text(json.dumps(hashes, indent=2))
    os.environ['PLAYWRIGHT_MCP_SANDBOX'] = 'false'
    os.environ['NO_PROXY'] = os.environ['no_proxy'] = 'localhost,127.0.0.1,::1'
    if args.condition != 'infrastructure':
        if not args.base_url:
            raise RuntimeError('An available identical Qwen endpoint is required')
        from dataclasses import replace
        from multimodalcode.agent_harness.cases import load_case
        from multimodalcode.agent_harness.claude_code_runner import run_claude_code
        case = load_case('vision2web', args.case_id)
        prompt = case.prompt + '\nAn existing implementation is present in /workspace. Continue from it to complete the task.\n'
        if args.condition in {'checkpoint_guided', 'checkpoint_guided_commands'}:
            prompt += GUIDANCE
        if args.condition == 'checkpoint_guided_commands':
            prompt += COMMAND_EXAMPLES
        (args.output/'input.txt').write_text(prompt)
        result = run_claude_code(replace(case, prompt=prompt), workspace,
            args.output/'claude.events.jsonl', args.output/'claude.stderr.log',
            model=args.model, base_url=args.base_url,
            api_key=os.environ.get('VSV_PROBE_API_KEY', 'EMPTY'), timeout=1200,
            vision2web_mode='official', max_retries=0)
        (args.output/'result.json').write_text(json.dumps(result, indent=2))
        shutil.copytree(workspace, args.output/'workspace', ignore=shutil.ignore_patterns('node_modules', '.git'))
        return

    records = []
    def run(argv):
        r = subprocess.run(argv, cwd=workspace, text=True, capture_output=True, timeout=90)
        record = dict(command=argv, returncode=r.returncode, stdout=r.stdout, stderr=r.stderr)
        records.append(record)
        (args.output/'commands.json').write_text(json.dumps(records, indent=2))
        if r.returncode or '### Error' in r.stdout:
            raise RuntimeError(f'Failed command: {argv[0:2]}')
        return r.stdout

    server = None
    result = {'condition': 'infrastructure', 'agent_behavior_test': False,
              'model_image_context': 'NOT_TESTED_NO_MODEL_ENDPOINT'}
    try:
        run(['claude', '--version'])
        run(['playwright-cli', '--version'])
        with (args.output/'server.log').open('w') as log:
            server = subprocess.Popen(['bash', 'start.sh'], cwd=workspace, stdout=log,
                                      stderr=subprocess.STDOUT, start_new_session=True)
        opener = build_opener(ProxyHandler({}))
        for _ in range(60):
            try:
                with opener.open('http://localhost:3000', timeout=1) as r:
                    if r.status == 200:
                        break
            except OSError:
                pass
            time.sleep(1)
        else:
            raise RuntimeError('start.sh did not serve a page')
        run(['playwright-cli', 'open', 'http://localhost:3000'])
        run(['playwright-cli', 'snapshot'])
        run(['playwright-cli', 'screenshot', '--filename='+str(args.output/'before.png')])
        run(['playwright-cli', 'run-code', "async page => { await page.getByRole('link').first().click(); return {url:page.url(), title:await page.title()}; }"])
        run(['playwright-cli', 'screenshot', '--filename='+str(args.output/'after.png')])
        run(['playwright-cli', 'console'])
        result['browser_status'] = 'passed'
        if args.base_url:
            from multimodalcode.agent_harness.cases import AgentCase
            from multimodalcode.agent_harness.claude_code_runner import run_claude_code
            # This explicitly prompted read is capability evidence, not initiative.
            image = workspace / 'probe-page.png'
            shutil.copy2(args.output/'after.png', image)
            prompt = ('This is an infrastructure test, not a coding task. Use Read on '
                      '/workspace/probe-page.png. Describe three visible layout or color '
                      'details from the image. Do not inspect source code or modify files.')
            (args.output/'image_read_prompt.txt').write_text(prompt)
            outcome = run_claude_code(AgentCase('vision2web', 'infrastructure/image-read',
                'infrastructure', prompt), workspace, args.output/'claude.events.jsonl',
                args.output/'claude.stderr.log', model=args.model, base_url=args.base_url,
                api_key=os.environ.get('VSV_PROBE_API_KEY', 'EMPTY'), timeout=600,
                vision2web_mode='official', max_retries=0)
            result['image_read_run'] = outcome
            result['model_image_context'] = 'REQUIRES_RAW_IMAGE_RESULT_AND_FOLLOWING_TURN_AUDIT'
    except Exception as exc:
        result.update(browser_status='failed', error=str(exc))
    finally:
        subprocess.run(['playwright-cli', 'close'], capture_output=True, timeout=30)
        if server:
            os.killpg(server.pid, signal.SIGTERM)
            server.wait(timeout=10)
        (args.output/'result.json').write_text(json.dumps(result, indent=2))
    print(json.dumps(result))


if __name__ == '__main__':
    main()
