"""Self-ping loop that stops a free-tier instance from sleeping.

Render's free plan spins an instance down after roughly 15 minutes with no
inbound request, and waking it again costs about 50 seconds - so the first
visitor after a quiet spell waits out a cold boot before the dashboard paints.

A request the instance makes to its own public URL leaves the container and
comes back through Render's router, which is inbound traffic as far as the
idle timer is concerned. Hitting ``/health`` well inside the timeout therefore
keeps the instance warm at the cost of one tiny request every few minutes.

The loop stays dormant unless a target URL is available: ``KEEPALIVE_URL``, or
``RENDER_EXTERNAL_URL``, which Render injects into every service. Local runs
set neither, so nothing pings. ``KEEPALIVE_ENABLED=false`` turns it off even
where a URL exists.
"""
from __future__ import annotations

import asyncio
import logging
import random

log = logging.getLogger("resort360.keepalive")

# Render wakes an instance on any inbound request, so a failed ping is only a
# missed opportunity - never a reason to take the loop (or the app) down.
_TIMEOUT_SECONDS = 30.0


async def ping_forever(url: str, interval: int) -> None:
    """Ping ``url`` every ``interval`` seconds until cancelled."""
    import httpx

    log.info("keep-alive: pinging %s every %ds", url, interval)
    headers = {"user-agent": "resort360-keepalive/1.0"}
    async with httpx.AsyncClient(timeout=_TIMEOUT_SECONDS, follow_redirects=True) as client:
        while True:
            # Sleep first: the instance is awake right now, having just booted.
            # The jitter keeps the self-ping from landing on the same second as
            # the GitHub Actions cron that pings from outside.
            await asyncio.sleep(interval + random.uniform(0, 30))
            try:
                response = await client.get(url, headers=headers)
                log.debug("keep-alive: %s -> %s", url, response.status_code)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                # A dropped ping just means this window went unclaimed.
                log.warning("keep-alive: ping failed (%s)", exc)
