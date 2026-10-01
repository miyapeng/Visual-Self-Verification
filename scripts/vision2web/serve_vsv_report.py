#!/usr/bin/env python3
"""Serve a report and its explicit local links, not the whole research repository."""
import argparse
import html
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import mimetypes
from pathlib import Path
import re
from urllib.parse import unquote, urljoin, urlsplit


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--report', type=Path, required=True)
    parser.add_argument('--reference-root', type=Path, required=True, action='append',
                        help='Allowed linked artifact directory; repeat for separate report roots')
    parser.add_argument('--port', type=int, default=8097)
    args = parser.parse_args()
    report = args.report.resolve()
    allowed = [report.parent, *(root.resolve() for root in args.reference_root)]
    files = {}

    def register(url, path):
        path = path.resolve()
        if not any(path.is_relative_to(root) for root in allowed) or not path.is_file():
            raise ValueError(f'Invalid report link: {url} -> {path}')
        if url in files:
            if files[url] != path:
                raise ValueError(f'Conflicting report links: {url}')
            return
        files[url] = path
        if path.suffix.lower() == '.html':
            for value in re.findall(r'(?:href|src)="([^"]+)"', path.read_text()):
                link = html.unescape(value)
                parsed = urlsplit(link)
                if parsed.scheme or parsed.netloc or not parsed.path:
                    continue
                register(unquote(urlsplit(urljoin('http://report.local' + url, link)).path), path.parent / unquote(parsed.path))

    register('/', report)
    register('/index.html', report)
    for name in ('README.md', 'scores.json'):
        if (report.parent / name).is_file():
            register('/' + name, report.parent / name)

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            path = files.get(unquote(urlsplit(self.path).path))
            if path is None:
                self.send_error(404)
                return
            data = path.read_bytes()
            self.send_response(200)
            self.send_header('Content-Type', mimetypes.guess_type(path.name)[0] or 'application/octet-stream')
            self.send_header('Content-Length', str(len(data)))
            self.send_header('Cache-Control', 'no-cache')
            self.end_headers()
            self.wfile.write(data)

    print(f'http://127.0.0.1:{args.port}/ — {len(files)} explicitly linked files', flush=True)
    ThreadingHTTPServer(('127.0.0.1', args.port), Handler).serve_forever()


if __name__ == '__main__':
    main()
