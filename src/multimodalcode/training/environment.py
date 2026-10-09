"""A reset-and-replay Web environment shared by teacher collection and RL."""
from __future__ import annotations

import asyncio
import copy
import functools
import hashlib
import json
import shutil
import socket
import subprocess
import threading
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

from multimodalcode.io import write_json
from multimodalcode.vsv_eval.catalogue import content_hash
from multimodalcode.vsv_eval.repair_pilot import digest_tree
from multimodalcode.vsv_eval.replay import _assertion, _capture, _perform


def _schema(name, description, properties, required):
    return {'type': 'function', 'function': {'name': name, 'description': description,
            'parameters': {'type': 'object', 'properties': properties, 'required': required,
                           'additionalProperties': False}}}


TEXT = {'type': 'string'}
TOOL_SCHEMAS = [
    _schema('read_file', 'Read a UTF-8 file in the task workspace.', {'path': TEXT}, ['path']),
    _schema('write_file', 'Write a UTF-8 file in the task workspace.',
            {'path': TEXT, 'content': TEXT}, ['path', 'content']),
    _schema('edit_file', 'Replace one unique literal occurrence in a UTF-8 file.',
            {'path': TEXT, 'old_string': TEXT, 'new_string': TEXT}, ['path', 'old_string', 'new_string']),
    _schema('browser', 'Execute ordered browser actions, then return the actual screenshot and page text. '
            'Use a relative route for navigate.url. Supported actions: navigate, click, fill, hover, press, '
            'select, scroll, go_back, wait. Targets use selector or role/name. An empty list observes the page.',
            {'actions': {'type': 'array', 'items': {'type': 'object'}}}, ['actions']),
]


class _QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass


def _copy_workspace(source, destination):
    source, destination = Path(source).resolve(), Path(destination).resolve()
    if destination.is_relative_to(source):
        raise ValueError('Place rollout artifacts outside the source workspace')
    if any(p.is_symlink() for p in source.rglob('*')):
        raise ValueError('Use a self-contained workspace without symlinks')
    shutil.copytree(source, destination)


