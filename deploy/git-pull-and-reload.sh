#!/bin/sh
# Run every 5 minutes by cron (see deploy/git-pull.cron). A plain
# `git pull` alone keeps the editable install (Dockerfile's own
# `uv pip install --system --no-cache -e .`) up to date on disk for free -
# but the already-running `ztp start` process (deploy/entrypoint.sh) still
# holds the old code in memory regardless, the same way any long-running
# Python process does. This script closes that gap: if the pull actually
# moved HEAD, and pyproject.toml/uv.lock changed, reinstall (a real
# dependency/entry-point change, per the Dockerfile's own comment); then
# always `ztp rebuild` - a SIGHUP to PID 1, which _supervise() (ztp_cli/
# cli.py) handles by cleanly killing and respawning just the FastAPI
# child, in place, no container exit. No Docker image rebuild needed for
# a code-only change.
set -e

cd /app
before="$(git rev-parse HEAD)"
git pull >> /var/log/git-pull.log 2>&1
after="$(git rev-parse HEAD)"

if [ "$before" != "$after" ]; then
    echo "$(date -Iseconds) code changed ($before -> $after)" >> /var/log/git-pull.log
    if git diff --name-only "$before" "$after" | grep -qE "^(pyproject\.toml|uv\.lock)$"; then
        echo "$(date -Iseconds) pyproject.toml/uv.lock changed, reinstalling" >> /var/log/git-pull.log
        uv pip install --system --no-cache -e . >> /var/log/git-pull.log 2>&1
    fi
    ztp rebuild >> /var/log/git-pull.log 2>&1
fi
