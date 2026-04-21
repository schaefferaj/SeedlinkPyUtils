# Slack webhook setup

Both `seedlink-py-archiver --monitor` and `seedlink-py-dashboard --alert` can
post alerts to a Slack channel via an **incoming webhook**. Each sends a
plain JSON body like

```json
{
  "text": "[host.example.com] STALE: CN.PGC..HHZ — no data for 412s (threshold 300s)",
  "event": "stale",
  "nslc": "CN.PGC..HHZ",
  "age_seconds": 412.3,
  "threshold_seconds": 300.0,
  "hostname": "host.example.com"
}
```

Slack renders `text` in the channel and ignores the structured fields. Any
other HTTP endpoint that accepts JSON POSTs will also work — the extra fields
are there for consumers that want to parse them (PagerDuty, Grafana Alerting,
a custom service, etc.).

## Create an incoming webhook

1. Open <https://api.slack.com/apps> and click **Create New App → From scratch**.
   Name it something obvious (e.g. `SeedLink archiver alerts`) and pick the
   workspace where the alert channel lives.
2. In the left sidebar go to **Incoming Webhooks** and toggle **Activate
   Incoming Webhooks** on.
3. Click **Add New Webhook to Workspace**. Slack will ask which channel to
   post to — pick the channel (or a private DM) and approve.
4. Copy the webhook URL it gives you. It will start with
   `https://hooks.slack.com/services/` followed by three slash-separated tokens.

Treat that URL like a secret — anyone who has it can post to your channel.

## Wire it into the archiver

Pass the URL to `--webhook`:

```bash
seedlink-py-archiver CN.PGC..HH? \
    --archive /data/sds \
    --state-file /var/lib/slarchiver/state.txt \
    --log-file /var/log/slarchiver.log \
    --monitor \
    --stale-timeout 300 \
    --webhook "$SLACK_WEBHOOK_URL"
```

## Wire it into the dashboard

```bash
seedlink-py-dashboard --network CN --alert --webhook "$SLACK_WEBHOOK_URL"
```

The dashboard fires a webhook when an NSLC transitions to **STALE** or
recovers from STALE. LAG <-> OK transitions are logged at INFO only —
they're a watch state, not pager-worthy.

Keep the URL out of shell history and out of the repo. Options:

- Read it from a file only root/the service user can see, e.g.
  `--webhook "$(cat /etc/slarchiver/webhook.url)"`.
- Put it in a systemd `EnvironmentFile=` (see `docs/systemd.md`) that's
  mode 0600 and owned by the service user.

## Test the webhook before you rely on it

A 1-liner from the box that will run the archiver:

```bash
curl -X POST -H 'Content-Type: application/json' \
     -d '{"text":"test from $(hostname)"}' \
     "$SLACK_WEBHOOK_URL"
```

If you don't see the message in the channel within a few seconds, the
webhook URL is wrong, revoked, or the box can't reach `hooks.slack.com`
(check egress firewall rules).

## What the tools will send

### Archiver (`--monitor`)

Per-NSLC alerts:

- `event: "stale"` — no packets for `--stale-timeout` seconds. Fires once
  on the `HEALTHY → STALE` transition, not every tick.
- `event: "recovered"` — a previously-STALE NSLC is flowing again.

First-packet events (UNKNOWN → HEALTHY) are logged at INFO only.

### Dashboard (`--alert`)

**Station-level** alerts (aggregated across all channels of a NET.STA):

- `event: "degraded"` — station status worsened (OK → LAG, LAG → STALE,
  or OK → STALE). Message includes all channels with their individual
  status and latency:

  ```
  [host] IU.ANMO: OK → LAG
    00.BHE  5.0s (OK)
    00.BHN  5.0s (OK)
    00.BHZ  5.0s (OK)
    00.LHZ  1.5m (LAG)
  ```

- `event: "improved"` — station status recovered (STALE → LAG, LAG → OK,
  or STALE → OK). Same channel-detail format.

Station status is the **worst** of its channels (STALE > LAG > UNKNOWN > OK).
First-sighting polls establish a baseline only — no webhook.

### Both tools

The two tools answer different questions:
- **Archiver `--monitor`**: "Is **my archiver** receiving packets?"
  (ground truth from the packet handler, per-NSLC).
- **Dashboard `--alert`**: "Is **the server** receiving packets?"
  (from the server's `INFO=STREAMS` `end_time`, per-station).

Both are useful — run the archiver monitor on boxes that archive, run
the dashboard alerter as a fleet-wide watcher.

Webhook failures (timeout, non-2xx, DNS error) are logged at WARNING and
never kill the running process — a broken webhook never loses seismic data.

## Reducing noise

- Raise `--stale-timeout` if you get false-positives during normal server
  hiccups. 300 s is fine for most broadband channels but may be tight for
  low-rate sensors.
- Create a dedicated low-priority Slack channel for these alerts and
  mute/unmute notifications there — keeps the signal without spamming
  your primary channels.
