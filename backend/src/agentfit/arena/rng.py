"""A random generator that gives the same numbers on every Python version and every machine.

The standard ``random`` module promises the same stream for ``random()`` only; ``randint``, ``shuffle`` and
``sample`` may change between versions. A benchmark whose questions depend on a seed cannot allow that: the
same seed has to give the same questions next year. This is splitmix64 started from a SHA-256 of the seed and
a key (the question's id), with integer-only helpers on top.
"""

from __future__ import annotations

import hashlib
from collections.abc import MutableSequence, Sequence
from typing import TypeVar

T = TypeVar("T")
_MASK = (1 << 64) - 1
_GOLDEN = 0x9E3779B97F4A7C15


class Rng:
    def __init__(self, seed: int | str, key: str = "") -> None:
        digest = hashlib.sha256(f"{seed}:{key}".encode()).digest()
        self._state = int.from_bytes(digest[:8], "little")

    @classmethod
    def from_state(cls, state: int) -> Rng:
        rng = cls(0)
        rng._state = state & _MASK
        return rng

    def next_u64(self) -> int:
        self._state = (self._state + _GOLDEN) & _MASK
        z = self._state
        z = ((z ^ (z >> 30)) * 0xBF58476D1CE4E5B9) & _MASK
        z = ((z ^ (z >> 27)) * 0x94D049BB133111EB) & _MASK
        return z ^ (z >> 31)

    def random(self) -> float:
        """A float in [0, 1) with 53 random bits."""
        return (self.next_u64() >> 11) / (1 << 53)

    def below(self, n: int) -> int:
        """An integer in [0, n) without modulo bias."""
        if n <= 0:
            raise ValueError("n must be positive")
        limit = (1 << 64) - ((1 << 64) % n)
        while True:
            value = self.next_u64()
            if value < limit:
                return value % n

    def randint(self, a: int, b: int) -> int:
        """An integer in [a, b], both ends included."""
        if b < a:
            raise ValueError("empty range")
        return a + self.below(b - a + 1)

    def uniform(self, a: float, b: float) -> float:
        return a + (b - a) * self.random()

    def choice(self, seq: Sequence[T]) -> T:
        if not seq:
            raise IndexError("cannot choose from an empty sequence")
        return seq[self.below(len(seq))]

    def shuffle(self, items: MutableSequence[T]) -> None:
        """In place, Fisher-Yates from the end."""
        for i in range(len(items) - 1, 0, -1):
            j = self.below(i + 1)
            items[i], items[j] = items[j], items[i]

    def sample(self, seq: Sequence[T], k: int) -> list[T]:
        pool = list(seq)
        if k > len(pool):
            raise ValueError("sample larger than the population")
        for i in range(k):
            j = i + self.below(len(pool) - i)
            pool[i], pool[j] = pool[j], pool[i]
        return pool[:k]
