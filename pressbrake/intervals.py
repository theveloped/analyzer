"""
One-dimensional interval-set algebra used for the X-axis tooling envelopes.

An :class:`IntervalSet` is an immutable, normalized set of disjoint closed
intervals stored as a ``(N, 2)`` float array sorted by start.  Touching or
overlapping input intervals are merged on construction, empty and inverted
inputs are dropped, so all operations can assume the canonical form.
"""

import numpy as np


class IntervalSet:

    __slots__ = ("arr",)

    def __init__(self, pairs=None):
        self.arr = self._normalize(pairs)

    @staticmethod
    def _normalize(pairs):
        if pairs is None:
            return np.empty((0, 2), dtype=float)
        arr = np.asarray(pairs, dtype=float).reshape(-1, 2)
        arr = arr[arr[:, 1] > arr[:, 0]]
        if len(arr) == 0:
            return np.empty((0, 2), dtype=float)
        arr = arr[np.argsort(arr[:, 0], kind="stable")]
        merged = [arr[0].copy()]
        for start, end in arr[1:]:
            if start <= merged[-1][1]:
                merged[-1][1] = max(merged[-1][1], end)
            else:
                merged.append(np.array([start, end]))
        return np.array(merged, dtype=float)

    @classmethod
    def empty(cls):
        return cls()

    @classmethod
    def from_pairs(cls, pairs):
        return cls(pairs)

    def is_empty(self):
        return len(self.arr) == 0

    def measure(self):
        if self.is_empty():
            return 0.0
        return float(np.sum(self.arr[:, 1] - self.arr[:, 0]))

    def union(self, other):
        if self.is_empty():
            return IntervalSet(other.arr)
        if other.is_empty():
            return IntervalSet(self.arr)
        return IntervalSet(np.vstack([self.arr, other.arr]))

    def intersect(self, other):
        result = []
        a, b = self.arr, other.arr
        i = j = 0
        while i < len(a) and j < len(b):
            start = max(a[i, 0], b[j, 0])
            end = min(a[i, 1], b[j, 1])
            if end > start:
                result.append((start, end))
            if a[i, 1] < b[j, 1]:
                i += 1
            else:
                j += 1
        return IntervalSet(result)

    def complement(self, low, high):
        """
        The gaps of this set within [low, high].
        """
        result = []
        cursor = low
        for start, end in self.arr:
            if start > cursor:
                result.append((max(cursor, low), min(start, high)))
            cursor = max(cursor, end)
            if cursor >= high:
                break
        if cursor < high:
            result.append((cursor, high))
        return IntervalSet(result)

    def difference(self, other):
        if self.is_empty() or other.is_empty():
            return IntervalSet(self.arr)
        low = min(self.arr[0, 0], other.arr[0, 0]) - 1.0
        high = max(self.arr[-1, 1], other.arr[-1, 1]) + 1.0
        return self.intersect(other.complement(low, high))

    def buffer(self, margin):
        """
        Expand every interval by ``margin`` on both sides (negative shrinks).
        """
        if self.is_empty():
            return IntervalSet()
        return IntervalSet(self.arr + np.array([-margin, margin]))

    def translate(self, offset):
        if self.is_empty():
            return IntervalSet()
        return IntervalSet(self.arr + offset)

    def contains(self, other, tolerance=1e-9):
        """
        True when ``other`` is a subset of this set (within tolerance).
        """
        return other.difference(self.buffer(tolerance)).is_empty()

    def contains_point(self, x):
        if self.is_empty():
            return False
        index = np.searchsorted(self.arr[:, 0], x, side="right") - 1
        return index >= 0 and x <= self.arr[index, 1]

    def to_pairs(self):
        return [(float(start), float(end)) for start, end in self.arr]

    def __eq__(self, other):
        if not isinstance(other, IntervalSet):
            return NotImplemented
        return self.arr.shape == other.arr.shape and np.allclose(self.arr, other.arr)

    def __len__(self):
        return len(self.arr)

    def __iter__(self):
        return iter(self.to_pairs())

    def __repr__(self):
        pairs = ", ".join("[{:.3f}, {:.3f}]".format(a, b) for a, b in self.arr)
        return "IntervalSet({})".format(pairs)
