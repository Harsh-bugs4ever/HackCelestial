"""Bounded process-local model cache with one computation per key at a time."""
from collections import OrderedDict
from concurrent.futures import Future
from threading import Lock
from time import monotonic
from typing import Callable, Generic, Hashable, TypeVar

T = TypeVar("T")


class ModelCache(Generic[T]):
    def __init__(self, maxsize: int, ttl: float, clock=monotonic):
        self.maxsize, self.ttl, self.clock = maxsize, ttl, clock
        self._values: OrderedDict[Hashable, tuple[float, T]] = OrderedDict()
        self._pending: dict[Hashable, Future] = {}
        self._lock = Lock()
        self._generation = 0

    def clear(self) -> None:
        with self._lock:
            self._values.clear()
            self._generation += 1
            self._pending.clear()

    def get(self, key: Hashable, compute: Callable[[], T]) -> T:
        with self._lock:
            cached = self._values.get(key)
            if cached is not None:
                expires, value = cached
                if expires > self.clock():
                    self._values.move_to_end(key)
                    return value
                del self._values[key]
            future = self._pending.get(key)
            owner = future is None
            if owner:
                future = Future()
                self._pending[key] = future
            generation = self._generation
        if not owner:
            return future.result()
        try:
            value = compute()
            with self._lock:
                if generation == self._generation:
                    self._values[key] = (self.clock() + self.ttl, value)
                    while len(self._values) > self.maxsize:
                        self._values.popitem(last=False)
            future.set_result(value)
            return value
        except BaseException as exc:
            future.set_exception(exc)
            raise
        finally:
            with self._lock:
                if self._pending.get(key) is future:
                    del self._pending[key]
