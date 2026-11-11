# SSH-enabled FRR image. Pass the upstream image as FRR_BASE.
ARG FRR_BASE
FROM ${FRR_BASE}

RUN apk add --no-cache openssh-server
# Disable root login; keep password auth for password-type users.
RUN sed -i '1i PermitRootLogin no\nPasswordAuthentication yes' /etc/ssh/sshd_config
COPY start.sh /usr/local/bin/start.sh
RUN chmod 755 /usr/local/bin/start.sh

# Upstream ENTRYPOINT is kept; only CMD changes.
CMD ["/usr/local/bin/start.sh"]
