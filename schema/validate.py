def validate_data_model(data):
    errors = []
    devices = data.get("devices", {})
    seen_ips = {}
    seen_sr_indexes = {}

    for name, device in devices.items():
        role = device.get("role")
        if role in ("spine", "leaf"):
            for field in ("loopback", "bgp_asn", "serial_number"):
                if field not in device:
                    errors.append(f"{name}: missing required field '{field}' for role '{role}'")

        # SR-MPLS backbone underlay - sr_index is
        # an IS-IS prefix-SID, which must be unique across the whole
        # backbone or two devices advertise the same label-switched
        # identity.
        sr_index = device.get("sr_index")
        if sr_index is not None:
            if sr_index in seen_sr_indexes:
                errors.append(
                    f"{name}: duplicate sr_index {sr_index}, also used by {seen_sr_indexes[sr_index]}"
                )
            else:
                seen_sr_indexes[sr_index] = name

        # L3VNI/VRF (routed EVPN symmetric-IRB access)
        # only makes sense as a pair - a vrf with no l3vni has nothing to
        # bind it to the overlay, and an l3vni with no vrf has nowhere in
        # the kernel to enslave access interfaces.
        has_l3vni = "l3vni" in device
        has_vrf_name = "vrf_name" in device
        if has_l3vni != has_vrf_name:
            errors.append(f"{name}: 'l3vni' and 'vrf_name' must be set together")

        interfaces = device.get("interfaces", {})
        for if_name, intf in interfaces.items():
            connects_to = intf.get("connects_to")
            if not connects_to:
                errors.append(f"{name}/{if_name}: missing connects_to")
                continue

            peer_device = connects_to.get("device")
            peer_if = connects_to.get("interface")
            # Backbone-ring links intentionally cross site/fixture
            # boundaries - a single-site fixture only ever contains its
            # own site's BBR, so the peer (another site's BBR) is never
            # resolvable within this one data model. Cross-site
            # cable-peer resolution instead happens against the
            # combined, multi-site NetBox-exported model
            # (export_data_model.py's own global device_by_id), not
            # this single-fixture check.
            if intf.get("role") != "backbone":
                if peer_device not in devices:
                    errors.append(f"{name}/{if_name}: connects_to unknown device '{peer_device}'")
                    continue

                peer_interfaces = devices[peer_device].get("interfaces", {})
                if peer_if not in peer_interfaces:
                    errors.append(
                        f"{name}/{if_name}: connects_to unknown interface '{peer_device}/{peer_if}'"
                    )

            ip = intf.get("ip")
            if ip:
                if ip in seen_ips:
                    errors.append(f"{name}/{if_name}: duplicate IP {ip}, also used by {seen_ips[ip]}")
                else:
                    seen_ips[ip] = f"{name}/{if_name}"

        loopback = device.get("loopback")
        if loopback:
            if loopback in seen_ips:
                errors.append(f"{name}: duplicate IP {loopback}, also used by {seen_ips[loopback]}")
            else:
                seen_ips[loopback] = f"{name} loopback"

        mgmt_ip = device.get("mgmt_ip")
        if mgmt_ip:
            if mgmt_ip in seen_ips:
                errors.append(f"{name}: duplicate IP {mgmt_ip}, also used by {seen_ips[mgmt_ip]}")
            else:
                seen_ips[mgmt_ip] = f"{name} mgmt_ip"

    return errors
