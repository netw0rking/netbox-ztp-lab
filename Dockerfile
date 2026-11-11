FROM python:3.12-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends git cron dnsmasq openssh-client openssh-server iproute2 iputils-ping \
    && rm -rf /var/lib/apt/lists/*

# uv, not pip. Copied from Astral's own distroless image rather than
# curl|sh'd (no curl in this image, and this avoids one) - the
# documented way to get the uv/uvx binaries into another image.
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /usr/local/bin/

WORKDIR /app

COPY . .

# Editable install: `git pull` (deploy/git-pull.cron) alone keeps this
# up to date afterward for pure code/data changes - editable mode
# points straight back at /app's own files rather than a frozen copy,
# so nothing needs reinstalling on every pull, only on a real dependency/
# entry-point change (a fresh `docker build`, i.e. a new image).
RUN uv pip install --system --no-cache -e .

# Operator SSH access to ztp-server itself, key-only (see
# deploy/sshd_local.conf and deploy/entrypoint.sh for the authorized_keys
# setup, sourced from the same admin_ssh_key.pub bind used for cEOS
# device logins).
COPY deploy/sshd_local.conf /etc/ssh/sshd_config.d/local.conf

COPY deploy/git-pull.cron /etc/cron.d/git-pull-cron
RUN chmod 0644 /etc/cron.d/git-pull-cron && crontab /etc/cron.d/git-pull-cron

RUN chmod +x deploy/entrypoint.sh deploy/git-pull-and-reload.sh

ENTRYPOINT ["deploy/entrypoint.sh"]
