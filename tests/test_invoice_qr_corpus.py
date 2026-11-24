"""Replay fixed screen pixels through the same decoder as the floating window."""
import cv2

from scripts.benchmark_invoice_qr import ROOT, evaluate, summarize


def test_preserves_recovered_receipts_and_never_returns_incorrect_items():
    previous_threads = cv2.getNumThreads()
    try:
        cv2.setNumThreads(1)
        records = evaluate(ROOT / 'tests' / 'fixtures' / 'invoice_qr')
    finally:
        cv2.setNumThreads(previous_threads)
    summary = summarize(records)
    assert summary['cases'] == 108
    assert summary['previously_incomplete'] == 35
    assert sum(r['required_complete'] for r in records) == 100
    assert not summary['regressions'], summary['regressions']
    assert not [r['id'] for r in records if r['outcome'] == 'wrong']
