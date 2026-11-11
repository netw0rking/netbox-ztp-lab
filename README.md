# netbox-ztp-lab

Containerlab network lab built from NetBox. Devices configure themselves through ZTP.

- Clos fabric: SR Linux spines, cEOS and FRR leafs, EVPN/VXLAN
- Backbone: 10 sites, IS-IS with SR-MPLS, iBGP, EVPN Type-5 DCI
- NetBox data is exported to `data/netbox-export.json`, checked in CI, and served by a FastAPI ZTP server

## Usage

```bash
uv sync --extra dev
export nb_url=https://netbox.example.com
export nb_token="Token <token>"

uv run ztp generate-data          # export NetBox data
uv run python generate_topology.py
sudo containerlab deploy -t topology/backbone-fabric.clab.yml
```

## Tests

```bash
uv run pytest
```
