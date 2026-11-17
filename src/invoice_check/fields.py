"""Conservative local field interpretation; original OCR observations remain intact."""
import math
import re
import unicodedata
from datetime import date
from decimal import Decimal, InvalidOperation

FIELD_KEYS = ('net_amount', 'tax_amount', 'total_amount', 'issue_date', 'invoice_number', 'seller_tax_id', 'buyer_tax_id')
LABELS = dict(zip(FIELD_KEYS, ('未稅金額', '稅額', '總金額', '開立日期', '發票號碼', '賣方統編', '買方統編')))
ANCHORS = {'net_amount': ('銷售額合計', '未稅金額', '未稅', '銷售額'), 'tax_amount': ('營業稅', '稅額'), 'total_amount': ('總計', '總金額', '含稅金額'), 'issue_date': ('開立日期', '日期'), 'invoice_number': ('發票號碼', '發票號', '字軌'), 'seller_tax_id': ('賣方統編', '賣方統一編號'), 'buyer_tax_id': ('買方統編', '買方統一編號')}


def normalize_value(key, raw):
    """Return canonical text or None; never fix an OCR digit by guessing."""
    s = unicodedata.normalize('NFKC', str(raw)).strip()
    if key.endswith('amount'):
        s = re.sub(r'^(?:NT\$|NTD|\$|新臺幣|新台幣)\s*', '', s, flags=re.IGNORECASE)
        s = re.sub(r'\s*元$', '', s)
        if not re.fullmatch(r'-?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?', s):
            return None
        try:
            value = Decimal(s.replace(',', ''))
            exact = format(value, 'f')
            return exact.rstrip('0').rstrip('.') if '.' in exact else exact
        except InvalidOperation:
            return None
    if key == 'issue_date':
        era = 'roc' if s.startswith('民國') else 'ad' if s.startswith('西元') else None
        s = re.sub(r'^(?:民國|西元)\s*', '', s)
        compact = re.fullmatch(r'(\d{3}|\d{4})(\d{2})(\d{2})', s)
        parts = compact or re.fullmatch(r'(\d{1,4})\s*(?:[./-]|年)\s*(\d{1,2})\s*(?:[./-]|月)\s*(\d{1,2})日?', s)
        if not parts:
            return None
        y, m, d = map(int, parts.groups())
        if (era == 'roc' and not 1 <= y <= 999) or (era == 'ad' and len(parts.group(1)) != 4):
            return None
        if y < 1911 and era != 'ad':
            if not 1 <= y <= 999:
                return None
            y += 1911
        try:
            return date(y, m, d).isoformat()
        except ValueError:
            return None
    if key == 'invoice_number':
        s = re.sub(r'[\s-]', '', s).upper()
        return s if re.fullmatch(r'[A-Z]{2}\d{8}', s) else None
    if key.endswith('tax_id'):
        s = re.sub(r'\s', '', s)
        return s if re.fullmatch(r'\d{8}', s) else None
    return None


