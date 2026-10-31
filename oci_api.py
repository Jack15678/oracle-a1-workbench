"""Narrow OCI SDK subprocess adapter for the engine's existing command contract.

The SDK is optional for source installs that already have OCI CLI. Release builds
bundle it, so no shell, WSL, or separately installed CLI is needed.
"""
import argparse
import json
from pathlib import Path
import sys
import time


def parser():
    result = argparse.ArgumentParser(description="Internal Oracle A1 SDK adapter")
    result.add_argument("service", choices=("iam", "compute", "bv", "network"))
    result.add_argument("resource")
    result.add_argument("action", choices=("list", "get", "create", "update", "launch"))
    result.add_argument("--config-file", required=True)
    result.add_argument("--profile", default="DEFAULT")
    result.add_argument("--region", required=True)
    result.add_argument("--output", choices=("json",), default="json")
    result.add_argument("--no-retry", action="store_true")
    result.add_argument("--connection-timeout", type=float, default=15)
    result.add_argument("--read-timeout", type=float, default=60)
    result.add_argument("--proxy", default="")
    result.add_argument("--all", action="store_true")
    result.add_argument("--force", action="store_true")
    for name in ("tenancy-id", "compartment-id", "compartment-id-in-subtree", "access-level",
                 "availability-domain", "instance-id", "operating-system", "operating-system-version",
                 "shape", "sort-by", "sort-order", "cidr-block", "display-name", "dns-label", "vcn-id",
                 "is-enabled", "rt-id", "route-rules", "security-list-id", "ingress-security-rules",
                 "egress-security-rules", "route-table-id", "security-list-ids", "prohibit-public-ip-on-vnic",
                 "wait-for-state", "subnet-id", "shape-config", "source-details", "assign-public-ip",
                 "ssh-authorized-keys-file"):
        result.add_argument("--" + name)
    return result


def as_bool(value):
    if value not in ("true", "false"):
        raise ValueError("Expected true or false")
    return value == "true"


def command(client, args, sdk):
    """Return the one SDK operation and arguments needed by this command."""
    operation = (args.service, args.resource, args.action)
    cid = args.compartment_id
    models = sdk.core.models
    reads = {
        ("iam", "region-subscription", "list"): ("list_region_subscriptions", {"tenancy_id": args.tenancy_id}),
        ("iam", "availability-domain", "list"): ("list_availability_domains", {"compartment_id": cid}),
        ("iam", "compartment", "list"): ("list_compartments", {"compartment_id": cid,
            "compartment_id_in_subtree": as_bool(args.compartment_id_in_subtree or "false"), "access_level": args.access_level or "ANY"}),
        ("compute", "instance", "list"): ("list_instances", {"compartment_id": cid}),
        ("compute", "instance", "get"): ("get_instance", {"instance_id": args.instance_id}),
        ("bv", "volume", "list"): ("list_volumes", {"compartment_id": cid}),
        ("bv", "boot-volume", "list"): ("list_boot_volumes", {"compartment_id": cid, "availability_domain": args.availability_domain}),
        ("compute", "image", "list"): ("list_images", {"compartment_id": cid,
            "operating_system": args.operating_system, "operating_system_version": args.operating_system_version,
            "shape": args.shape, "sort_by": args.sort_by, "sort_order": args.sort_order}),
        ("network", "subnet", "get"): ("get_subnet", {"subnet_id": args.subnet_id}),
        ("network", "security-list", "get"): ("get_security_list", {"security_list_id": args.security_list_id}),
    }
    if operation in reads:
        name, options = reads[operation]
        return getattr(client, name), {k: v for k, v in options.items() if v is not None}
    if operation == ("compute", "instance", "launch"):
        shape, source = json.loads(args.shape_config), json.loads(args.source_details)
        if source.get("sourceType") != "image":
            raise ValueError("Only an image source is supported")
        details = models.LaunchInstanceDetails(
            availability_domain=args.availability_domain, compartment_id=cid, shape=args.shape,
            display_name=args.display_name,
            shape_config=models.LaunchInstanceShapeConfigDetails(ocpus=shape["ocpus"], memory_in_gbs=shape["memoryInGBs"]),
            source_details=models.InstanceSourceViaImageDetails(image_id=source["imageId"],
                boot_volume_size_in_gbs=source["bootVolumeSizeInGBs"], boot_volume_vpus_per_gb=source["bootVolumeVpusPerGB"]),
            create_vnic_details=models.CreateVnicDetails(subnet_id=args.subnet_id, assign_public_ip=as_bool(args.assign_public_ip)),
            metadata={"ssh_authorized_keys": Path(args.ssh_authorized_keys_file).read_text(encoding="utf-8")})
        return client.launch_instance, {"launch_instance_details": details}
    if operation == ("network", "vcn", "create"):
        return client.create_vcn, {"create_vcn_details": models.CreateVcnDetails(compartment_id=cid,
            cidr_block=args.cidr_block, display_name=args.display_name, dns_label=args.dns_label)}
    if operation == ("network", "internet-gateway", "create"):
        return client.create_internet_gateway, {"create_internet_gateway_details": models.CreateInternetGatewayDetails(
            compartment_id=cid, vcn_id=args.vcn_id, is_enabled=as_bool(args.is_enabled), display_name=args.display_name)}
    if operation == ("network", "route-table", "update"):
        rules = [models.RouteRule(destination=x["destination"], destination_type=x["destinationType"],
                                 network_entity_id=x["networkEntityId"]) for x in json.loads(args.route_rules)]
        return client.update_route_table, {"rt_id": args.rt_id,
            "update_route_table_details": models.UpdateRouteTableDetails(route_rules=rules)}
    if operation == ("network", "security-list", "update"):
        ingress = []
        for item in json.loads(args.ingress_security_rules):
            ports = item["tcpOptions"]["destinationPortRange"]
            ingress.append(models.IngressSecurityRule(source=item["source"], source_type=item["sourceType"],
                protocol=item["protocol"], is_stateless=item["isStateless"],
                tcp_options=models.TcpOptions(destination_port_range=models.PortRange(min=ports["min"], max=ports["max"]))))
        outgoing = [models.EgressSecurityRule(destination=x["destination"], protocol=x["protocol"],
                    is_stateless=x["isStateless"]) for x in json.loads(args.egress_security_rules)]
        return client.update_security_list, {"security_list_id": args.security_list_id,
            "update_security_list_details": models.UpdateSecurityListDetails(ingress_security_rules=ingress, egress_security_rules=outgoing)}
    if operation == ("network", "subnet", "create"):
        return client.create_subnet, {"create_subnet_details": models.CreateSubnetDetails(compartment_id=cid,
            vcn_id=args.vcn_id, cidr_block=args.cidr_block, display_name=args.display_name, dns_label=args.dns_label,
            route_table_id=args.route_table_id, security_list_ids=json.loads(args.security_list_ids),
            prohibit_public_ip_on_vnic=as_bool(args.prohibit_public_ip_on_vnic))}
    raise ValueError("Unsupported OCI operation: " + " ".join(operation))


