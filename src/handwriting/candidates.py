"""Deterministic candidate union; incomparable scores are never combined."""
from dataclasses import dataclass


@dataclass(frozen=True)
class Candidate:
    label: str
    raw_rank: int = 0
    normalized_rank: int = 0
    image_rank: int = 0

    @property
    def sources(self):
        return tuple(name for rank, name in (
            (self.raw_rank, '筆畫原始'), (self.normalized_rank, '筆畫置中'),
            (self.image_rank, '影像補辨')) if rank)


def merge_stroke_candidates(raw, normalized):
    """Keep raw first ten stable, then alternate normalized/raw, unique <=60.

    This is a bounded availability policy, not a calibrated accuracy ranking.
    Every original Top30 remains reachable, including reverse-stroke cases for
    which bbox normalization loses a correct candidate.
    """
    raw_rank = {label: index+1 for index, label in enumerate(raw)}
    normalized_rank = {label: index+1 for index, label in enumerate(normalized)}
    order = list(raw[:10])
    for index in range(max(len(raw), len(normalized))):
        if index < len(normalized):
            order.append(normalized[index])
        if index+10 < len(raw):
            order.append(raw[index+10])
    unique = dict.fromkeys(order)
    return tuple(Candidate(label, raw_rank.get(label, 0), normalized_rank.get(label, 0))
                 for label in unique)


def is_cjk_scalar(label):
    if len(label) != 1:
        return False
    code = ord(label)
    return any(start <= code <= end for start, end in (
        (0x3400, 0x4dbf), (0x4e00, 0x9fff), (0xf900, 0xfaff),
        (0x20000, 0x323af)))
