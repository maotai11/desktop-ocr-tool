"""Score the same 108 synthetic screen crops used for the rc.22 comparison.

The decoder gets only the raster. Expected item triples, rc.22 outcomes, and
pixel identities live with the development fixtures, outside the application.
"""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import sys
import time
import zipfile

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from src.invoice_check.qr_items import InvoiceQrError, read_invoice_items


def evaluate(fixtures):
    manifest = json.loads((fixtures / 'cases.json').read_text(encoding='utf-8'))
    recovered_baseline = set(manifest['recovered_in_rc23'])
    if not recovered_baseline.issubset(manifest['cases']):
        raise ValueError('Unknown recovery fixture')
    records = []
    with zipfile.ZipFile(fixtures / 'screens.zip') as archive:
        for key, case in manifest['cases'].items():
            data = np.frombuffer(archive.read(key + '.png'), np.uint8)
            gray = cv2.imdecode(data, cv2.IMREAD_GRAYSCALE)
            if gray is None or hashlib.sha256(gray.tobytes()).hexdigest() != case['sha256']:
                raise ValueError(f'Fixture pixels differ: {key}')
            expected = tuple(map(tuple, manifest['receipts'][case['receipt']]))
            image = cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR)
            start = time.perf_counter()
            try:
                rows = read_invoice_items(image).rows
            except InvoiceQrError:
                rows = ()
            if rows == expected:
                outcome = 'complete'
            elif not rows:
                outcome = 'unreadable'
            elif rows == expected[:len(rows)]:
                outcome = 'partial'
            else:
                outcome = 'wrong'
            records.append({'id': key, 'outcome': outcome, 'rows_read': len(rows),
                            'complete_in_rc22': case['complete_in_rc22'],
                            'required_complete': case['complete_in_rc22'] or key in recovered_baseline,
                            'elapsed_ms': round((time.perf_counter() - start) * 1000, 1)})
    return records


def summarize(records):
    counts = Counter(r['outcome'] for r in records)
    recovered = [r for r in records if not r['complete_in_rc22'] and r['outcome'] == 'complete']
    lost = [r['id'] for r in records if r['required_complete'] and r['outcome'] != 'complete']
    return {'cases': len(records), 'previously_incomplete': sum(not r['complete_in_rc22'] for r in records),
            'recovered': len(recovered), 'outcomes': dict(counts), 'regressions': lost}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--report', type=Path, help='Write a per-image JSON report')
    args = parser.parse_args()
    cv2.setNumThreads(1)
    records = evaluate(ROOT / 'tests' / 'fixtures' / 'invoice_qr')
    summary = summarize(records)
    print(json.dumps(summary))
    if args.report:
        args.report.write_text(json.dumps({'summary': summary, 'cases': records}, indent=2) + '\n',
                               encoding='utf-8')
    return int(bool(summary['regressions'] or summary['outcomes'].get('wrong')))


if __name__ == '__main__':
    sys.exit(main())
