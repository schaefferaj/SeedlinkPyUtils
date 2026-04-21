"""Shared alert-delivery helpers.

Used by the archiver's :class:`~seedlink_py_utils.monitor.StaleWatcher` and
the dashboard's :class:`~seedlink_py_utils.dashboard.DashboardAlerter`.
Webhook payloads are Slack-compatible (``text`` field renders in Slack;
extra structured fields are ignored by Slack but useful for generic
consumers like PagerDuty or Grafana).

Webhook failures log at WARNING and never raise — broken alerting must
not kill a long-running archiver or dashboard.
"""

from __future__ import annotations

import json
import logging
import socket
from typing import Optional
from urllib import error as urllib_error
from urllib import request as urllib_request


log = logging.getLogger("seedlink_py_utils.alerts")


def resolve_hostname(hostname: Optional[str] = None) -> str:
    """Return *hostname* if given, else the machine's FQDN."""
    return hostname or socket.gethostname()


def post_webhook(
    url: str,
    *,
    text: str,
    event: str,
    hostname: str,
    timeout: float = 10.0,
    color: Optional[str] = None,
    **fields,
) -> None:
    """POST a Slack-compatible JSON body to *url*.

    Parameters
    ----------
    url : str
        Incoming-webhook URL.
    text : str
        Human-readable message (rendered by Slack).
    event : str
        Machine-readable event type (``"stale"``, ``"recovered"``).
    hostname : str
        Source label for the alert.
    timeout : float
        HTTP request timeout in seconds.
    color : str, optional
        Hex colour for the Slack attachment sidebar (e.g. ``"#cc0000"``).
        When set, the payload uses Slack's ``attachments`` format which
        renders a coloured vertical bar beside the message. When None,
        the payload uses a plain ``text`` body (no colour).
    **fields
        Extra key/value pairs merged into the JSON body (e.g.
        ``nslc="CN.PGC..HHZ"``, ``age_seconds=412.3``).
    """
    meta = {"event": event, "hostname": hostname, **fields}
    if color:
        payload = {"attachments": [{"color": color, "text": text, **meta}]}
    else:
        payload = {"text": text, **meta}
    body = json.dumps(payload).encode("utf-8")
    req = urllib_request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib_request.urlopen(req, timeout=timeout):
            pass
    except urllib_error.URLError as e:
        log.warning("webhook POST failed (%s %s): %s", event,
                    fields.get("nslc", ""), e)
    except Exception as e:
        log.warning("webhook POST failed (%s %s): %s", event,
                    fields.get("nslc", ""), e)
