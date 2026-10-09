#!/usr/bin/env python3
"""Serve an exported checkpoint in an isolated container without editing its bytes."""
import argparse
from pathlib import Path
import signal
import subprocess
import uuid
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
from multimodalcode.vsv_eval.archive_replay import blocked_shell


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--image', required=True)
    parser.add_argument('--materials', type=Path, required=True)
    parser.add_argument('--project', default='app')
    parser.add_argument('--port', required=True, type=int)
    args = parser.parse_args()
    code = Path.cwd() / 'app'
    project = Path(args.project)
    if not code.is_dir() or project.is_absolute() or '..' in project.parts:
        raise ValueError('Expected a checkpoint and a relative project directory')
    import json
    startup = code / 'start.sh'
    if startup.is_file() and blocked_shell(startup.read_text()):
        raise ValueError('Recorded startup script requires explicit review before execution')
    package = json.loads((code / project / 'package.json').read_text())
    if any(blocked_shell(command) for command in package.get('scripts', {}).values()):
        raise ValueError('Recorded package scripts require explicit review before execution')
    name = 'vsv-acceptance-' + uuid.uuid4().hex[:12]
    command = ['docker', 'run', '--name', name, '-p', f'127.0.0.1:{args.port}:3000', '--memory=4g', '--cpus=2',
               '--tmpfs', '/workspace:rw',
               '-v', f'{code.resolve()}:/checkpoint:ro',
               '-v', f'{args.materials.resolve()}:{args.materials.resolve()}:ro',
               '-e', 'HTTP_PROXY', '-e', 'HTTPS_PROXY', '-e', 'NO_PROXY',
               '--entrypoint', 'bash', args.image, '-c',
               'set -eu\ncp -a /checkpoint/. /workspace/\n'
               'cd /workspace\nif test -f start.sh; then exec bash start.sh; fi\n'
               'cd "/workspace/$1"\n'
               'npm install --no-audit --no-fund --fetch-retries=1 --fetch-timeout=30000\n'
               'exec npm run dev -- --host 0.0.0.0 --port 3000 --strictPort',
               'vsv-checkpoint', str(project)]
    def stop(*_):
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, stop)
    try:
        process = subprocess.Popen(command)
        return process.wait()
    except KeyboardInterrupt:
        return 130
    finally:
        # Preserve the container for debugging; cleanup is an explicit user action.
        subprocess.run(['docker', 'stop', '-t', '2', name], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


if __name__ == '__main__':
    raise SystemExit(main())
