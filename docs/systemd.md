# Running `seedlink-py-archiver` under systemd

The archiver is designed to run for months unattended. Process-level auto-
restart (hard kernel kills, OOM, segfaults, `--exit-on-all-stale` triggers)
is out of scope for the archiver itself — systemd already does this better
than anything we'd ship. This page gives a working unit file and a couple
of notes on how it interacts with the in-process stale-stream watchdog.

## Minimal unit

Save as `/etc/systemd/system/seedlink-archiver.service`:

```ini
[Unit]
Description=SeedLink-to-SDS archiver (seedlink-py-archiver)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=slarchiver
Group=slarchiver

EnvironmentFile=/etc/slarchiver/env
# env file contents (mode 0600, owned by slarchiver):
#   SLACK_WEBHOOK_URL=https://hooks.slack.com/services/...

ExecStart=/usr/local/bin/seedlink-py-archiver \
    IU.ANMO.00.BH? \
    --archive /data/sds \
    --state-file /var/lib/slarchiver/state.txt \
    --log-file /var/log/slarchiver/archiver.log \
    --monitor \
    --stale-timeout 300 \
    --webhook ${SLACK_WEBHOOK_URL} \
    --exit-on-all-stale

Restart=always
RestartSec=30

# Clean-ish shutdown. The archiver saves state on SIGINT/SIGTERM.
KillSignal=SIGINT
TimeoutStopSec=30

# Sandboxing — tighten as you like.
ProtectSystem=strict
ReadWritePaths=/data/sds /var/lib/slarchiver /var/log/slarchiver
ProtectHome=true
PrivateTmp=true
NoNewPrivileges=true

[Install]
WantedBy=multi-user.target
```

Then:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now seedlink-archiver
sudo systemctl status seedlink-archiver
journalctl -u seedlink-archiver -f
```

## Choosing the restart policy

Two reasonable choices:

- **`Restart=always`** — systemd restarts the archiver on any exit, clean or
  not. Good default: the archiver is long-running and should effectively
  never stop on its own.
- **`Restart=on-failure`** — only restart on non-zero exit. Pairs naturally
  with `--exit-on-all-stale`, which exits with status `2` when every
  registered NSLC has gone silent. A clean Ctrl-C (status `0`) won't
  trigger a restart.

For most deployments `Restart=always` is simpler. Switch to `on-failure` only
if you want operator Ctrl-C to mean "stay down."

## `--exit-on-all-stale` + systemd loop

If every channel is dead — server unreachable, account revoked, network
partition on the archiver side — the archiver with `--exit-on-all-stale`
will exit with status 2, systemd will wait `RestartSec` and restart it,
and the new process will probably also fail. Two things make that tolerable:

1. **The webhook still fires.** Before the process exits, the watcher sends
   a `STALE` alert for each transition and a log line explaining the
   all-stale exit. You'll see the Slack messages even if the whole feed
   dies.
2. **`RestartSec=30`** keeps the restart cadence low enough not to DoS the
   SeedLink server.

If you want to avoid restart storms, set `StartLimitIntervalSec=600
StartLimitBurst=5` in the `[Unit]` section — systemd will stop retrying
after 5 failures in 10 minutes and put the unit into a failed state. You'll
see that in `systemctl status` and can investigate.

## Log management

The archiver writes a rotating log via `--log-file`; systemd also captures
stdout/stderr into the journal. Either is fine to watch; pick one:

- **File:** `tail -F /var/log/slarchiver/archiver.log` — rotation is handled
  in-process (10 MB × 5 backups).
- **Journal:** `journalctl -u seedlink-archiver -f` — rotation is handled by
  journald. Drop `--log-file` if you prefer journald-only.

## Verifying the setup end-to-end

1. Start the unit, confirm it's running: `systemctl status seedlink-archiver`.
2. Watch the log for "stale-watcher started: ..." and then the per-NSLC
   "first packet: ..." lines as data flows in.
3. Simulate a stream death by picking an NSLC that's always dead on your
   server (or drop `--stale-timeout` very low temporarily, e.g. `30`) and
   watch for the Slack alert.
4. Stop the unit: `systemctl stop seedlink-archiver`. Confirm the log has
   "Interrupted by user. Saving state and exiting." — that means
   `KillSignal=SIGINT` reached the process and state was saved.
5. Re-start: the "Recovered state from ..." log line should appear on
   startup and the server should replay anything it still has buffered.
