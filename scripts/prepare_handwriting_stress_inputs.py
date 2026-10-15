"""Seal public model-source replay inputs before observing recognition output.

These are training traces and synthetic geometry stress cases, never human
accuracy data. No application files, environments, or model bytes are changed.
"""
from pathlib import Path
import hashlib
import json
import xml.etree.ElementTree as ET

PRODUCT = Path(__file__).resolve().parents[1]
HERE = None
SOURCE = PRODUCT / 'models/handwriting/source/handwriting-zh_TW.xml'
EXPECTED_SOURCE_SHA = '85ab37c56bcb14a54a7ae106f2504b6505cf6c9917ca8c636312153fd9d00e53'
SEED = 'InkPick-geometry-stress-v1-fixed-before-inference'
COUNT = 128


def digest(data):
    return hashlib.sha256(data).hexdigest()


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()


def scaled(strokes, scale, dx, dy):
    return [[[round(x * scale + dx), round(y * scale + dy)] for x, y in stroke] for stroke in strokes]


def normalize(strokes):
    points = [point for stroke in strokes for point in stroke]
    x0, x1 = min(p[0] for p in points), max(p[0] for p in points)
    y0, y1 = min(p[1] for p in points), max(p[1] for p in points)
    span = max(x1 - x0, y1 - y0)
    if span == 0:
        return [[[500, 500] for _ in stroke] for stroke in strokes]
    scale = 800 / span
    return [[[round((x - (x0+x1)/2)*scale + 500), round((y - (y0+y1)/2)*scale + 500)]
             for x, y in stroke] for stroke in strokes]


def main():
    assert digest(SOURCE.read_bytes()) == EXPECTED_SOURCE_SHA
    characters = {}
    for _, element in ET.iterparse(SOURCE, events=['end']):
        if element.tag != 'character':
            continue
        label = element.findtext('utf8')
        assert label not in characters
        assert element.findtext('width') == element.findtext('height') == '1000'
        strokes = [[[int(p.get('x')), int(p.get('y'))] for p in s.findall('point')]
                   for s in element.findall('strokes/stroke')]
        assert strokes and all(strokes)
        characters[label] = strokes
        element.clear()
    assert len(characters) == 11853
    labels = sorted(characters, key=lambda c: digest((SEED+c).encode()))[:COUNT]
    cases = []
    for label in labels:
        raw = characters[label]
        variants = {
            'source': raw,
            'small_center': scaled(raw, .45, 275, 275),
            'small_top_left': scaled(raw, .45, 20, 20),
            'small_bottom_right': scaled(raw, .45, 530, 530),
            'reverse_stroke_order': list(reversed(raw)),
            'reverse_each_stroke_direction': [list(reversed(s)) for s in raw],
        }
        for name, strokes in variants.items():
            for processing, candidate in [('raw', strokes), ('bbox_800', normalize(strokes))]:
                cases.append({'id': f'U{ord(label):04X}:{name}:{processing}',
                    'expected_source_label': label, 'variant': name, 'processing': processing,
                    'strokes': candidate, 'stroke_sha256': digest(canonical(candidate))})
    result = {'schema': 1, 'evidence_scope': 'UPSTREAM_TRAINING_REPLAY_AND_SYNTHETIC_STRESS_ONLY',
        'human_accuracy': 'NOT_MEASURED', 'source_sha256': EXPECTED_SOURCE_SHA,
        'selection': {'method': 'first 128 labels sorted by sha256(seed+label)',
                      'seed': SEED, 'count': COUNT, 'selected_labels': labels},
        'processing_policy': 'bbox_800 always uses aspect-preserving longest dimension 800, centered at 500',
        'candidate_policy': 'Top1, Top10 and Top30 are prefixes of exact Zinnia ranking; no GT-dependent reranking',
        'ocr_policy': 'Raw PP-OCRv6-small recognition of fixed white 256px raster, black 3px strokes; no detector, zhconv or text cleanup',
        'cases': cases}
    data = canonical(result) + b'\n'
    target = HERE / 'inputs.json'
    if target.exists():
        assert target.read_bytes() == data, 'Refuse to replace a different sealed input set'
    else:
        target.write_bytes(data)
    receipt = {'input_manifest_sha256': digest(data), 'case_count': len(cases),
               'source_labels': COUNT, 'evidence_scope': result['evidence_scope'],
               'input_bytes': len(data)}
    (HERE / 'input-receipt.json').write_text(json.dumps(receipt, indent=2)+'\n')
    print(json.dumps(receipt))


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    HERE = args.output.resolve()
    HERE.mkdir(parents=True, exist_ok=True)
    main()
