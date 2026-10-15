"""Keeps a free-tier host from putting the service to sleep.

Render's free web services are suspended after ~15 minutes without inbound traffic, and the detection scheduler lives
inside the process, so a sleeping service also means no monitoring and no alerts. While the service is awake, this pings
its own public URL (which counts as inbound traffic). It cannot wake a service that is already asleep: for that, also point
a free external monitor (UptimeRobot, cron-job.org) at /healthz. Disabled when no public URL is known or KEEPALIVE_MINUTES=0.
"""
import logging
import threading

import httpx

logger = logging.getLogger("business_reality_engine")
_stop = threading.Event()
_thread: threading.Thread | None = None


def _loop(url: str, minutes: int):
    target = url.rstrip("/") + "/healthz"
    while not _stop.wait(minutes * 60):
        try:
            httpx.get(target, timeout=20, follow_redirects=True)
        except Exception as e:  # never let the pinger die
            logger.warning(f"Keep-alive ping failed: {type(e).__name__}")


def start_keepalive(url: str, minutes: int):
    global _thread
    if not url or minutes <= 0 or _thread is not None:
        return
    _stop.clear()
    _thread = threading.Thread(target=_loop, args=(url, minutes), daemon=True, name="keepalive")
    _thread.start()
    logger.info(f"Keep-alive: pinging {url} every {minutes} min")


def stop_keepalive():
    global _thread
    _stop.set()
    _thread = None
