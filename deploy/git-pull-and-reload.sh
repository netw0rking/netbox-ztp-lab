#!/bin/sh
# Run by cron every 5 minutes: pull, reinstall if deps changed, reload.
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
