#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ตั้งค่า inventory/hosts.yml (IP ของ node, subnet, VIP) แบบถาม-ตอบ หรือผ่าน argument

แบบถาม-ตอบ (แนะนำ):
    python3 scripts/setup-inventory.py

แบบใส่ค่าในบรรทัดเดียว (ใช้ใน script/CI ได้):
    python3 scripts/setup-inventory.py --ips 10.10.10.11,10.10.10.12,10.10.10.13 --vip 10.10.10.10 --yes
    python3 scripts/setup-inventory.py \\
        --nodes db1=10.0.0.11,db2=10.0.0.12,db3=10.0.0.13 \\
        --cidr 10.0.0.0/24 --app-cidrs 10.0.1.0/24,10.0.2.0/24 \\
        --vip 10.0.0.10 --router-id 60 --interface ens192 --yes

ตรวจให้: IPv4 ถูกรูปแบบ, ไม่ซ้ำ, อยู่ใน cluster subnet, VIP ไม่ชนกับ node,
จำนวน node เป็นเลขคี่ >= 3, ชื่อ host ใช้เป็น hostname ได้
ไฟล์เดิมจะถูกสำรองเป็น hosts.yml.bak-<วันเวลา> ก่อนเขียนทับ

ใช้แค่ Python standard library (รองรับ Python 3.6+ เช่น python3 ของ EL8)
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
# แต่ละตัวคืนค่าที่ normalize แล้ว หรือ raise ValueError พร้อมข้อความภาษาไทย

def check_ip(value):
    try:
        return str(ipaddress.IPv4Address(value.strip()))
    except ValueError:
        raise ValueError("'%s' ไม่ใช่ IPv4 (ตัวอย่าง 10.10.10.11)" % value)


def check_cidr(value):
    value = value.strip()
    if "/" not in value:
        raise ValueError("'%s' ต้องมี prefix ด้วย เช่น 10.10.10.0/24" % value)
    try:
        return str(ipaddress.IPv4Network(value, strict=False))
    except ValueError:
        raise ValueError("'%s' ไม่ใช่ subnet แบบ CIDR (ตัวอย่าง 10.10.10.0/24)" % value)


def check_cidr_list(value):
    items = [v for v in re.split(r"[,\s]+", value.strip()) if v]
    if not items:
        raise ValueError("ต้องมีอย่างน้อย 1 subnet")
    out = []
    for item in items:
        net = check_cidr(item)
        if net not in out:
            out.append(net)
    return out


def check_hostname(value):
    name = value.strip().lower()
    if not HOST_RE.match(name):
        raise ValueError("ชื่อ '%s' ใช้ไม่ได้ — ใช้ a-z, 0-9 และ - เท่านั้น (ไม่ขึ้นต้น/ลงท้ายด้วย -)" % value)
    return name


def check_node_count(value):
    try:
        n = int(value)
    except ValueError:
        raise ValueError("ต้องเป็นตัวเลข")
    if n < 3 or n % 2 == 0:
        raise ValueError("ต้องเป็นเลขคี่ตั้งแต่ 3 ขึ้นไป (3, 5, 7) เพื่อให้ etcd มี quorum")
    return n


def check_prefix(value):
    try:
        p = int(str(value).strip().lstrip("/"))
    except ValueError:
        raise ValueError("ต้องเป็นตัวเลข 1-32")
    if not 1 <= p <= 32:
        raise ValueError("ต้องอยู่ระหว่าง 1-32")
    return p


def check_router_id(value):
    try:
        r = int(value)
    except ValueError:
        raise ValueError("ต้องเป็นตัวเลข 1-255")
    if not 1 <= r <= 255:
        raise ValueError("ต้องอยู่ระหว่าง 1-255")
    return r


def check_iface(value):
    value = value.strip()
    if value and not IFACE_RE.match(value):
        raise ValueError("ชื่อ interface '%s' ไม่ถูกต้อง (เช่น ens192, eth0)" % value)
    return value


