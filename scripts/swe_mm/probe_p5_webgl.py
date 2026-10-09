"""Run recorded p5.js reproduction and selected original browser tests in isolation."""
import argparse
import functools
import http.server
import json
import shutil
import subprocess
import sys
import threading
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    for name in ('app', 'output', 'dependencies', 'chromium', 'repro'):
        parser.add_argument('--' + name, required=True)
    parser.add_argument('--inside', action='store_true')
    args = parser.parse_args()
    app, output = Path(args.app).resolve(), Path(args.output).resolve()
    if not args.inside:
        prefix = Path(sys.prefix).resolve()
        command = ['bwrap', '--unshare-all', '--die-with-parent', '--new-session',
                   '--ro-bind', '/usr', '/usr', '--ro-bind', '/lib', '/lib',
                   '--ro-bind', '/lib64', '/lib64', '--symlink', 'usr/bin', '/bin',
                   '--proc', '/proc', '--dev', '/dev', '--tmpfs', '/tmp']
        for p in [prefix, Path(args.dependencies).resolve(), Path(args.chromium).resolve().parent,
                  Path(args.repro).resolve(), Path(__file__).resolve()]:
            command += ['--ro-bind', str(p), str(p)]
        command += ['--bind', str(app), str(app), '--bind', str(output), str(output),
                    '--chdir', str(app), '--clearenv', '--setenv', 'HOME', '/tmp',
                    '--setenv', 'PATH', str(prefix / 'bin') + ':/usr/bin:/bin',
                    '--setenv', 'BABEL_CACHE_PATH', '/tmp/babel-register-cache.json',
                    sys.executable, str(Path(__file__).resolve()), *sys.argv[1:], '--inside']
        return subprocess.call(command)
    from playwright.sync_api import sync_playwright
    (app / 'node_modules').symlink_to(Path(args.dependencies) / 'node_modules', target_is_directory=True)
    if not (app / 'repro_test.html').exists():
        shutil.copyfile(args.repro, app / 'repro_test.html')
    elif (app / 'repro_test.html').read_bytes() != Path(args.repro).read_bytes():
        raise ValueError('Recorded reproduction changed')
    builds = []
    for target in ('yuidoc:prod', 'browserify', 'browserify:test'):
        r = subprocess.run(['node', str(Path(args.dependencies) / 'node_modules/grunt-cli/bin/grunt'), target],
                           cwd=app, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=180)
        (output / (target.replace(':', '-') + '.log')).write_text(r.stdout)
        builds.append({'target': target, 'returncode': r.returncode})
        if r.returncode:
            (output / 'probe.json').write_text(json.dumps({'builds': builds, 'status': 'build_failed'}, indent=2))
            return 2
    class QuietHandler(http.server.SimpleHTTPRequestHandler):
        def log_message(self, *args):
            pass
    server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), functools.partial(QuietHandler, directory=str(app)))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    results = []
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=args.chromium, headless=True,
                                   args=['--no-sandbox', '--use-angle=swiftshader', '--enable-unsafe-swiftshader'])
        for name, path, expression in [('repro', '/repro_test.html', 'window.__TEST_RESULT__'),
                ('renderer', '/test/test.html?grep=p5.RendererGL', 'window.mochaResults'),
                ('shader', '/test/test.html?grep=Shader', 'window.mochaResults'),
                ('light', '/test/test.html?grep=light', 'window.mochaResults')]:
            page = browser.new_page(viewport={'width': 1000, 'height': 800}, device_scale_factor=1)
            errors = []
            page.on('pageerror', lambda error: errors.append(str(error)))
            row = {'probe': name, 'path': path, 'page_errors': errors}
            try:
                page.goto(f'http://127.0.0.1:{server.server_port}' + path, timeout=30000)
                page.wait_for_function(expression, timeout=90000)
                row['result'] = page.evaluate('JSON.parse(JSON.stringify(' + expression + '))')
            except Exception as exc:
                row['execution_error'] = str(exc)
            page.screenshot(path=str(output / f'{name}.png'), full_page=False)
            row['text'] = page.locator('body').inner_text()[:30000]
            results.append(row)
            page.close()
        version = browser.version
        browser.close()
    server.shutdown()
    report = {'status': 'executed', 'builds': builds, 'chromium_version': version,
              'origin': 'evaluator_acceptance', 'historical_pixel_equivalence': 'unverified',
              'repro_source_event': 46, 'results': results}
    (output / 'probe.json').write_text(json.dumps(report, indent=2))
    print(json.dumps([{k: v for k, v in r.items() if k not in ['text']} for r in results]))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
