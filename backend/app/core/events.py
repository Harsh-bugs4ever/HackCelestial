"""A thread-to-event-loop nudge for the live dashboard.

The action watcher used to wake every four seconds, open a session and diff the
pending-card set, whether or not anything had happened and whether or not
anyone was connected - about 21,600 pointless queries a day on an instance that
is also paying for keep-alive pings. Worse, it made the "real-time" WebSocket
up to four seconds late.

The writers already know when something changed. This lets them say so, from a
worker thread, to the loop running the watcher: updates become immediate and
the idle poll can drop to a slow safety net.

The poll is kept, not replaced. A nudge can be missed - another process, a
Celery worker, a signal raised while the loop was busy - and a missed card in
an operations queue is not acceptable. Nudges make it fast; the poll makes it
certain.
"""
from __future__ import annotations

import asyncio
import logging

log = logging.getLogger(__name__)

_loop: asyncio.AbstractEventLoop | None = None
_wake: asyncio.Event | None = None


def bind(loop: asyncio.AbstractEventLoop, wake: asyncio.Event) -> None:
    """Called once at startup by the watcher that owns the event."""
    global _loop, _wake
    _loop, _wake = loop, wake


def unbind() -> None:
    global _loop, _wake
    _loop, _wake = None, None


def nudge() -> None:
    """Ask the watcher to look now. Safe from any thread, and a no-op if
    nothing is listening - Celery workers and the test suite have no loop."""
    loop, wake = _loop, _wake
    if loop is None or wake is None or loop.is_closed():
        return
    try:
        loop.call_soon_threadsafe(wake.set)
    except RuntimeError:
        # The loop shut down between the check and the call; the next poll
        # covers it.
        pass