def check_user(value):
    value = value.strip()
    if not USER_RE.match(value):
        raise ValueError("ชื่อ user '%s' ไม่ถูกต้อง" % value)
    return value


def is_edge(net, ip):
    """network / broadcast address ของ subnet (ยกเว้น /31, /32)"""
    return net.prefixlen <= 30 and ip in (net.network_address, net.broadcast_address)


def validate(cfg):
    """ตรวจความสัมพันธ์ระหว่างค่าทั้งหมด คืน list ของข้อผิดพลาด"""
    errors = []
    nodes = cfg["nodes"]
    if len(nodes) < 3 or len(nodes) % 2 == 0:
        errors.append("จำนวน node ต้องเป็นเลขคี่ >= 3 (ตอนนี้ %d)" % len(nodes))
    names = [n for n, _ in nodes]
    ips = [ipaddress.IPv4Address(ip) for _, ip in nodes]
    for name in set(names):
        if names.count(name) > 1:
            errors.append("ชื่อ host '%s' ซ้ำ" % name)
    for ip in set(ips):
        if ips.count(ip) > 1:
            errors.append("IP %s ถูกใช้มากกว่า 1 node" % ip)
    net = ipaddress.IPv4Network(cfg["cidr"])
    for name, ip in zip(names, ips):
        if ip not in net:
            errors.append("%s (%s) อยู่นอก cluster subnet %s" % (name, ip, net))
        elif is_edge(net, ip):
            errors.append("%s (%s) เป็น network/broadcast address ของ %s" % (name, ip, net))
    if cfg["vip_enabled"]:
        vip = ipaddress.IPv4Address(cfg["vip"])
        if vip in ips:
            errors.append("VIP %s ซ้ำกับ IP ของ %s" % (vip, names[ips.index(vip)]))
        if vip not in net:
            errors.append("VIP %s อยู่นอก cluster subnet %s" % (vip, net))
        elif is_edge(net, vip):
            errors.append("VIP %s เป็น network/broadcast address ของ %s" % (vip, net))
    return errors


def default_cidr(ip, prefix=24):
    return str(ipaddress.IPv4Network("%s/%d" % (ip, prefix), strict=False))


# --------------------------------------------------------------------------- existing file

def load_existing(path):
    """อ่านค่าที่ใช้ได้จาก hosts.yml เดิมเป็นค่า default (ต้องมี PyYAML — มีมากับ Ansible)"""
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
    except Exception:  # noqa: BLE001 — ไฟล์เดิมเสีย/รูปแบบอื่น ก็แค่ไม่มีค่า default
        return {}
    gvars = group.get("vars") or {}
    allvars = (data.get("all") or {}).get("vars") or {}
    out = {}

    # ค่าที่สคริปต์ไม่ได้จัดการ (เช่น node_ip ราย host) จะหายเมื่อเขียนไฟล์ใหม่ — เตือนไว้ก่อน
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
            sys.exit("ยกเลิก: input สิ้นสุดก่อนตอบครบ")
        value = raw.strip()
        if not value:
            if default not in (None, ""):
                value = str(default)
            elif allow_empty:
                return ""
            else:
                print("  ! ต้องใส่ค่า")
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
            sys.exit("ยกเลิก: input สิ้นสุดก่อนตอบครบ")
        if not raw:
            return default
        if raw in ("y", "yes", "ใช่"):
            return True
        if raw in ("n", "no", "ไม่"):
            return False
        print("  ! ตอบ y หรือ n")