def make_client(args, sdk):
    config = sdk.config.from_file(str(Path(args.config_file).expanduser()), args.profile)
    config["region"] = args.region
    factory = {"iam": sdk.identity.IdentityClient, "compute": sdk.core.ComputeClient,
               "bv": sdk.core.BlockstorageClient, "network": sdk.core.VirtualNetworkClient}[args.service]
    client = factory(config, retry_strategy=sdk.retry.NoneRetryStrategy(),
                     timeout=(args.connection_timeout, args.read_timeout))
    session = client.base_client.session
    session.trust_env = False
    session.proxies = {"http": args.proxy, "https": args.proxy} if args.proxy else {}
    return client


def execute(args, sdk, client=None):
    client = client or make_client(args, sdk)
    method, options = command(client, args, sdk)
    options["retry_strategy"] = sdk.retry.NoneRetryStrategy()
    response = method(**options)
    if args.action == "list" and args.all:
        data = list(response.data)
        while response.headers.get("opc-next-page"):
            options["page"] = response.headers["opc-next-page"]
            response = method(**options)
            data.extend(response.data)
        return data
    if (args.service, args.resource, args.action) == ("network", "subnet", "create") and args.wait_for_state:
        # The parent enforces a 120-second request deadline and can terminate this
        # process. Polling here is only for this newly created subnet, never launch.
        deadline = time.monotonic() + 100
        while response.data.lifecycle_state != args.wait_for_state:
            if response.data.lifecycle_state in ("TERMINATING", "TERMINATED", "FAILED", "FAULTY"):
                raise ValueError("Subnet entered " + response.data.lifecycle_state)
            if time.monotonic() >= deadline:
                raise TimeoutError("Timed out waiting for subnet " + args.wait_for_state)
            time.sleep(2)
            response = client.get_subnet(response.data.id, retry_strategy=sdk.retry.NoneRetryStrategy())
    return response.data


def hyphenated(value):
    if isinstance(value, dict):
        return {key.replace("_", "-"): hyphenated(item) for key, item in value.items()}
    if isinstance(value, list):
        return [hyphenated(item) for item in value]
    return value


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        import oci
        data = execute(args, oci)
        print(json.dumps({"data": hyphenated(oci.util.to_dict(data))}, ensure_ascii=False, default=str))
    except Exception as error:
        print("ServiceError: " + json.dumps({"message": str(error), "code": getattr(error, "code", ""),
              "status": getattr(error, "status", 0)}, ensure_ascii=False), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
