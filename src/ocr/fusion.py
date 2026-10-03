"""Arbitrate overlapping hypotheses across passes of the SAME recognizer.

Each overlap component selects one complete pass hypothesis. It never appends
both a full line and its second-pass fragments. Disjoint boxes remain distinct,
including identical text at separate locations. Confidence is not calibrated
across different engines and this module must not be used for that purpose.
"""
from .postprocessor import box_bounds
import math


def _area(b):
    return max(0, b[2]-b[0]) * max(0, b[3]-b[1])


def _collides(a, b):
    x1, y1, x2, y2 = box_bounds(a[0])
    u1, v1, u2, v2 = box_bounds(b[0])
    intersection = max(0, min(x2,u2)-max(x1,u1)) * max(0, min(y2,v2)-max(y1,v1))
    smaller = min(_area((x1,y1,x2,y2)), _area((u1,v1,u2,v2)))
    return smaller > 0 and intersection / smaller >= 0.6


def fuse_passes(first, second):
    first = list(first) if first is not None else []
    second = list(second) if second is not None else []
    if not first or not second:
        return first or second
    nodes = [(0, i) for i in range(len(first))] + [(1, i) for i in range(len(second))]
    edges = {n: set() for n in nodes}
    for i, a in enumerate(first):
        for j, b in enumerate(second):
            if _collides(a,b):
                edges[0,i].add((1,j)); edges[1,j].add((0,i))
    output, visited = [], set()
    for node in nodes:
        if node in visited:
            continue
        stack, group = [node], set()
        while stack:
            n = stack.pop()
            if n not in group:
                group.add(n); stack.extend(edges[n] - group)
        visited.update(group)
        choices = [[(first,second)[p][i] for p,i in sorted(group) if p==side] for side in (0,1)]
        if not choices[0] or not choices[1]:
            output.extend(choices[0] or choices[1]); continue
        def score(items):
            weight = sum(max(1,len(it[1])) for it in items)
            return sum(float(it[2])*max(1,len(it[1])) for it in items)/weight
        def coverage(items):
            return _union_area([box_bounds(it[0]) for it in items])
        # Do not replace a whole line with one high-confidence partial crop.
        candidate = choices[1]
        if coverage(candidate) < 0.8 * coverage(choices[0]):
            candidate = choices[0]
        elif (coverage(candidate) > 1.2 * coverage(choices[0])
              and sum(len(it[1]) for it in candidate) > sum(len(it[1]) for it in choices[0])):
            # A high-confidence short hypothesis must not erase recovered text.
            # This is coverage preservation, not evidence the extra text is right.
            pass
        elif score(candidate) <= score(choices[0]):
            candidate = choices[0]
        output.extend(candidate)
    return output


def _union_area(rectangles):
    """Rectangle union, so duplicate overlapping fragments cannot inflate coverage."""
    xs = sorted({x for r in rectangles for x in (r[0], r[2])})
    total = 0.
    for x1, x2 in zip(xs, xs[1:]):
        intervals = sorted((r[1], r[3]) for r in rectangles if r[0] < x2 and r[2] > x1)
        end, height = -math.inf, 0.
        for low, high in intervals:
            height += max(0., high - max(low, end))
            end = max(end, high)
        total += (x2 - x1) * height
    return total


