"""Bounded real-engine smoke/robustness probe, not held-out handwriting accuracy."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import signal
import struct
import subprocess
import sys
import time
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from src.handwriting.assets import verified_assets
from src.handwriting.protocol import (HEADER, MAX_REQUEST_BYTES, MAX_REPLY_BYTES, VERSION, READY, RECOGNIZE, RESULT,
                                      START, STOP, decode_candidates, encode_strokes, frame)


def limits():
    import resource
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    resource.setrlimit(resource.RLIMIT_AS, (256 * 1024 * 1024,) * 2)
    resource.setrlimit(resource.RLIMIT_CPU, (10, 12))
    resource.setrlimit(resource.RLIMIT_NOFILE, (32, 32))


def bounded_process(assets, data, timeout=10):
    process = subprocess.Popen([str(assets.program), *assets.arguments], stdin=subprocess.PIPE,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=sys.platform != 'win32',
        preexec_fn=limits if sys.platform != 'win32' else None)
    start = time.monotonic()
    try:
        stdout, stderr = process.communicate(data, timeout=timeout)
    except subprocess.TimeoutExpired:
        if sys.platform == 'win32':
            subprocess.run(['taskkill', '/PID', str(process.pid), '/T', '/F'],
                           check=False, capture_output=True, timeout=15)
        else:
            os.killpg(process.pid, signal.SIGKILL)
        process.communicate(timeout=15)
        raise RuntimeError('Native handwriting probe timed out')
    if len(stdout) > 65536 or len(stderr) > 16384:
        raise ValueError('Probe output budget exceeded')
    return process.returncode, stdout, stderr.decode('ascii', errors='strict'), time.monotonic() - start


def replays(root):
    source = root / 'models/handwriting/source/handwriting-zh_TW.xml'
    manifest = json.loads((root / 'models/handwriting/manifest.json').read_text())
    if hashlib.sha256(source.read_bytes()).hexdigest() != manifest['source_sha256']:
        raise ValueError('Model source hash mismatch')
    wanted = set('一二十己已巳臺灣龜鬱鑑靈壹貳參龘')
    cases = []
    for _, element in ET.iterparse(source, events=('end',)):
        if element.tag != 'character':
            continue
        label = element.findtext('utf8')
        if label in wanted:
            width = int(element.findtext('width'))
            height = int(element.findtext('height'))
            strokes = tuple(tuple((round(int(point.get('x')) * 1000 / width),
                                   round(int(point.get('y')) * 1000 / height))
                                  for point in stroke.findall('point'))
                            for stroke in element.findall('strokes/stroke'))
            cases.append((label, strokes))
            wanted.remove(label)
        element.clear()
    if wanted:
        raise ValueError('Missing pinned source fixtures')
    return cases


def probe(report_path, root=ROOT):
    root = Path(root).resolve()
    assets = verified_assets(root)
    cases = replays(root)
    cases += [('synthetic_repeated', (((500, 500),) * 512,)),
              ('synthetic_sawtooth', (tuple((i % 1001, 0 if i % 2 else 1000) for i in range(512)),)),
              ('synthetic_max_points', tuple(tuple((i * 7 % 1001, j * 7 % 1001) for j in range(128))
                                             for i in range(128)))]
    request = frame(START, 0)
    for generation, (_, strokes) in enumerate(cases, 1):
        request += frame(RECOGNIZE, generation, encode_strokes(strokes))
    request += frame(RECOGNIZE, len(cases) + 1, b'\0\0') + frame(STOP, 0)
    report = {'schema': 1, 'probe': 'handwriting_native_helper', 'platform': sys.platform,
              'helper_sha256': hashlib.sha256(assets.program.read_bytes()).hexdigest(),
              'accuracy_evidence': 'UPSTREAM_TRAINING_REPLAY_AND_SYNTHETIC_ROBUSTNESS_ONLY',
              'clean_machine_verified': False, 'cases': [], 'passed': False}
    def save():
        path = Path(report_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    save()
    code, output, stderr, elapsed = bounded_process(assets, request)
    report.update(exit_code=code, stderr=stderr, elapsed_seconds=elapsed)
    save()
    if code != 0 or stderr:
        raise ValueError('Native probe failed')
    cursor = 0
    for index in range(len(cases) + 2):
        if cursor + HEADER.size > len(output):
            raise ValueError('Truncated native probe response')
        magic, version, kind, reserved, generation, length = HEADER.unpack_from(output, cursor)
        cursor += HEADER.size
        if magic != b'ZHW1' or version != VERSION or reserved or length > MAX_REPLY_BYTES or cursor + length > len(output):
            raise ValueError('Malformed native probe response')
        status, values = decode_candidates(output[cursor:cursor + length], assets.alphabet)
        cursor += length
        if index == 0:
            if (kind, generation, status, values) != (READY, 0, 0, ()):
                raise ValueError('Native readiness failure')
            continue
        expected_status = 1 if index == len(cases) + 1 else 0
        record = {'generation': generation, 'kind': kind, 'status': status,
                  'candidates': list(values), 'expected_status': expected_status}
        if index <= len(cases):
            record['fixture'] = cases[index - 1][0]
            record['training_rank'] = values.index(record['fixture']) + 1 if record['fixture'] in values else None
        report['cases'].append(record)
        save()
        if kind != RESULT or generation != index or status != expected_status:
            raise ValueError('Native response sequence/status failure')
        if not expected_status and not values:
            raise ValueError('Native smoke response is empty')
        if index <= 16 and record['training_rank'] is None:
            raise ValueError('Pinned training trace expected label missing from top10')
    if cursor != len(output):
        raise ValueError('Native probe trailing output')
    report['negative_protocol'] = []
    for name, data, expected in (
        ('header', b'BAD!' + frame(START, 0)[4:], 'PROTOCOL_HEADER'),
        ('truncated', b'ZHW', 'PROTOCOL_TRUNCATED'),
        ('oversize', HEADER.pack(b'ZHW1', VERSION, RECOGNIZE, 0, 1, MAX_REQUEST_BYTES + 1), 'PROTOCOL_SIZE')):
        code, output, stderr, elapsed = bounded_process(assets, data, timeout=3)
        record = {'case': name, 'exit_code': code, 'stdout_bytes': len(output), 'stderr': stderr,
                  'expected_error': expected, 'elapsed_seconds': elapsed}
        report['negative_protocol'].append(record)
        save()
        if code != 2 or output or stderr.strip() != expected:
            raise ValueError('Native negative protocol gate failed')
    report['passed'] = True
    save()
    return 0


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--report', required=True)
    arguments = parser.parse_args()
    sys.exit(probe(arguments.report))
