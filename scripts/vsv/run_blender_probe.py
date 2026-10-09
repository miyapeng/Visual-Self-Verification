#!/usr/bin/env python3
"""Run the pinned benchmark renderer without host writes or network access."""
import argparse
import subprocess
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--blender', required=True)
    parser.add_argument('--renderer', required=True)
    parser.add_argument('--app', required=True)
    parser.add_argument('--script', required=True, help='Path relative to the restored app')
    parser.add_argument('--output', required=True)
    parser.add_argument('--samples', type=int, default=64)
    parser.add_argument('--resolution', type=int, default=512)
    args = parser.parse_args()
    script = Path(args.script)
    if script.is_absolute() or '..' in script.parts:
        parser.error('--script must stay inside --app')
    blender = Path(args.blender).resolve()
    command = ['bwrap', '--unshare-all', '--die-with-parent', '--new-session',
               '--ro-bind', '/usr', '/usr', '--ro-bind', '/lib', '/lib',
               '--ro-bind', '/lib64', '/lib64', '--symlink', 'usr/bin', '/bin',
               '--proc', '/proc', '--dev', '/dev', '--tmpfs', '/tmp',
               '--ro-bind', str(blender.parent), '/blender',
               '--ro-bind', str(Path(args.renderer).resolve()), '/renderer.py',
               '--ro-bind', str(Path(args.app).resolve()), '/app',
               '--bind', str(Path(args.output).resolve()), '/output',
               '--chdir', '/tmp', '--clearenv', '--setenv', 'HOME', '/tmp',
               '--setenv', 'PATH', '/usr/bin:/bin',
               '/blender/' + blender.name, '--background', '--threads', '4',
               '--python', '/renderer.py', '--', '--blender-render',
               '--script', '/app/' + script.as_posix(), '--output-dir', '/output',
               '--samples', str(args.samples), '--resolution', str(args.resolution)]
    return subprocess.call(command)


if __name__ == '__main__':
    raise SystemExit(main())