def _stitch_horizontal(a, b):
    """Join agreeing seam fragments only when text overlap agrees with geometry.

    No language correction, Unicode normalization, or character substitution.
    Ambiguous/non-horizontal overlaps are left for review by the caller.
    """
    ab, bb = box_bounds(a[0]), box_bounds(b[0])
    if ab[0] > bb[0]:
        return _stitch_horizontal(b, a)
    aw, bw = ab[2] - ab[0], bb[2] - bb[0]
    ah, bh = ab[3] - ab[1], bb[3] - bb[1]
    overlap = ab[2] - bb[0]
    vertical = min(ab[3], bb[3]) - max(ab[1], bb[1])
    if (min(aw, bw, ah, bh) <= 0 or ab[2] >= bb[2]
            or overlap <= 0 or vertical < .7 * min(ah, bh)
            or aw < 2 * ah or bw < 2 * bh):
        return None
    left, right = a[1], b[1]
    if not left or not right:
        return None
    char_width = (aw / len(left) + bw / len(right)) / 2
    matches = []
    for length in range(2, min(len(left), len(right))):
        error = abs(overlap - length * char_width)
        if (left[-length:] == right[:length]
                and error <= max(char_width * .75, overlap * .15)):
            matches.append((error, length))
    if not matches:
        return None
    matches.sort()
    # Repeated digits/periodic strings can have several exact text overlaps.
    # The longest plausible match can silently delete genuine repetitions.
    # Require a distinct best geometric fit, allowing for box-edge uncertainty;
    # otherwise the caller retains both raw hypotheses and flags the conflict.
    if len(matches) > 1 and matches[1][0] - matches[0][0] <= char_width * .25:
        return None
    length = matches[0][1]
    text = left + right[length:]
    x1, y1, x2, y2 = ab[0], min(ab[1], bb[1]), bb[2], max(ab[3], bb[3])
    return [[[x1, y1], [x2, y1], [x2, y2], [x1, y2]], text, min(a[2], b[2])]


def fuse_tiles(first, second):
    """Reconcile same-model tiles in source coordinates; report unresolved seams.

    Disjoint repeats are retained. Contained hypotheses use the pass arbitrator;
    horizontally extended hypotheses stitch only with exact text/geometry support.
    Uncertain partial overlaps remain visible and require review, never silent loss.
    """
    output, conflicts = list(first), 0
    for candidate in second:
        index = 0
        while index < len(output):
            prior = output[index]
            ab, bb = box_bounds(prior[0]), box_bounds(candidate[0])
            intersection = max(0., min(ab[2], bb[2]) - max(ab[0], bb[0])) * max(
                0., min(ab[3], bb[3]) - max(ab[1], bb[1]))
            if intersection == 0:
                index += 1
                continue
            stitched = _stitch_horizontal(prior, candidate)
            if stitched is not None:
                candidate = stitched
                output.pop(index)
                index = 0
                continue
            small, large = min(_area(ab), _area(bb)), max(_area(ab), _area(bb))
            min_width = min(ab[2] - ab[0], bb[2] - bb[0])
            min_height = min(ab[3] - ab[1], bb[3] - bb[1])
            edge_slack = min(.2 * min_width, max(.25 * min_height, .05 * min_width))
            horizontally_contained = (
                ab[0] - edge_slack <= bb[0] and bb[2] <= ab[2] + edge_slack
            ) or (
                bb[0] - edge_slack <= ab[0] and ab[2] <= bb[2] + edge_slack
            )
            # A high intersection ratio is not containment: two extended
            # periodic fragments may overlap 75% yet each hold unique text.
            # Do not let an ambiguous stitch fall through into de-duplication.
            contained = small > 0 and intersection / small >= .6 and horizontally_contained
            similar = large > 0 and intersection / large >= .7
            # Whitespace is ignored ONLY for spatial duplicate matching; the
            # winning hypothesis retains its exact text and raw alternatives.
            prior_key, candidate_key = ''.join(prior[1].split()), ''.join(candidate[1].split())
            related = bool(prior_key and candidate_key) and (
                prior_key in candidate_key or candidate_key in prior_key)
            if contained and (similar or related):
                chosen = fuse_passes([prior], [candidate])[0]
                if prior[1] != candidate[1] and similar:
                    conflicts += 1
                candidate = chosen
                output.pop(index)
                index = 0
                continue
            # Only significant line-level overlaps count as seam conflicts.
            if small > 0 and intersection / small >= .15:
                conflicts += 1
            index += 1
        output.append(candidate)
    return output, conflicts


def has_text_conflicts(first, second):
    """Conservative review signal; retain raw alternatives for the human reader."""
    return any(_collides(a, b) and a[1] != b[1] for a in first for b in second)