def interactive(existing):
    print("=" * 66)
    print(" ตั้งค่า inventory สำหรับ PostgreSQL HA cluster (Rocky Linux / RHEL)")
    print(" กด Enter เพื่อใช้ค่าในวงเล็บ [ ]   ยกเลิกได้ด้วย Ctrl+C")
    print("=" * 66)

    old_nodes = existing.get("nodes") or []
    count_default = len(old_nodes) if len(old_nodes) >= 3 and len(old_nodes) % 2 == 1 else 3
    count = ask("จำนวน node (เลขคี่ >= 3)", count_default, check_node_count)

    print("\n-- Node (node แรก = Leader ตอนติดตั้ง และเป็นเจ้าของ VIP) --")
    nodes = []
    for i in range(count):
        old_name, old_ip = old_nodes[i] if i < len(old_nodes) else ("pg-node-%d" % (i + 1), None)

        def check_unique_name(value, _taken=[n for n, _ in nodes]):
            name = check_hostname(value)
            if name in _taken:
                raise ValueError("ชื่อ '%s' ใช้ไปแล้ว" % name)
            return name

        def check_unique_ip(value, _taken=[ip for _, ip in nodes]):
            ip = check_ip(value)
            if ip in _taken:
                raise ValueError("IP %s ใช้ไปแล้ว" % ip)
            return ip

        name = ask("Node %d  ชื่อ host" % (i + 1), old_name, check_unique_name)
        ip = ask("Node %d  IP" % (i + 1), old_ip, check_unique_ip)
        nodes.append((name, ip))

    print("\n-- Network --")
    node_ips = [ipaddress.IPv4Address(ip) for _, ip in nodes]

    def check_cluster_cidr(value):
        cidr = check_cidr(value)
        net = ipaddress.IPv4Network(cidr)
        outside = [str(ip) for ip in node_ips if ip not in net]
        if outside:
            raise ValueError("IP ต่อไปนี้อยู่นอก %s: %s" % (cidr, ", ".join(outside)))
        return cidr

    cidr_default = existing.get("cidr")
    if not cidr_default or any(ip not in ipaddress.IPv4Network(cidr_default) for ip in node_ips):
        cidr_default = default_cidr(nodes[0][1])
    cidr = ask("Subnet ของ cluster (CIDR)", cidr_default, check_cluster_cidr)
    net = ipaddress.IPv4Network(cidr)

    app_default = ",".join(existing.get("app_cidrs") or [cidr])
    app_cidrs = ask("Subnet ของ application ที่ให้ต่อ DB (คั่นด้วย , )", app_default, check_cidr_list)

    print("\n-- VIP (keepalived) --")
    vip_enabled = ask_yes_no("ใช้ Virtual IP สำหรับ application หรือไม่", existing.get("vip_enabled", True))
    vip, vip_prefix, router_id, interface = None, net.prefixlen, 51, ""
    if vip_enabled:
        def check_vip(value):
            ip = check_ip(value)
            addr = ipaddress.IPv4Address(ip)
            if addr in node_ips:
                raise ValueError("VIP ซ้ำกับ IP ของ node")
            if addr not in net:
                raise ValueError("VIP ต้องอยู่ใน %s" % cidr)
            if is_edge(net, addr):
                raise ValueError("VIP เป็น network/broadcast address ของ %s" % cidr)
            return ip

        old_vip = existing.get("vip")
        if old_vip and (ipaddress.IPv4Address(old_vip) in node_ips or ipaddress.IPv4Address(old_vip) not in net):
            old_vip = None
        vip = ask("VIP (IP ว่างใน %s)" % cidr, old_vip, check_vip)
        vip_prefix = ask("Prefix ของ VIP", existing.get("vip_prefix", net.prefixlen), check_prefix)
        router_id = ask("virtual_router_id (1-255, ห้ามซ้ำกับ VRRP อื่นใน LAN)", existing.get("router_id", 51), check_router_id)
        interface = ask("Network interface ของ VIP (เว้นว่าง = ตรวจหาเอง)", existing.get("interface", ""), check_iface,
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
                    fail("--nodes ต้องเป็นรูปแบบ ชื่อ=IP คั่นด้วย , (เช่น db1=10.0.0.11,db2=10.0.0.12,db3=10.0.0.13)")
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
                fail("ต้องใส่ --vip <IP> (หรือ --no-vip ถ้าไม่ใช้ VIP)")
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
        "#  ค่าของไซต์ — สร้างโดย scripts/setup-inventory.py เมื่อ %s" % stamp,
        "#  แก้ค่า: รันสคริปต์ซ้ำ (ค่าเดิมจะเป็น default) หรือแก้ไฟล์นี้ตรง ๆ",
        "#  preflight ตรวจความถูกต้องของ IP / subnet / VIP ให้ก่อนติดตั้งทุกครั้ง",
        "#",
        "#  - จำนวน host ใน pg_cluster ต้องเป็นเลขคี่ >= 3 (etcd quorum)",
        "#  - host แรก = Leader ตอน bootstrap และ keepalived MASTER (priority สูงสุด)",
        "#  - ชื่อ host = hostname ของ OS + ชื่อ member ใน etcd/Patroni",
        "#    (ตั้งให้เสร็จก่อนรันครั้งแรก — เปลี่ยนภายหลังไม่ได้ง่าย ๆ)",
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
        "        # subnet ที่ node ทั้งหมดอยู่ (ใช้กับ firewall + pg_hba) — IP ทุก node ต้องอยู่ในวงนี้",
        '        cluster_network_cidr: "%s"' % cfg["cidr"],
        "",
        "        # subnet ของ application ที่ต่อเข้า HAProxy 5000/5001 และ PostgreSQL",
        "        app_allowed_cidrs:",
    ]
    out += ['          - "%s"' % c for c in cfg["app_cidrs"]]
    out += ["", "        # Virtual IP สำหรับ application (keepalived)"]
    if cfg["vip_enabled"]:
        out += [
            "        keepalived_enabled: true",
            '        keepalived_vip: "%s"' % cfg["vip"],
            "        keepalived_vip_prefix: %d" % cfg["vip_prefix"],
            "        keepalived_router_id: %d               # ห้ามซ้ำกับ VRRP อื่นใน LAN เดียวกัน" % cfg["router_id"],
        ]
        if cfg["interface"]:
            out.append('        keepalived_interface: "%s"' % cfg["interface"])
        else:
            out.append("        # keepalived_interface: ens192         # ไม่ใส่ = ใช้ interface ที่มี IP ของ node อยู่")
    else:
        out += [
            "        keepalived_enabled: false              # ไม่ใช้ VIP — application ต่อ HAProxy ของ node ใดก็ได้",
        ]
    out += [
        "",
        "  vars:",
        "    ansible_user: %s                      # user ที่ SSH เข้าได้และ sudo แบบ NOPASSWD" % cfg["ssh_user"],
        "    ansible_become: true",
        "",
    ]
    return "\n".join(out)


