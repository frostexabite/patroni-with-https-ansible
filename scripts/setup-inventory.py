#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Set up inventory/hosts.yml (node IPs, subnet, VIP) interactively or through arguments

Interactive (recommended):
    python3 scripts/setup-inventory.py

Everything on one line (usable from scripts/CI):
    python3 scripts/setup-inventory.py --ips 10.10.10.11,10.10.10.12,10.10.10.13 --vip 10.10.10.10 --yes
    python3 scripts/setup-inventory.py \\
        --nodes db1=10.0.0.11,db2=10.0.0.12,db3=10.0.0.13 \\
        --cidr 10.0.0.0/24 --app-cidrs 10.0.1.0/24,10.0.2.0/24 \\
        --vip 10.0.0.10 --router-id 60 --interface ens192 --yes

Checks: valid IPv4, no duplicates, inside the cluster subnet, VIP does not clash with a node,
an odd number of nodes >= 3, host names usable as hostnames
The existing file is backed up as hosts.yml.bak-<timestamp> before it is overwritten

Uses only the Python standard library (Python 3.6+, e.g. EL8's python3)
"""
from __future__ import print_function

import argparse
import datetime
import ipaddress
import os
import re
import shutil
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_OUTPUT = os.path.join(ROOT, "inventory", "hosts.yml")

HOST_RE = re.compile(r"^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$")
IFACE_RE = re.compile(r"^[A-Za-z0-9_.:@-]{1,15}$")
USER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_.-]{0,31}$")


# --------------------------------------------------------------------------- validators
# Each returns the normalised value, or raises ValueError with a readable message

def check_ip(value):
    try:
        return str(ipaddress.IPv4Address(value.strip()))
    except ValueError:
        raise ValueError("'%s' is not an IPv4 address (example 10.10.10.11)" % value)


def check_cidr(value):
    value = value.strip()
    if "/" not in value:
        raise ValueError("'%s' needs a prefix, e.g. 10.10.10.0/24" % value)
    try:
        return str(ipaddress.IPv4Network(value, strict=False))
    except ValueError:
        raise ValueError("'%s' is not a CIDR subnet (example 10.10.10.0/24)" % value)


def check_cidr_list(value):
    items = [v for v in re.split(r"[,\s]+", value.strip()) if v]
    if not items:
        raise ValueError("at least 1 subnet is required")
    out = []
    for item in items:
        net = check_cidr(item)
        if net not in out:
            out.append(net)
    return out


def check_hostname(value):
    name = value.strip().lower()
    if not HOST_RE.match(name):
        raise ValueError("name '%s' is not allowed — use a-z, 0-9 and - only (not at the start/end)" % value)
    return name


def check_node_count(value):
    try:
        n = int(value)
    except ValueError:
        raise ValueError("must be a number")
    if n < 3 or n % 2 == 0:
        raise ValueError("must be an odd number of 3 or more (3, 5, 7) so etcd has quorum")
    return n


def check_prefix(value):
    try:
        p = int(str(value).strip().lstrip("/"))
    except ValueError:
        raise ValueError("must be a number 1-32")
    if not 1 <= p <= 32:
        raise ValueError("must be between 1 and 32")
    return p


def check_router_id(value):
    try:
        r = int(value)
    except ValueError:
        raise ValueError("must be a number 1-255")
    if not 1 <= r <= 255:
        raise ValueError("must be between 1 and 255")
    return r


def check_iface(value):
    value = value.strip()
    if value and not IFACE_RE.match(value):
        raise ValueError("interface name '%s' is not valid (e.g. ens192, eth0)" % value)
    return value


def check_user(value):
    value = value.strip()
    if not USER_RE.match(value):
        raise ValueError("user name '%s' is not valid" % value)
    return value


def is_edge(net, ip):
    """network / broadcast address of the subnet (except /31, /32)"""
    return net.prefixlen <= 30 and ip in (net.network_address, net.broadcast_address)


def validate(cfg):
    """check how all values relate to each other; return a list of errors"""
    errors = []
    nodes = cfg["nodes"]
    if len(nodes) < 3 or len(nodes) % 2 == 0:
        errors.append("the number of nodes must be odd and >= 3 (now %d)" % len(nodes))
    names = [n for n, _ in nodes]
    ips = [ipaddress.IPv4Address(ip) for _, ip in nodes]
    for name in set(names):
        if names.count(name) > 1:
            errors.append("host name '%s' is duplicated" % name)
    for ip in set(ips):
        if ips.count(ip) > 1:
            errors.append("IP %s is used by more than 1 node" % ip)
    net = ipaddress.IPv4Network(cfg["cidr"])
    for name, ip in zip(names, ips):
        if ip not in net:
            errors.append("%s (%s) is outside the cluster subnet %s" % (name, ip, net))
        elif is_edge(net, ip):
            errors.append("%s (%s) is the network/broadcast address of %s" % (name, ip, net))
    if cfg["vip_enabled"]:
        vip = ipaddress.IPv4Address(cfg["vip"])
        if vip in ips:
            errors.append("VIP %s is the same as the IP of %s" % (vip, names[ips.index(vip)]))
        if vip not in net:
            errors.append("VIP %s is outside the cluster subnet %s" % (vip, net))
        elif is_edge(net, vip):
            errors.append("VIP %s is the network/broadcast address of %s" % (vip, net))
    return errors


def default_cidr(ip, prefix=24):
    return str(ipaddress.IPv4Network("%s/%d" % (ip, prefix), strict=False))


# --------------------------------------------------------------------------- existing file

def load_existing(path):
    """read usable values from the existing hosts.yml as defaults (needs PyYAML — ships with Ansible)"""
    if not os.path.exists(path):
        return {}
    try:
        import yaml  # noqa: WPS433 (optional dependency)
    except ImportError:
        return {}
    try:
        with open(path, encoding="utf-8") as fh:
            data = yaml.safe_load(fh) or {}
        group = data["all"]["children"]["pg_cluster"]
    except Exception:  # noqa: BLE001 — a broken/different old file simply means no defaults
        return {}
    gvars = group.get("vars") or {}
    allvars = (data.get("all") or {}).get("vars") or {}
    out = {}

    # values this script does not manage (e.g. per-host node_ip) are lost when the file is rewritten — warn first
    known_group = {"cluster_network_cidr", "app_allowed_cidrs", "keepalived_enabled", "keepalived_vip",
                   "keepalived_vip_prefix", "keepalived_router_id", "keepalived_interface"}
    extras = ["pg_cluster.vars.%s" % k for k in gvars if k not in known_group]
    extras += ["all.vars.%s" % k for k in allvars if k not in ("ansible_user", "ansible_become")]
    for name, hv in (group.get("hosts") or {}).items():
        extras += ["%s.%s" % (name, k) for k in (hv or {}) if k != "ansible_host"]
    extras += ["group %s" % g for g in ((data.get("all") or {}).get("children") or {}) if g != "pg_cluster"]
    if extras:
        out["extras"] = extras

    def keep(key, value, check):
        try:
            out[key] = check(str(value))
        except (ValueError, TypeError):
            pass

    nodes = []
    for name, hv in (group.get("hosts") or {}).items():
        try:
            nodes.append((check_hostname(str(name)), check_ip(str((hv or {}).get("ansible_host", "")))))
        except ValueError:
            nodes.append((str(name).lower(), None))
    if nodes:
        out["nodes"] = nodes
    if "cluster_network_cidr" in gvars:
        keep("cidr", gvars["cluster_network_cidr"], check_cidr)
    apps = gvars.get("app_allowed_cidrs")
    if apps:
        keep("app_cidrs", ",".join(apps) if isinstance(apps, list) else apps, check_cidr_list)
    if "keepalived_enabled" in gvars:
        out["vip_enabled"] = str(gvars["keepalived_enabled"]).lower() in ("true", "yes", "1")
    if "keepalived_vip" in gvars:
        keep("vip", gvars["keepalived_vip"], check_ip)
    if "keepalived_vip_prefix" in gvars:
        keep("vip_prefix", gvars["keepalived_vip_prefix"], check_prefix)
    if "keepalived_router_id" in gvars:
        keep("router_id", gvars["keepalived_router_id"], check_router_id)
    if gvars.get("keepalived_interface"):
        keep("interface", gvars["keepalived_interface"], check_iface)
    if "ansible_user" in allvars:
        keep("ssh_user", allvars["ansible_user"], check_user)
    return out


# --------------------------------------------------------------------------- interactive

def ask(prompt, default=None, check=None, allow_empty=False):
    while True:
        suffix = " [%s]" % default if default not in (None, "") else ""
        try:
            raw = input("%s%s: " % (prompt, suffix))
        except EOFError:
            print()
            sys.exit("Cancelled: input ended before all questions were answered")
        value = raw.strip()
        if not value:
            if default not in (None, ""):
                value = str(default)
            elif allow_empty:
                return ""
            else:
                print("  ! a value is required")
                continue
        if check is None:
            return value
        try:
            return check(value)
        except ValueError as exc:
            print("  ! %s" % exc)


def ask_yes_no(prompt, default=True):
    hint = "Y/n" if default else "y/N"
    while True:
        try:
            raw = input("%s [%s]: " % (prompt, hint)).strip().lower()
        except EOFError:
            print()
            sys.exit("Cancelled: input ended before all questions were answered")
        if not raw:
            return default
        if raw in ("y", "yes"):
            return True
        if raw in ("n", "no"):
            return False
        print("  ! answer y or n")


def interactive(existing):
    print("=" * 66)
    print(" Inventory setup for the PostgreSQL HA cluster (Rocky Linux / RHEL)")
    print(" Press Enter to accept the value in [ ]   Cancel with Ctrl+C")
    print("=" * 66)

    old_nodes = existing.get("nodes") or []
    count_default = len(old_nodes) if len(old_nodes) >= 3 and len(old_nodes) % 2 == 1 else 3
    count = ask("Number of nodes (odd, >= 3)", count_default, check_node_count)

    print("\n-- Nodes (the first node = Leader at install time and owner of the VIP) --")
    nodes = []
    for i in range(count):
        old_name, old_ip = old_nodes[i] if i < len(old_nodes) else ("pg-node-%d" % (i + 1), None)

        def check_unique_name(value, _taken=[n for n, _ in nodes]):
            name = check_hostname(value)
            if name in _taken:
                raise ValueError("name '%s' is already used" % name)
            return name

        def check_unique_ip(value, _taken=[ip for _, ip in nodes]):
            ip = check_ip(value)
            if ip in _taken:
                raise ValueError("IP %s is already used" % ip)
            return ip

        name = ask("Node %d  host name" % (i + 1), old_name, check_unique_name)
        ip = ask("Node %d  IP" % (i + 1), old_ip, check_unique_ip)
        nodes.append((name, ip))

    print("\n-- Network --")
    node_ips = [ipaddress.IPv4Address(ip) for _, ip in nodes]

    def check_cluster_cidr(value):
        cidr = check_cidr(value)
        net = ipaddress.IPv4Network(cidr)
        outside = [str(ip) for ip in node_ips if ip not in net]
        if outside:
            raise ValueError("these IPs are outside %s: %s" % (cidr, ", ".join(outside)))
        return cidr

    cidr_default = existing.get("cidr")
    if not cidr_default or any(ip not in ipaddress.IPv4Network(cidr_default) for ip in node_ips):
        cidr_default = default_cidr(nodes[0][1])
    cidr = ask("Cluster subnet (CIDR)", cidr_default, check_cluster_cidr)
    net = ipaddress.IPv4Network(cidr)

    app_default = ",".join(existing.get("app_cidrs") or [cidr])
    app_cidrs = ask("Application subnets allowed to reach the DB (comma separated)", app_default, check_cidr_list)

    print("\n-- VIP (keepalived) --")
    vip_enabled = ask_yes_no("Use a Virtual IP for applications", existing.get("vip_enabled", True))
    vip, vip_prefix, router_id, interface = None, net.prefixlen, 51, ""
    if vip_enabled:
        def check_vip(value):
            ip = check_ip(value)
            addr = ipaddress.IPv4Address(ip)
            if addr in node_ips:
                raise ValueError("the VIP is the same as a node IP")
            if addr not in net:
                raise ValueError("the VIP must be inside %s" % cidr)
            if is_edge(net, addr):
                raise ValueError("the VIP is the network/broadcast address of %s" % cidr)
            return ip

        old_vip = existing.get("vip")
        if old_vip and (ipaddress.IPv4Address(old_vip) in node_ips or ipaddress.IPv4Address(old_vip) not in net):
            old_vip = None
        vip = ask("VIP (an unused IP in %s)" % cidr, old_vip, check_vip)
        vip_prefix = ask("VIP prefix", existing.get("vip_prefix", net.prefixlen), check_prefix)
        router_id = ask("virtual_router_id (1-255, unique among VRRP groups on the LAN)", existing.get("router_id", 51), check_router_id)
        interface = ask("VIP network interface (empty = detect automatically)", existing.get("interface", ""), check_iface,
                        allow_empty=True)

    print("\n-- SSH --")
    ssh_user = ask("SSH user (sudo NOPASSWD)", existing.get("ssh_user", "ansible"), check_user)

    return {
        "nodes": nodes, "cidr": cidr, "app_cidrs": app_cidrs,
        "vip_enabled": vip_enabled, "vip": vip, "vip_prefix": vip_prefix,
        "router_id": router_id, "interface": interface, "ssh_user": ssh_user,
    }


# --------------------------------------------------------------------------- non-interactive

def from_args(args, existing):
    def fail(msg):
        sys.exit("ERROR: %s" % msg)

    try:
        if args.nodes:
            nodes = []
            for item in [x for x in re.split(r"[,\s]+", args.nodes) if x]:
                if "=" not in item:
                    fail("--nodes must look like name=IP separated by , (e.g. db1=10.0.0.11,db2=10.0.0.12,db3=10.0.0.13)")
                name, ip = item.split("=", 1)
                nodes.append((check_hostname(name), check_ip(ip)))
        else:
            ips = [check_ip(x) for x in re.split(r"[,\s]+", args.ips) if x]
            prefix = check_hostname(args.name_prefix + "1")[:-1]
            nodes = [("%s%d" % (prefix, i + 1), ip) for i, ip in enumerate(ips)]

        cidr = check_cidr(args.cidr) if args.cidr else default_cidr(nodes[0][1])
        app_cidrs = check_cidr_list(args.app_cidrs) if args.app_cidrs else [cidr]
        vip_enabled = not args.no_vip
        vip = None
        if vip_enabled:
            if not args.vip:
                fail("--vip <IP> is required (or --no-vip to run without a VIP)")
            vip = check_ip(args.vip)
        vip_prefix = check_prefix(args.vip_prefix) if args.vip_prefix else ipaddress.IPv4Network(cidr).prefixlen
        router_id = check_router_id(args.router_id) if args.router_id else existing.get("router_id", 51)
        interface = check_iface(args.interface or "")
        ssh_user = check_user(args.ssh_user) if args.ssh_user else existing.get("ssh_user", "ansible")
    except ValueError as exc:
        fail(str(exc))

    return {
        "nodes": nodes, "cidr": cidr, "app_cidrs": app_cidrs,
        "vip_enabled": vip_enabled, "vip": vip, "vip_prefix": vip_prefix,
        "router_id": router_id, "interface": interface, "ssh_user": ssh_user,
    }


# --------------------------------------------------------------------------- output

def render(cfg):
    stamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    out = [
        "---",
        "# =====================================================================",
        "#  Site settings — generated by scripts/setup-inventory.py on %s" % stamp,
        "#  To change: re-run the script (current values become defaults) or edit this file directly",
        "#  preflight validates the IPs / subnet / VIP before every install",
        "#",
        "#  - pg_cluster must have an odd number of hosts >= 3 (etcd quorum)",
        "#  - the first host = Leader at bootstrap and keepalived MASTER (highest priority)",
        "#  - host name = OS hostname + etcd/Patroni member name",
        "#    (settle it before the first run — it is hard to change later)",
        "# =====================================================================",
        "all:",
        "  children:",
        "    pg_cluster:",
        "      hosts:",
    ]
    for name, ip in cfg["nodes"]:
        out.append("        %s:" % name)
        out.append('          ansible_host: "%s"' % ip)
    out += [
        "",
        "      vars:",
        "        # subnet all nodes live in (used for firewall + pg_hba) — every node IP must be inside it",
        '        cluster_network_cidr: "%s"' % cfg["cidr"],
        "",
        "        # application subnets allowed to reach HAProxy 5000/5001 and PostgreSQL",
        "        app_allowed_cidrs:",
    ]
    out += ['          - "%s"' % c for c in cfg["app_cidrs"]]
    out += ["", "        # Virtual IP for applications (keepalived)"]
    if cfg["vip_enabled"]:
        out += [
            "        keepalived_enabled: true",
            '        keepalived_vip: "%s"' % cfg["vip"],
            "        keepalived_vip_prefix: %d" % cfg["vip_prefix"],
            "        keepalived_router_id: %d               # must not clash with other VRRP on the same LAN" % cfg["router_id"],
        ]
        if cfg["interface"]:
            out.append('        keepalived_interface: "%s"' % cfg["interface"])
        else:
            out.append("        # keepalived_interface: ens192         # unset = use the interface holding the node IP")
    else:
        out += [
            "        keepalived_enabled: false              # no VIP — applications connect to HAProxy on any node",
        ]
    out += [
        "",
        "  vars:",
        "    ansible_user: %s                      # SSH user with passwordless (NOPASSWD) sudo" % cfg["ssh_user"],
        "    ansible_become: true",
        "",
    ]
    return "\n".join(out)


def summary(cfg):
    lines = ["", "-- Summary --"]
    for i, (name, ip) in enumerate(cfg["nodes"]):
        lines.append("  %-20s %-15s %s" % (name, ip, "(Leader at install / VIP MASTER)" if i == 0 else ""))
    lines.append("  cluster subnet : %s" % cfg["cidr"])
    lines.append("  app subnets    : %s" % ", ".join(cfg["app_cidrs"]))
    if cfg["vip_enabled"]:
        lines.append("  VIP            : %s/%d  (VRID %d, interface %s)" % (
            cfg["vip"], cfg["vip_prefix"], cfg["router_id"], cfg["interface"] or "auto-detect"))
    else:
        lines.append("  VIP            : not used")
    lines.append("  SSH user       : %s" % cfg["ssh_user"])
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(
        description="Enter node IPs / subnet / VIP and write inventory/hosts.yml",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="Without --ips/--nodes the script runs interactively",
    )
    src = parser.add_mutually_exclusive_group()
    src.add_argument("--ips", help="IPs of all nodes separated by , (named pg-node-1, pg-node-2, ...)")
    src.add_argument("--nodes", help="name=IP separated by , e.g. db1=10.0.0.11,db2=10.0.0.12,db3=10.0.0.13")
    parser.add_argument("--name-prefix", default="pg-node-", help="host name prefix with --ips (default: pg-node-)")
    parser.add_argument("--cidr", help="cluster subnet (default: the /24 of the first node IP)")
    parser.add_argument("--app-cidrs", help="application subnets separated by , (default: the cluster subnet)")
    parser.add_argument("--vip", help="keepalived Virtual IP")
    parser.add_argument("--no-vip", action="store_true", help="no VIP / keepalived")
    parser.add_argument("--vip-prefix", help="VIP prefix (default: the cluster subnet prefix)")
    parser.add_argument("--router-id", help="keepalived virtual_router_id 1-255 (default: 51)")
    parser.add_argument("--interface", help="VIP network interface (default: detect automatically)")
    parser.add_argument("--ssh-user", help="SSH user (default: ansible)")
    parser.add_argument("-o", "--output", default=DEFAULT_OUTPUT, help="output file (default: inventory/hosts.yml)")
    parser.add_argument("-y", "--yes", action="store_true", help="write without asking for confirmation")
    parser.add_argument("--dry-run", action="store_true", help="print the resulting file without writing it")
    args = parser.parse_args()

    if args.no_vip and args.vip:
        parser.error("--vip and --no-vip cannot be used together")

    existing = load_existing(args.output)
    try:
        cfg = from_args(args, existing) if (args.ips or args.nodes) else interactive(existing)
    except KeyboardInterrupt:
        print("\nCancelled — no file was changed")
        return 130

    errors = validate(cfg)
    if errors:
        print("\nERROR: some values are not valid:", file=sys.stderr)
        for err in errors:
            print("  - %s" % err, file=sys.stderr)
        return 2

    content = render(cfg)
    print(summary(cfg))
    if existing.get("extras"):
        print("\nWarning: these values in %s are not part of this form and will not be written to the new file" % os.path.relpath(args.output))
        print("         (the old file is backed up — copy them back yourself if still needed):")
        for item in existing["extras"]:
            print("           - %s" % item)
    if args.dry_run:
        print("\n" + content)
        return 0

    if not args.yes:
        try:
            if not ask_yes_no("\nSave to %s" % os.path.relpath(args.output), True):
                print("Cancelled — no file was changed")
                return 1
        except KeyboardInterrupt:
            print("\nCancelled — no file was changed")
            return 130

    out_dir = os.path.dirname(os.path.abspath(args.output))
    if not os.path.isdir(out_dir):
        os.makedirs(out_dir)
    if os.path.exists(args.output):
        backup = "%s.bak-%s" % (args.output, datetime.datetime.now().strftime("%Y%m%d-%H%M%S"))
        shutil.copy2(args.output, backup)
        print("Backed up the old file to %s" % os.path.relpath(backup))
    tmp = args.output + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(content)
    os.replace(tmp, args.output)
    print("Wrote %s" % os.path.relpath(args.output))
    print("\nNext steps:")
    print("  ansible-playbook playbooks/preflight.yml --ask-vault-pass   # readiness checks (no changes)")
    print("  ansible-playbook site.yml --ask-vault-pass                  # install")
    return 0


if __name__ == "__main__":
    sys.exit(main())
