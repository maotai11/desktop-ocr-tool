"""Arbitrate overlapping hypotheses across passes of the SAME recognizer.

Each overlap component selects one complete pass hypothesis. It never appends
both a full line and its second-pass fragments. Disjoint boxes remain distinct,
including identical text at separate locations. Confidence is not calibrated
across different engines and this module must not be used for that purpose.
"""
from .postprocessor import box_bounds


def _area(b):
    return max(0, b[2]-b[0]) * max(0, b[3]-b[1])


def _collides(a, b):
    x1, y1, x2, y2 = box_bounds(a[0])
    u1, v1, u2, v2 = box_bounds(b[0])
    intersection = max(0, min(x2,u2)-max(x1,u1)) * max(0, min(y2,v2)-max(y1,v1))
    smaller = min(_area((x1,y1,x2,y2)), _area((u1,v1,u2,v2)))
    return smaller > 0 and intersection / smaller >= 0.6


def fuse_passes(first, second):
    first, second = list(first or []), list(second or [])
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
            return sum(_area(box_bounds(it[0])) for it in items)
        # Do not replace a whole line with one high-confidence partial crop.
        candidate = choices[1]
        if coverage(candidate) < 0.8 * coverage(choices[0]):
            candidate = choices[0]
        elif score(candidate) <= score(choices[0]):
            candidate = choices[0]
        output.extend(candidate)
    return output