def summary(cfg):
    lines = ["", "-- สรุป --"]
    for i, (name, ip) in enumerate(cfg["nodes"]):
        lines.append("  %-20s %-15s %s" % (name, ip, "(Leader ตอนติดตั้ง / VIP MASTER)" if i == 0 else ""))
    lines.append("  cluster subnet : %s" % cfg["cidr"])
    lines.append("  app subnets    : %s" % ", ".join(cfg["app_cidrs"]))
    if cfg["vip_enabled"]:
        lines.append("  VIP            : %s/%d  (VRID %d, interface %s)" % (
            cfg["vip"], cfg["vip_prefix"], cfg["router_id"], cfg["interface"] or "ตรวจหาเอง"))
    else:
        lines.append("  VIP            : ไม่ใช้")
    lines.append("  SSH user       : %s" % cfg["ssh_user"])
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(
        description="ใส่ IP ของ node / subnet / VIP แล้วเขียน inventory/hosts.yml",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="ไม่ใส่ --ips/--nodes = โหมดถาม-ตอบ",
    )
    src = parser.add_mutually_exclusive_group()
    src.add_argument("--ips", help="IP ของทุก node คั่นด้วย , (ตั้งชื่อให้เป็น pg-node-1, pg-node-2, ...)")
    src.add_argument("--nodes", help="ชื่อ=IP คั่นด้วย , เช่น db1=10.0.0.11,db2=10.0.0.12,db3=10.0.0.13")
    parser.add_argument("--name-prefix", default="pg-node-", help="prefix ชื่อ host เมื่อใช้ --ips (default: pg-node-)")
    parser.add_argument("--cidr", help="cluster subnet (default: /24 ของ IP node แรก)")
    parser.add_argument("--app-cidrs", help="subnet ของ application คั่นด้วย , (default: เท่ากับ cluster subnet)")
    parser.add_argument("--vip", help="Virtual IP ของ keepalived")
    parser.add_argument("--no-vip", action="store_true", help="ไม่ใช้ VIP / keepalived")
    parser.add_argument("--vip-prefix", help="prefix ของ VIP (default: prefix ของ cluster subnet)")
    parser.add_argument("--router-id", help="keepalived virtual_router_id 1-255 (default: 51)")
    parser.add_argument("--interface", help="network interface ของ VIP (default: ตรวจหาเอง)")
    parser.add_argument("--ssh-user", help="SSH user (default: ansible)")
    parser.add_argument("-o", "--output", default=DEFAULT_OUTPUT, help="ไฟล์ปลายทาง (default: inventory/hosts.yml)")
    parser.add_argument("-y", "--yes", action="store_true", help="ไม่ต้องถามยืนยันก่อนเขียนไฟล์")
    parser.add_argument("--dry-run", action="store_true", help="แสดงไฟล์ที่จะได้ แต่ไม่เขียน")
    args = parser.parse_args()

    if args.no_vip and args.vip:
        parser.error("--vip กับ --no-vip ใช้พร้อมกันไม่ได้")

    existing = load_existing(args.output)
    try:
        cfg = from_args(args, existing) if (args.ips or args.nodes) else interactive(existing)
    except KeyboardInterrupt:
        print("\nยกเลิก — ไม่ได้แก้ไฟล์")
        return 130

    errors = validate(cfg)
    if errors:
        print("\nERROR: ค่าที่ใส่ยังไม่ถูกต้อง:", file=sys.stderr)
        for err in errors:
            print("  - %s" % err, file=sys.stderr)
        return 2

    content = render(cfg)
    print(summary(cfg))
    if existing.get("extras"):
        print("\nคำเตือน: ค่าต่อไปนี้ใน %s ไม่ได้อยู่ในแบบฟอร์มนี้ และจะไม่ถูกเขียนลงไฟล์ใหม่" % os.path.relpath(args.output))
        print("         (ไฟล์เดิมถูกสำรองไว้ — คัดลอกกลับเองถ้ายังต้องใช้):")
        for item in existing["extras"]:
            print("           - %s" % item)
    if args.dry_run:
        print("\n" + content)
        return 0

    if not args.yes:
        try:
            if not ask_yes_no("\nบันทึกลง %s" % os.path.relpath(args.output), True):
                print("ยกเลิก — ไม่ได้แก้ไฟล์")
                return 1
        except KeyboardInterrupt:
            print("\nยกเลิก — ไม่ได้แก้ไฟล์")
            return 130

    out_dir = os.path.dirname(os.path.abspath(args.output))
    if not os.path.isdir(out_dir):
        os.makedirs(out_dir)
    if os.path.exists(args.output):
        backup = "%s.bak-%s" % (args.output, datetime.datetime.now().strftime("%Y%m%d-%H%M%S"))
        shutil.copy2(args.output, backup)
        print("สำรองไฟล์เดิมไว้ที่ %s" % os.path.relpath(backup))
    tmp = args.output + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(content)
    os.replace(tmp, args.output)
    print("เขียน %s แล้ว" % os.path.relpath(args.output))
    print("\nขั้นต่อไป:")
    print("  ansible-playbook playbooks/preflight.yml --ask-vault-pass   # ตรวจความพร้อม (ไม่แก้เครื่อง)")
    print("  ansible-playbook site.yml --ask-vault-pass                  # ติดตั้ง")
    return 0


if __name__ == "__main__":
    sys.exit(main())
