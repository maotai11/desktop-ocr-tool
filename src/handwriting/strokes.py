"""Bounded immutable stroke snapshots in a fixed, aspect-preserving canvas."""
from .protocol import CANVAS_SIZE, MAX_POINTS, MAX_POINTS_PER_STROKE, MAX_STROKES


class StrokeBuffer:
    def __init__(self):
        self.strokes = []
        self.active = None
        self.revision = 0

    @staticmethod
    def _point(x, y):
        if any(type(value) is not int or not 0 <= value <= CANVAS_SIZE for value in (x, y)):
            raise ValueError('Invalid stroke coordinate')
        return x, y

    def begin(self, x, y):
        point = self._point(x, y)
        if self.active is not None or len(self.strokes) >= MAX_STROKES:
            return False
        if sum(map(len, self.strokes)) >= MAX_POINTS:
            return False
        self.active = [point]
        self.revision += 1
        return True

    def append(self, x, y):
        point = self._point(x, y)
        if self.active is None or point == self.active[-1]:
            return False
        if len(self.active) == MAX_POINTS_PER_STROKE:
            # Preserve geometry and the latest endpoint within a fixed budget.
            self.active = self.active[::2]
        if sum(map(len, self.strokes)) + len(self.active) >= MAX_POINTS:
            return False
        self.active.append(point)
        self.revision += 1
        return True

    def finish(self):
        if self.active is None:
            return False
        self.strokes.append(tuple(self.active))
        self.active = None
        self.revision += 1
        return True

    def cancel(self):
        if self.active is not None:
            self.active = None
            self.revision += 1

    def undo(self):
        if self.active is not None:
            self.cancel()
        elif self.strokes:
            self.strokes.pop()
            self.revision += 1

    def clear(self):
        self.strokes.clear()
        self.active = None
        self.revision += 1

    def snapshot(self):
        return tuple(self.strokes)