def valid_ubn(value):
    if not value or value == '00000000' or not re.fullmatch(r'\d{8}', value):
        return False
    products = [int(n) * w for n, w in zip(value, (1, 2, 1, 2, 1, 2, 4, 1))]
    total = sum(p // 10 + p % 10 for p in products)
    return total % 5 == 0 or (value[6] == '7' and (total + 1) % 5 == 0)


def _box(observation):
    b = observation.get('box')
    if b is None:
        return None
    if len(b) == 4 and all(isinstance(v, (int, float)) for v in b):
        return list(map(float, b))
    try:
        xs, ys = zip(*b)
        return [float(min(xs)), float(min(ys)), float(max(xs)-min(xs)), float(max(ys)-min(ys))]
    except (ValueError, TypeError):
        return None


def _observations(detail):
    result = []
    for item in detail:
        box = _box(item)
        if box and all(math.isfinite(v) for v in box) and box[2] > 0 and box[3] > 0:
            result.append((item, box, str(item.get('raw_text', item.get('text', '')))))
    return result


def _invoice_observations(detail):
    """Join only geometrically adjacent, syntactically complete observations."""
    observations = _observations(detail)
    extras = []
    for item, box, raw in observations:
        x, y, w, h = box
        peers = [(other, b, text) for other, b, text in observations if other is not item
                 and b[0] >= x+w-2
                 and abs(b[1]+b[3]/2-y-h/2) <= max(h, b[3])*.4]
        peers.sort(key=lambda entry: entry[1][0])
        # Joining must not skip intervening observations on this baseline.
        chain = [(item, box, raw)]
        for other, b, text in peers[:4]:
            previous = chain[-1][1]
            if b[0]-(previous[0]+previous[2]) > max(previous[3],b[3])*2:
                break
            chain.append((other,b,text))
            joined = ''.join(part[2].strip() for part in chain)
            key = None
            if re.fullmatch(r'[A-Za-z]{2}', raw.strip()) and normalize_value('invoice_number', joined):
                key = 'invoice_number'
            if normalize_value('issue_date', joined) and any(mark in joined for mark in ('年','/','-','.')):
                key = 'issue_date'
            if key:
                right = max(part[1][0]+part[1][2] for part in chain)
                top = min(part[1][1] for part in chain)
                bottom = max(part[1][1]+part[1][3] for part in chain)
                merged = {'raw_text': joined, 'confidence': min(float(part[0].get('confidence',0)) for part in chain),
                          '_field_hint': key, '_parts': [dict(part[0]) for part in chain]}
                if any(part[0].get('alternatives') for part in chain):
                    merged['alternatives'] = [part[0].get('alternatives') for part in chain]
                extras.append((merged,[x,top,right-x,bottom-top],joined))
                break
    # Plain tax-ID labels are assigned only with one nearby explicit party context.
    for item, box, raw in observations:
        if '統一編號' not in raw or any(a in raw for a in ('買方','賣方')) or not _strong(item):
            continue
        x,y,w,h = box
        parties = set()
        for context, b, text in observations:
            if not _strong(context):
                continue
            if abs((b[0]+b[2]/2)-(x+w/2)) > max(w,b[2])*1.5 or abs(b[1]-y) > max(h,b[3])*6:
                continue
            if any(a in text for a in ('買受人','買方')):
                parties.add('buyer_tax_id')
            if any(a in text for a in ('賣方','營業人','發票專用章')):
                parties.add('seller_tax_id')
        if len(parties) == 1:
            item = dict(item, _field_hint=parties.pop())
            extras.append((item,box,raw))
    return observations + extras


def _strong(item):
    try:
        confidence = float(item.get('confidence', 0))
        return math.isfinite(confidence) and .85 <= confidence <= 1 and not item.get('alternatives')
    except (TypeError, ValueError):
        return False


def _empty(key):
    return {'key': key, 'label': LABELS[key], 'raw': '', 'value': None, 'box': None, 'confidence': 0.0, 'ambiguous': False, 'candidates': []}


def _extract(detail, image_size, overrides=None, headers=None, invoice=False):
    # image_size is (width, height); overrides are normalized XYWH.
    width, height = image_size
    if not all(isinstance(v, (int, float)) and math.isfinite(v) and v > 0 for v in image_size):
        raise ValueError('Invalid image size')
    for region in [*(overrides or {}).values(), *(headers or {}).values()]:
        if len(region) != 4 or not all(isinstance(v, (int, float)) and math.isfinite(v) for v in region):
            raise ValueError('Invalid normalized field region')
        x, y, w, h = region
        if x < 0 or y < 0 or w <= 0 or h <= 0 or x+w > 1.000001 or y+h > 1.000001:
            raise ValueError('Field region outside image')
    observations = _invoice_observations(detail) if invoice else _observations(detail)
    fields = {}
    for key in FIELD_KEYS:
        candidates = []
        anchors = [(b, text) for item, b, text in observations if _strong(item) and (any(a in text for a in ANCHORS[key]) or item.get('_field_hint') == key)]
        region = (overrides or {}).get(key)
        if region is None and headers:
            region = headers.get(key)
        for item, box, raw in observations:
            x, y, w, h = box
            if item.get('_field_hint') and item['_field_hint'] != key:
                continue
            value = normalize_value(key, raw)
            anchored_inline = item.get('_field_hint') == key
            if anchored_inline and key.endswith('tax_id'):
                value = normalize_value(key, raw.split('統一編號',1)[-1].lstrip(' :：'))
            if value is None:
                for anchor in ANCHORS[key]:
                    if anchor in raw:
                        value = normalize_value(key, raw.split(anchor, 1)[1].lstrip(' :：'))
                        anchored_inline = value is not None and _strong(item)
                        if value is not None:
                            break
            if value is None:
                continue
            if region is not None:
                rx, ry, rw, rh = region
                if not (rx*width <= x+w/2 <= (rx+rw)*width and ry*height <= y+h/2 <= (ry+rh)*height):
                    continue
                score = 0
            else:
                distances = [abs(y+h/2-(ay+ah/2)) + max(0, x-(ax+aw))*.1 for (ax, ay, aw, ah), _ in anchors if abs(y+h/2-(ay+ah/2)) <= max(h, ah)*1.3 and x >= ax]
                if anchored_inline:
                    score = 0
                elif distances:
                    score = min(distances)
                elif key in ('issue_date', 'invoice_number'):
                    score = height + y
                else:
                    continue  # An unlabelled number is not evidence of its field type.
            try:
                confidence = float(item.get('confidence', 0))
            except (TypeError, ValueError):
                confidence = 0.0
            candidate = {'raw': raw, 'value': value, 'box': box, 'confidence': confidence, 'score': score}
            if not invoice and region is None and not anchored_inline and not anchors:
                candidate['unmapped'] = True
            if item.get('_parts'):
                candidate['parts'] = item['_parts']
            alternatives = item.get('alternatives')
            if alternatives:
                candidate['alternatives'] = alternatives
            candidates.append(candidate)
        candidates.sort(key=lambda c: c['score'])
        field = _empty(key)
        if candidates:
            best = candidates[0]
            field.update({k: best[k] for k in ('raw', 'value', 'box', 'confidence')})
            field['candidates'] = candidates
            field['ambiguous'] = bool(best.get('unmapped')) or len(candidates) > 1 or bool(best.get('alternatives')) or not math.isfinite(best['confidence']) or best['confidence'] < .85 or best['confidence'] > 1
            if key.endswith('tax_id') and not valid_ubn(best['value']):
                field['ambiguous'] = True
        fields[key] = field
    # Compare original component provenance, not only a joined bounding rectangle.
    def sources(field):
        if not field['candidates']:
            return set()
        candidate = field['candidates'][0]
        parts = candidate.get('parts')
        if parts:
            return {(tuple(_box(part) or ()), str(part.get('raw_text', part.get('text', '')))) for part in parts}
        return {(tuple(candidate['box']), candidate['raw'])}

    for key, field in fields.items():
        provenance = sources(field)
        if provenance and any(other != key and provenance & sources(f) for other, f in fields.items()):
            field['ambiguous'] = True
    return fields


def extract_invoice_fields(detail, image_size, overrides=None):
    return _extract(detail, image_size, overrides, invoice=True)


def extract_row_fields(detail, image_size, headers=None, overrides=None):
    return _extract(detail, image_size, overrides, headers)


def compare_fields(invoice, row, amount_key='net_amount', tax_id_key='seller_tax_id'):
    if amount_key not in ('net_amount', 'total_amount') or tax_id_key not in ('seller_tax_id', 'buyer_tax_id'):
        raise ValueError('Unsupported comparison field')
    results = []
    for key in (amount_key, 'tax_amount', 'issue_date', 'invoice_number', tax_id_key):
        a, b = invoice.get(key, _empty(key)), row.get(key, _empty(key))
        if a.get('value') is None or b.get('value') is None:
            status, message = 'missing', '缺少可辨識的欄位'
        elif a.get('ambiguous') or b.get('ambiguous'):
            status, message = 'review', '有多個候選或格式疑慮，請確認'
        elif a['value'] == b['value']:
            status, message = 'matched', '一致'
        else:
            status, message = 'different', '數值不同，請核對原文'
        results.append({'key': key, 'label': LABELS[key], 'status': status, 'invoice': a, 'row': b, 'message': message})
    return results


def header_columns(detail, image_size, amount_key='net_amount', tax_id_key='seller_tax_id'):
    """Map unambiguous labelled header centres to non-overlapping row columns."""
    if amount_key not in ('net_amount', 'total_amount') or tax_id_key not in ('seller_tax_id', 'buyer_tax_id'):
        raise ValueError('Unsupported header preset')
    width, _ = image_size
    found = []
    for item, box, raw in _observations(detail):
        keys = [key for key in FIELD_KEYS if any(anchor in raw for anchor in ANCHORS[key])]
        # Only table headers use the user's selected generic column roles.
        plain = unicodedata.normalize('NFKC', raw).strip(' :：')
        preset = {'金額': amount_key, '憑證號碼': 'invoice_number', '統編': tax_id_key,
                  '統一編號': tax_id_key, '稅': 'tax_amount'}.get(plain)
        if preset is not None:
            keys = [preset]
        # Longer explicit labels distinguish tax total versus net and seller/buyer.
        found.append((box[0] + box[2]/2, keys[0] if len(keys) == 1 and _strong(item) else None))
    found.sort(key=lambda entry: entry[0])
    result = {}
    for index, (centre, key) in enumerate(found):
        if key is None or sum(c == centre for c, _ in found) != 1:
            continue
        if sum(k == key for _, k in found) != 1:
            continue
        left = (found[index-1][0]+centre)/2 if index else 0
        right = (centre+found[index+1][0])/2 if index+1 < len(found) else width
        result[key] = [left/width, 0, (right-left)/width, 1]
    return result