class WebEnvironment:
    """Single-threaded Playwright session; never operates on the source workspace.

    Checkpoints describe deterministic reset/replay, not live process snapshots.
    Backends requiring persistent external state need their own restore adapter.
    """

    def __init__(self, workspace, output, runtime=None):
        self.output = Path(output).resolve()
        self.output.mkdir(parents=True, exist_ok=False)
        self.workspace = self.output / 'workspace'
        _copy_workspace(workspace, self.workspace)
        self.runtime = copy.deepcopy(runtime or {'kind': 'static'})
        self.actions, self.console, self.records = [], [], []
        self.server = self.process = self.playwright = self.browser = None
        self.initial_storage = self.runtime.get('storage_state', {'cookies': [], 'origins': []})
        try:
            self._start()
        except BaseException:
            self.close()
            raise

    def _start(self):
        from playwright.sync_api import sync_playwright
        if self.runtime['kind'] == 'static':
            self.server = ThreadingHTTPServer(('127.0.0.1', 0),
                functools.partial(_QuietHandler, directory=str(self.workspace)))
            threading.Thread(target=self.server.serve_forever, daemon=True).start()
            port = self.server.server_port
        elif self.runtime['kind'] == 'command':
            # Allocate a separate port for each restored branch. Startup failures
            # remain execution errors; another service must not become task evidence.
            with socket.socket() as reservation:
                reservation.bind(('127.0.0.1', int(self.runtime.get('port', 0))))
                port = reservation.getsockname()[1]
            command = [s.replace('{port}', str(port)) for s in self.runtime['command']]
            self.server_log = (self.output / 'server.log').open('w')
            self.process = subprocess.Popen(command, cwd=self.workspace, stdout=self.server_log,
                                            stderr=subprocess.STDOUT)
        else:
            raise ValueError('Supported Web runtimes: static, command')
        self.base_url = f'http://127.0.0.1:{port}'
        deadline = time.monotonic() + self.runtime.get('startup_timeout', 30)
        while True:
            if self.process and self.process.poll() is not None:
                raise RuntimeError('Task server exited during startup; inspect server.log')
            try:
                urllib.request.urlopen(self.base_url, timeout=1).close()
                if self.process:
                    time.sleep(0.1)
                    if self.process.poll() is not None:
                        raise RuntimeError('Task server exited during readiness verification')
                break
            except OSError:
                if time.monotonic() >= deadline:
                    raise RuntimeError('Task server did not become ready')
                time.sleep(0.1)
        self.playwright = sync_playwright().start()
        launch = {'headless': True, 'executable_path': self.runtime.get(
            'executable_path', self.playwright.chromium.executable_path)}
        self.browser = self.playwright.chromium.launch(**launch)
        self.context = self.browser.new_context(viewport=self.runtime.get('viewport', {'width': 1280, 'height': 900}),
                                               storage_state=self.initial_storage)
        self.page = self.context.new_page()
        self.page.set_default_timeout(self.runtime.get('action_timeout_ms', 5000))
        self.page.on('console', lambda msg: self.console.append({'type': msg.type, 'text': msg.text}))
        self.page.goto(self.base_url + self.runtime.get('initial_route', '/'), wait_until='domcontentloaded')

    def _path(self, value):
        path = (self.workspace / value).resolve()
        if not path.is_relative_to(self.workspace) or path == self.workspace:
            raise ValueError('File paths must stay inside the task workspace')
        return path

    def _action(self, action, *, page=None):
        action = copy.deepcopy(action)
        if action['type'] == 'navigate':
            url = str(action['url'])
            if urlsplit(url).netloc or not url.startswith('/') or url.startswith('//'):
                raise ValueError('Navigate using a relative route beginning with /')
            action['url'] = self.base_url + url
        if action['type'] == 'wait' and not 0 <= float(action.get('seconds', 0.5)) <= 10:
            raise ValueError('A wait must be between 0 and 10 seconds')
        _perform(page or self.page, action)

    def _fingerprint(self):
        # Let screenshot caret-style cleanup reach the rendered frame before comparison.
        self.page.evaluate('() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))')
        screenshot_sha256 = hashlib.sha256(self.page.screenshot(full_page=True)).hexdigest()
        state = self.page.evaluate("""() => ({url: location.href, html: document.documentElement.outerHTML,
          scroll: [scrollX, scrollY], forms: [...document.querySelectorAll('input,select,textarea')]
            .map(e => [e.tagName, e.id, e.value, e.checked]), storage: {...localStorage},
          session: {...sessionStorage}})""")
        state['screenshot_sha256'] = screenshot_sha256
        return content_hash(json.loads(json.dumps(state).replace(self.base_url, '{origin}')))

    def snapshot(self, destination):
        destination = Path(destination).resolve()
        destination.mkdir(parents=True, exist_ok=False)
        _copy_workspace(self.workspace, destination / 'workspace')
        record = {'kind': 'web-replay', 'workspace': str(destination / 'workspace'),
                  'code_manifest': digest_tree(destination / 'workspace'), 'runtime': self.runtime,
                  'actions': copy.deepcopy(self.actions), 'state_sha256': self._fingerprint()}
        write_json(destination / 'environment.json', record)
        return record

    @classmethod
    def restore(cls, checkpoint, output):
        if checkpoint.get('kind') != 'web-replay':
            raise ValueError('This adapter requires a Web reset/replay checkpoint')
        if digest_tree(Path(checkpoint['workspace'])) != checkpoint['code_manifest']:
            raise ValueError('Checkpoint workspace changed')
        env = cls(checkpoint['workspace'], output, checkpoint['runtime'])
        try:
            for action in checkpoint['actions']:
                if action['type'] == 'observation_capture':
                    # Screenshot capture can change DOM attributes when restoring the caret.
                    # Replay the observation too, rather than normalizing away real differences.
                    env.page.screenshot(full_page=True)
                else:
                    env._action(action)
                env.actions.append(copy.deepcopy(action))
            if env._fingerprint() != checkpoint['state_sha256']:
                raise ValueError('Browser state could not be reproduced; do not use this checkpoint')
        except BaseException:
            env.close()
            raise
        return env

    def execute(self, name, arguments, call_id):
        from playwright.sync_api import TimeoutError as BrowserTimeout
        before = digest_tree(self.workspace)['sha256']
        images = []
        error = False
        try:
            schema = next((s['function']['parameters'] for s in TOOL_SCHEMAS if s['function']['name'] == name), None)
            if schema is None or set(arguments) != set(schema['required']):
                raise ValueError('Unknown tool or incorrect argument fields')
            if name == 'browser':
                actions = arguments['actions']
                if not isinstance(actions, list) or len(actions) > 30:
                    raise ValueError('Use at most 30 ordered actions per browser call')
                for action in actions:
                    self._action(action)
                    self.actions.append(copy.deepcopy(action))
                observation = _capture(self.page, self.output / 'observations', f'{len(self.records):04d}', self.console)
                self.actions.append({'type': 'observation_capture'})
                images = [observation['screenshot']]
                text = json.dumps({'url': self.page.url.replace(self.base_url, ''),
                                   'title': self.page.title(), 'text': self.page.locator('body').inner_text()[:16000],
                                   'console': self.console[-20:]})
            else:
                path = self._path(arguments['path'])
                if name == 'read_file':
                    text = path.read_text()[:24000]
                else:
                    if name == 'edit_file':
                        old = arguments['old_string']
                        content = path.read_text()
                        if not old or content.count(old) != 1:
                            raise ValueError('Edit requires one unique, nonempty old_string')
                        content = content.replace(old, arguments['new_string'], 1)
                    else:
                        content = arguments['content']
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_text(content)
                    text = 'File updated.'
        except (ValueError, KeyError, TypeError, FileNotFoundError, BrowserTimeout) as exc:
            error, text = True, f'{type(exc).__name__}: {exc}'
        after = digest_tree(self.workspace)['sha256']
        record = {'tool_call_id': call_id, 'tool': name, 'arguments': copy.deepcopy(arguments),
                  'text': text, 'images': images, 'is_error': error,
                  'program_before_sha256': before, 'program_after_sha256': after}
        self.records.append(record)
        write_json(self.output / 'tool_records.json', self.records)
        return record

    def probe(self, definition, name):
        """Run an evaluator probe in a separate browser context, outside agent history."""
        context = self.browser.new_context(viewport=self.runtime.get('viewport', {'width': 1280, 'height': 900}))
        page = context.new_page()
        page.set_default_timeout(self.runtime.get('action_timeout_ms', 5000))
        try:
            page.goto(self.base_url + definition.get('route', '/'), wait_until='domcontentloaded')
            for action in definition.get('actions', []):
                self._action(action, page=page)
            observation = _capture(page, self.output / 'acceptance', name, [])
            assertions = [_assertion(page, a) for a in definition.get('assertions', [])]
            return {'observation': observation, 'assertions': assertions}
        finally:
            context.close()

    def close(self):
        if self.browser:
            self.browser.close()
        if self.playwright:
            self.playwright.stop()
        if self.server:
            self.server.shutdown()
            self.server.server_close()
        if self.process:
            self.process.terminate()
            try:
                self.process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait()
            self.server_log.close()


class AsyncWebEnvironment:
    """Keep all sync Playwright operations on the same dedicated thread."""
    def __init__(self):
        self.pool = ThreadPoolExecutor(max_workers=1)
        self.env = None

    async def restore(self, checkpoint, output):
        self.env = await asyncio.get_running_loop().run_in_executor(
            self.pool, functools.partial(WebEnvironment.restore, checkpoint, output))
        return self

    async def call(self, method, *args):
        return await asyncio.get_running_loop().run_in_executor(
            self.pool, functools.partial(getattr(self.env, method), *args))

    async def close(self):
        if self.env:
            await self.call('close')
        self.pool.shutdown(wait=True)
