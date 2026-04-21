"""Stale-stream watchdog for the SeedLink archiver.

Tracks per-NSLC packet arrival times in the archiver's packet handler, runs a
daemon thread that classifies each channel as ``HEALTHY`` / ``STALE`` /
``UNKNOWN``, and emits alerts on state transitions. Alerts fan out to the
standard logger (always) and to an optional Slack-compatible webhook.

Hysteresis is per-NSLC: the watcher alerts once on ``HEALTHY -> STALE`` and
once on ``STALE -> HEALTHY``, not on every check tick. First-packet transitions
(``UNKNOWN -> HEALTHY``) log at INFO but do not send a webhook — that's a
startup event, not an alert.

Process-level auto-restart is intentionally out of scope: let systemd /
supervisord / docker handle that. See ``docs/systemd.md`` for an example unit.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional

from .alerts import post_webhook, resolve_hostname


log = logging.getLogger("seedlink_py_utils.monitor")


HEALTHY = "HEALTHY"
STALE = "STALE"
UNKNOWN = "UNKNOWN"


@dataclass
class MonitorConfig:
    """Configuration for :class:`StaleWatcher`.

    Attributes
    ----------
    stale_timeout : float
        Seconds without a packet before an NSLC is classified STALE.
    check_interval : float
        Seconds between watcher ticks. Must be less than ``stale_timeout`` or
        the watcher will miss transitions.
    webhook_url : str, optional
        Slack-compatible incoming-webhook URL. Receives JSON bodies of the
        form ``{"text": "...", "event": "...", "nslc": "...", ...}``. Slack
        ignores unknown fields, so the same URL works for generic consumers
        that want the structured metadata.
    webhook_timeout : float
        Per-request timeout for the webhook POST. The watcher thread does not
        block the packet handler, so a slow webhook only delays future checks.
    exit_on_all_stale : bool
        If True, signal the main loop to exit with a non-zero status when
        every registered NSLC is STALE. Intended to pair with a systemd
        ``Restart=on-failure`` policy.
    hostname : str, optional
        Label used in alert text. Defaults to ``socket.gethostname()``.
    """

    stale_timeout: float = 300.0
    check_interval: float = 60.0
    webhook_url: Optional[str] = None
    webhook_timeout: float = 10.0
    exit_on_all_stale: bool = False
    hostname: Optional[str] = None


class StaleWatcher:
    """Watch per-NSLC packet arrivals and emit transition alerts.

    Thread-safety: ``record_packet`` is called from the SeedLink packet
    handler thread; ``_tick`` runs on the watcher's own daemon thread. Both
    hold ``self._lock`` while reading/writing the shared dicts. Webhook POSTs
    run outside the lock so a slow endpoint can't back up the handler.
    """

    def __init__(
        self,
        cfg: MonitorConfig,
        expected_nslcs: Optional[List[str]] = None,
        on_all_stale: Optional[Callable[[], None]] = None,
    ):
        self.cfg = cfg
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._last_seen: Dict[str, float] = {}
        self._status: Dict[str, str] = {}
        self._start_time = time.time()
        self._on_all_stale = on_all_stale
        self._all_stale_fired = False
        self._hostname = resolve_hostname(cfg.hostname)

        for nslc in expected_nslcs or []:
            self.register(nslc)

    # ---- public API ---------------------------------------------------

    def register(self, nslc: str) -> None:
        """Declare an NSLC we expect to receive. Status starts UNKNOWN and
        transitions to HEALTHY on the first packet or STALE after
        ``stale_timeout`` seconds have elapsed with no packet."""
        with self._lock:
            self._status.setdefault(nslc, UNKNOWN)

    def record_packet(self, nslc: str) -> None:
        """Record that a packet for ``nslc`` arrived now."""
        now = time.time()
        transition = None
        with self._lock:
            self._last_seen[nslc] = now
            prev = self._status.get(nslc, UNKNOWN)
            if prev != HEALTHY:
                self._status[nslc] = HEALTHY
                transition = prev  # remember what we transitioned FROM
                self._all_stale_fired = False
        if transition is not None:
            self._on_healthy(nslc, from_status=transition)

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run, name="stale-watcher", daemon=True
        )
        self._thread.start()
        log.info(
            f"stale-watcher started: threshold={self.cfg.stale_timeout:.0f}s, "
            f"interval={self.cfg.check_interval:.0f}s, "
            f"webhook={'yes' if self.cfg.webhook_url else 'no'}, "
            f"watching {len(self._status)} NSLC(s)"
        )

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5.0)
            self._thread = None

    def snapshot(self) -> Dict[str, str]:
        """Return a copy of the current NSLC -> status map (for tests/CLI)."""
        with self._lock:
            return dict(self._status)

    # ---- internal -----------------------------------------------------

    def _run(self) -> None:
        while not self._stop.is_set():
            # Event.wait returns True when the event is set (stop requested);
            # returns False on timeout. We only tick on timeout.
            if self._stop.wait(self.cfg.check_interval):
                break
            try:
                self._tick()
            except Exception as e:
                # The watcher is defensive: a bug here must not kill the
                # archiver. Log and keep going.
                log.exception(f"stale-watcher tick failed: {e}")

    def _tick(self) -> None:
        now = time.time()
        transitions = []  # (nslc, age) — collected under lock, alerted outside
        all_stale = True
        any_registered = False
        with self._lock:
            for nslc, status in self._status.items():
                any_registered = True
                # Never-seen channels are judged against process start time,
                # so we don't alert before stale_timeout has even elapsed.
                last = self._last_seen.get(nslc, self._start_time)
                age = now - last
                if age > self.cfg.stale_timeout:
                    if status != STALE:
                        self._status[nslc] = STALE
                        transitions.append((nslc, age))
                else:
                    all_stale = False
            fire_all_stale = (
                self.cfg.exit_on_all_stale
                and any_registered
                and all_stale
                and not self._all_stale_fired
            )
            if fire_all_stale:
                self._all_stale_fired = True

        for nslc, age in transitions:
            self._on_stale(nslc, age)

        if fire_all_stale and self._on_all_stale:
            log.error(
                "All registered NSLCs are STALE; triggering exit-on-all-stale."
            )
            try:
                self._on_all_stale()
            except Exception as e:
                log.exception(f"on_all_stale callback failed: {e}")

    # ---- alert emitters ----------------------------------------------

    def _on_stale(self, nslc: str, age: float) -> None:
        text = (
            f"[{self._hostname}] STALE: {nslc} — no data for {age:.0f}s "
            f"(threshold {self.cfg.stale_timeout:.0f}s)"
        )
        log.warning(text)
        self._post_webhook(
            text=text,
            event="stale",
            nslc=nslc,
            age_seconds=round(age, 1),
            threshold_seconds=self.cfg.stale_timeout,
        )

    def _on_healthy(self, nslc: str, from_status: str) -> None:
        if from_status == UNKNOWN:
            log.info(f"[{self._hostname}] first packet: {nslc}")
            # Don't webhook on the very first packet — it's a startup event,
            # not an alert-worthy transition.
            return
        text = f"[{self._hostname}] RECOVERED: {nslc} — data flowing again"
        log.info(text)
        self._post_webhook(text=text, event="recovered", nslc=nslc)

    def _post_webhook(self, *, text: str, event: str, **fields) -> None:
        if not self.cfg.webhook_url:
            return
        post_webhook(
            self.cfg.webhook_url,
            text=text,
            event=event,
            hostname=self._hostname,
            timeout=self.cfg.webhook_timeout,
            **fields,
        )
