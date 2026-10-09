#!/usr/bin/env python3
"""Download a bounded number of conversation members from an OpenHands archive."""
import argparse
import hashlib
import json
import tarfile
import urllib.request
from pathlib import Path


def sample_archive(url, output, count=2, max_bytes=16 * 1024 * 1024):
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    records = []
    request = urllib.request.Request(url, headers={'Range': f'bytes=0-{max_bytes - 1}'})
    with urllib.request.urlopen(request, timeout=30) as response:
        class LimitedReader:
            downloaded = 0

            def read(self, size):
                if self.downloaded >= max_bytes:
                    raise ValueError('Archive sample exceeded the download budget')
                data = response.read(min(size, max_bytes - self.downloaded))
                self.downloaded += len(data)
                return data

        reader = LimitedReader()
        with tarfile.open(fileobj=reader, mode='r|gz') as archive:
            for member in archive:
                if not (member.isfile() and '/conversations/' in member.name
                        and member.name.endswith('.tar.gz')):
                    continue
                if member.size > max_bytes:
                    raise ValueError('Conversation exceeds the sample budget')
                data = archive.extractfile(member).read()
                target = output / Path(member.name).name
                with target.open('xb') as handle:
                    handle.write(data)
                records.append({'member': member.name, 'path': target.name, 'bytes': len(data),
                                'sha256': hashlib.sha256(data).hexdigest()})
                if len(records) == count:
                    break
    result = {'source_url': url, 'downloaded_bytes': reader.downloaded, 'members': records}
    (output / 'download.json').write_text(json.dumps(result, indent=2) + '\n')
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('url')
    parser.add_argument('--output-dir', type=Path, required=True)
    parser.add_argument('--count', type=int, default=2)
    parser.add_argument('--max-mib', type=int, default=16)
    args = parser.parse_args()
    if args.count < 1 or args.max_mib < 1:
        parser.error('Sample count and download budget must be positive')
    print(json.dumps(sample_archive(args.url, args.output_dir, args.count, args.max_mib * 1024**2)))
