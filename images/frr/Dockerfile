# SSH-enabled FRR image for every FRR node in both labs.
# Upstream FRR images ship the routing daemons only, no sshd. Built once
# per FRR version in use (FRR_BASE); generate_topology.py's FRR_SSH_IMAGES
# maps each upstream image to its tag here, so NetBox's vendor_image
# still decides the FRR version.
#
#   docker build --build-arg FRR_BASE=quay.io/frrouting/frr:10.5.1 -t netbox-ztp-lab-frr:10.5.1 images/frr
#   docker build --build-arg FRR_BASE=frrouting/frr:latest        -t netbox-ztp-lab-frr:8.4.1  images/frr
ARG FRR_BASE
FROM ${FRR_BASE}

RUN apk add --no-cache openssh-server
# sshd_config: first value wins, so prepend. No root logins; password
# auth stays on for users.yaml's password-type users (e.g. netautobot).
RUN sed -i '1i PermitRootLogin no\nPasswordAuthentication yes' /etc/ssh/sshd_config
COPY start.sh /usr/local/bin/start.sh
RUN chmod 755 /usr/local/bin/start.sh

# Upstream's ENTRYPOINT (/sbin/tini --) is kept; only CMD changes.
CMD ["/usr/local/bin/start.sh"]
