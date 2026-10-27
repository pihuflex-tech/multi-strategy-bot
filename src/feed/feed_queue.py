"""
feed_queue.py
Phase 6: Bounded Drop-Oldest Snapshot Queue for Market Data Ingestion.

Guarantees:
- Strictly bounded capacity at all times (default maxsize=5).
- Drops the oldest pending snapshot on overflow to preserve the freshest market state.
- Strictly validates object types: rejects non-MarketSnapshot items with TypeError.
- Thread-safe synchronization via threading.Lock and threading.Condition.
- Non-blocking enqueue for feed listener performance.
- Exposes metrics for accepted, evicted, and rejected snapshots.
- Standard-library-only implementation compatible with Android Termux and Windows.
"""

from collections import deque
import logging
import queue
import threading
import time
from typing import Any, Dict, List, Optional

from src.market.market_snapshot import MarketSnapshot

logger = logging.getLogger("DropOldestSnapshotQueue")


class DropOldestSnapshotQueue:
    """
    Thread-safe bounded FIFO queue with Drop-Oldest eviction policy.
    Maintains a maximum of `maxsize` MarketSnapshot items.
    """

    DEFAULT_MAXSIZE: int = 5

    def __init__(self, maxsize: int = DEFAULT_MAXSIZE):
        if not isinstance(maxsize, int) or isinstance(maxsize, bool) or maxsize <= 0:
            raise ValueError(f"maxsize must be a positive integer >= 1, got {maxsize!r}")

        self._maxsize: int = maxsize
        self._deque: deque = deque()
        self._lock = threading.Lock()
        self._not_empty = threading.Condition(self._lock)
        self._closed: bool = False

        # Diagnostic telemetry counters
        self._accepted_count: int = 0
        self._evicted_count: int = 0
        self._rejected_count: int = 0

    @property
    def maxsize(self) -> int:
        """Returns the configured maximum capacity of the queue."""
        return self._maxsize

    @property
    def accepted_count(self) -> int:
        """Total number of valid snapshots accepted and enqueued."""
        with self._lock:
            return self._accepted_count

    @property
    def evicted_count(self) -> int:
        """Total number of older snapshots evicted due to queue overflow."""
        with self._lock:
            return self._evicted_count

    @property
    def rejected_count(self) -> int:
        """Total number of items rejected due to invalid type or queue closure."""
        with self._lock:
            return self._rejected_count

    @property
    def is_closed(self) -> bool:
        """Returns True if the queue has been closed."""
        with self._lock:
            return self._closed

    def put_nowait(self, item: Any) -> Optional[MarketSnapshot]:
        """
        Enqueues a MarketSnapshot without blocking.
        If queue is at capacity, atomically discards the oldest snapshot to make room.

        Parameters:
            item: Must be an instance of MarketSnapshot.

        Returns:
            The evicted MarketSnapshot if an overflow eviction occurred, else None.

        Raises:
            TypeError: If item is not a MarketSnapshot.
            RuntimeError: If queue is closed.
        """
        return self.put(item, block=False)

    def put(self, item: Any, block: bool = False, timeout: Optional[float] = None) -> Optional[MarketSnapshot]:
        """
        Enqueues a MarketSnapshot with Drop-Oldest overflow policy.
        Since overflow evicts the oldest item, enqueue never needs to block.

        Parameters:
            item: Must be an instance of MarketSnapshot.
            block: Accepted for queue API compatibility; does not block.
            timeout: Accepted for queue API compatibility.

        Returns:
            The evicted MarketSnapshot if eviction occurred, else None.
        """
        if not isinstance(item, MarketSnapshot):
            with self._lock:
                self._rejected_count += 1
            raise TypeError(f"INVALID_ITEM_TYPE: Expected MarketSnapshot, got {type(item).__name__}")

        with self._lock:
            if self._closed:
                self._rejected_count += 1
                raise RuntimeError("QUEUE_CLOSED: Cannot put items into a closed queue.")

            evicted: Optional[MarketSnapshot] = None
            if len(self._deque) >= self._maxsize:
                evicted = self._deque.popleft()
                self._evicted_count += 1
                logger.debug(
                    "DropOldestQueue overflow: evicted oldest snapshot (timestamp=%s)",
                    getattr(evicted, "timestamp", "UNKNOWN")
                )

            self._deque.append(item)
            self._accepted_count += 1
            self._not_empty.notify()
            return evicted

    def get(self, block: bool = True, timeout: Optional[float] = None) -> MarketSnapshot:
        """
        Dequeues and returns the oldest pending MarketSnapshot.

        Parameters:
            block: If True, blocks until an item is available or timeout occurs.
            timeout: Maximum seconds to block. None means block indefinitely.

        Returns:
            MarketSnapshot: The oldest queued snapshot.

        Raises:
            queue.Empty: If queue is empty (or on timeout/closure).
        """
        with self._lock:
            if not block:
                if not self._deque:
                    raise queue.Empty
                return self._deque.popleft()

            if timeout is not None:
                if not isinstance(timeout, (int, float)) or isinstance(timeout, bool) or timeout < 0:
                    raise ValueError(f"timeout must be a non-negative number, got {timeout!r}")
                endtime = time.monotonic() + timeout

            while not self._deque:
                if self._closed:
                    raise queue.Empty
                if timeout is None:
                    self._not_empty.wait()
                else:
                    remaining = endtime - time.monotonic()
                    if remaining <= 0.0:
                        raise queue.Empty
                    self._not_empty.wait(remaining)

            return self._deque.popleft()

    def get_nowait(self) -> MarketSnapshot:
        """
        Dequeues an item without blocking.
        Equivalent to get(block=False).
        """
        return self.get(block=False)

    def qsize(self) -> int:
        """Returns the current number of queued snapshots."""
        with self._lock:
            return len(self._deque)

    def empty(self) -> bool:
        """Returns True if the queue is empty."""
        with self._lock:
            return len(self._deque) == 0

    def full(self) -> bool:
        """Returns True if the queue is currently at maximum capacity."""
        with self._lock:
            return len(self._deque) >= self._maxsize

    def clear(self) -> List[MarketSnapshot]:
        """
        Atomically drains and returns all pending snapshots from the queue.
        Useful for safe shutdown or discarding stale backlog.
        """
        with self._lock:
            items = list(self._deque)
            self._deque.clear()
            return items

    def close(self) -> None:
        """
        Closes the queue and unblocks any waiting get() callers.
        """
        with self._lock:
            self._closed = True
            self._not_empty.notify_all()

    def to_dict(self) -> Dict[str, Any]:
        """Returns diagnostic telemetry dictionary."""
        with self._lock:
            return {
                "maxsize": self._maxsize,
                "qsize": len(self._deque),
                "accepted_count": self._accepted_count,
                "evicted_count": self._evicted_count,
                "rejected_count": self._rejected_count,
                "is_closed": self._closed,
            }
