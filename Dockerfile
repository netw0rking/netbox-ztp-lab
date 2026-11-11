FROM python:3.12-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends git cron dnsmasq openssh-client openssh-server iproute2 iputils-ping \
    && rm -rf /var/lib/apt/lists/*

# uv binaries from Astral's image.
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /usr/local/bin/

WORKDIR /app

COPY . .

# Editable install, so git pull updates code without a reinstall.
RUN uv pip install --system --no-cache -e .

# Operator SSH config, key-only.
COPY deploy/sshd_local.conf /etc/ssh/sshd_config.d/local.conf

COPY deploy/git-pull.cron /etc/cron.d/git-pull-cron
RUN chmod 0644 /etc/cron.d/git-pull-cron && crontab /etc/cron.d/git-pull-cron

RUN chmod +x deploy/entrypoint.sh deploy/git-pull-and-reload.sh

ENTRYPOINT ["deploy/entrypoint.sh"]
