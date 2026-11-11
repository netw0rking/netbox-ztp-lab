# netbox-ztp-lab

A containerlab network lab where NetBox is the source of truth and every
device builds itself through zero-touch provisioning (ZTP).

You describe the network in NetBox: sites, devices, cables, IPs, ASNs. A
script exports that to JSON, CI validates it, and a small FastAPI server
hands each device its rendered config when it boots.

## What's in the lab

**us-east-01a: Clos fabric with ZTP**
- 2x Nokia SR Linux spines
- 4x Arista cEOS leafs + 3x FRR leafs
- VXLAN/EVPN overlay: L2 VNI between cEOS and FRR (leaf-01/02), plus routed EVPN Type-5 with symmetric IRB (leaf-06/07)
- Linux test hosts behind the leafs
- Real DHCP-based ZTP: dnsmasq hands out the lease and ZTP options, the device pulls its bootstrap script and config

**backbone-fabric: multi-site backbone**
- 10 POP sites in a ladder topology, one backbone router (BBR) each
- 2 fabric sites (us-central-01a, us-west-01a), each with a spine, 2 edge leafs, and an EVPN route reflector
- IS-IS underlay with SR-MPLS prefix-SIDs
- iBGP AS 65000, route reflectors on bbr-03 and bbr-08
- Data center interconnect: VRF `DCI`, L3VNI 50000, EVPN Type-5 between sites
- One ZTP server per site

Everything runs on FRR, SR Linux, and cEOS containers on a single host.

## How it works

```
NetBox  --(ztp generate-data)-->  data/netbox-export.json  --> PR
                                                               |
                                              CI: schema check + render every config + topology check
                                                               |
                                                             merge
                                                               |
ztp-server (cron git pull every 5 min)  <----------------------+
      |
      |  device boots -> DHCP -> GET /ztp/script/{vendor}
      |                        -> GET /ztp/config/{serial}
      v
 cEOS / SR Linux / FRR configure themselves
```

The ZTP server never talks to NetBox directly. It only reads the committed
JSON, so the lab keeps working when NetBox is down, and every change goes
through a PR.

## Repo layout

| Path | What it is |
|---|---|
| `netbox/` | Seed NetBox from fixtures, export NetBox to JSON |
| `schema/validate.py` | Data model validation (used by export, topology gen, and CI) |
| `generate_topology.py` | JSON -> containerlab `.clab.yml` files and per-device bootstrap files |
| `ztp_server/` | FastAPI app and Jinja2 templates for cEOS, SR Linux, and FRR |
| `ztp_cli/` | The `ztp` command |
| `images/frr/` | FRR image with sshd so FRR nodes are reachable over SSH |
| `scripts/` | Host prep: OVS bridges, MPLS sysctls |
| `tests/` | pytest suite (222 tests) and fixtures |
| `.github/workflows/` | CI that validates every data model PR |

## Setup

Requires [uv](https://docs.astral.sh/uv/), Docker, containerlab, and a NetBox instance.

```bash
uv sync --extra dev

export nb_url=https://netbox.example.com
export nb_token="Token <your-token>"

uv run python -m netbox.build_data_model   # one-time: seed NetBox
uv run ztp generate-data                   # NetBox -> JSON, opens a PR
```

## Deploy

```bash
docker build -t netbox-ztp-lab-ztp-server:latest .
docker build --build-arg FRR_BASE=quay.io/frrouting/frr:10.5.1 -t netbox-ztp-lab-frr:10.5.1 images/frr
docker build --build-arg FRR_BASE=frrouting/frr:latest        -t netbox-ztp-lab-frr:8.4.1  images/frr

uv run python generate_topology.py

# Clos fabric lab
uv run python scripts/ensure_ovs_bridges.py topology/us-east-01a.clab.yml
sudo containerlab deploy -t topology/us-east-01a.clab.yml

# Backbone lab (MPLS sysctls are set after deploy, re-run after every redeploy)
uv run python scripts/ensure_ovs_bridges.py topology/backbone-fabric.clab.yml
sudo containerlab deploy -t topology/backbone-fabric.clab.yml
uv run python scripts/ensure_mpls_enabled.py data/netbox-export.json
```

Re-run `generate_topology.py` after any template change. Bootstrap scripts
are rendered at generation time.

FRR nodes log ZTP progress to `/tmp/ztp_bootstrap.log` inside the container.

## The `ztp` command

| Command | What it does |
|---|---|
| `ztp generate-data` | Export NetBox to `data/netbox-export.json`, commit, push, open a PR |
| `ztp start` | Run the ZTP server (default `127.0.0.1:8000`, API docs at `/docs`) |
| `ztp rebuild` | Restart the FastAPI process inside the container |
| `ztp stop` | Stop the server and exit the container |
| `ztp logs [-f]` | Tail the server log |
| `ztp dhcp-status` | dnsmasq state and current leases |
| `ztp dhcp-restart` | Restart dnsmasq |

## Device logins

Login credentials are kept out of this repo. The ZTP server reads an
optional `users.yaml` from `SECRETS_REPO_PATH` (default
`~/git/netbox-ztp-lab-secrets`):

```yaml
users:
  - username: admin
    role: admin
    auth: ssh_key
    value: ssh-ed25519 AAAA...
  - username: automation
    role: admin
    auth: password
    value: "<password>"
```

If the file is missing, devices come up with no local users.

## Tests

```bash
uv run pytest
```

## Teardown

```bash
sudo containerlab destroy -t topology/us-east-01a.clab.yml
sudo containerlab destroy -t topology/backbone-fabric.clab.yml
```
