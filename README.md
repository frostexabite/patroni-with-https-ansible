# PostgreSQL HA Cluster on Rocky Linux / RHEL 8 · 9 · 10 — Patroni + etcd + HAProxy + keepalived (Ansible)

An Ansible project that installs PostgreSQL High Availability as a **stacked topology** (every node runs every component).
It is adapted from the article *Building a Highly Available PostgreSQL Cluster with Patroni, etcd, and HAProxy (Oracle Linux 8 & PostgreSQL 16)*
by Foyez Ahmed Rony, and runs on **every supported Rocky Linux and Red Hat Enterprise Linux release (8, 9, 10)**
with the latest version of every component. IPs, subnets and the VIP are values you enter yourself (nothing is hard-coded),
and every channel is encrypted with **TLS from a cluster-owned CA** (applications, replication, Patroni REST API, etcd, HAProxy stats).

```
                         Application
                              │
                   VIP <VIP> (keepalived / VRRP)
                    :5000 write      :5001 read
        ┌─────────────────────┼─────────────────────┐
 ┌──────┴────────┐    ┌───────┴───────┐    ┌────────┴──────┐
 │ pg-node-1     │    │ pg-node-2     │    │ pg-node-3     │
 │ HAProxy       │    │ HAProxy       │    │ HAProxy       │  health check → Patroni REST :8008 (https)
 │ Patroni       │    │ Patroni       │    │ Patroni       │  (/primary, /replica)
 │ PostgreSQL 18 │═══▶│ PostgreSQL 18 │    │ PostgreSQL 18 │  streaming replication :5432 (TLS)
 │   (Leader)    │═══════════════════════▶ │   (Replica)   │
 │ etcd          │◀──▶│ etcd          │◀──▶│ etcd          │  quorum :2379 / :2380 (mutual TLS)
 └───────────────┘    └───────────────┘    └───────────────┘
   <IP-node-1>          <IP-node-2>          <IP-node-3>
```

Node names and the number of nodes (3, 5, 7, ...) are chosen when you enter the site values.

## Supported operating systems

| OS | System Python (used by Ansible) | Patroni Python | PostgreSQL 18 (`pg_install_source: auto`) | ansible-core on the control node |
|---|---|---|---|---|
| Rocky Linux / RHEL **8** (8.10) | platform-python 3.6 | `python3.12` from AppStream | PGDG (+ `postgresql` module disabled) | **2.16.x only** |
| Rocky Linux / RHEL **9** (9.4+) | python3 3.9 | `python3.12` from AppStream | PGDG (+ `postgresql` module disabled) | 2.16 – 2.21 |
| Rocky Linux / RHEL **10** | python3 3.12 | `python3` (3.12) | AppStream `postgresql18` | 2.16 – 2.21 |

- AlmaLinux and Oracle Linux 8/9/10 work the same way (IDs `almalinux`, `ol`)
- All nodes in a cluster must run the same major version (glibc/collation must match for streaming replication)
- RHEL must be registered (`subscription-manager register`) with BaseOS + AppStream enabled
- **EL8**: ansible-core 2.17+ cannot manage Python 3.6, and EL8's dnf bindings exist only for Python 3.6, so ansible-core 2.16 is required
  (preflight checks this and tells you)
- RHEL/CentOS 7 is not supported — end of life, and no Python ≥ 3.10 for psycopg 3.3 (Rocky Linux has no version 7)

The project picks the OS-specific values automatically (`playbooks/detect_os.yml` + `inventory/group_vars/all/main.yml`).
Override them with `os_system_python`, `patroni_python_by_major`, `pg_install_source`.

## Versions (latest as of 12 Oct 2026)

| Component  | Version | Installed from | Variable |
|------------|---------|-----------|--------|
| OS packages | always latest | `dnf upgrade` + `state: latest` | `os_update_all`, `package_state` |
| PostgreSQL | **18.6** | EL8/9: PGDG · EL10: AppStream (configurable) | `pg_version`, `pg_install_source` |
| etcd | **3.7.2** | GitHub release + SHA256 check | `etcd_version` |
| Patroni | **4.1.5** | PyPI → venv `/opt/patroni` | `patroni_version` |
| psycopg | **3.3.6** (needs Python ≥ 3.10) | PyPI | `psycopg_version` |
| HAProxy | **3.4.6** (LTS) | built from source (haproxy.org + SHA256 check) | `haproxy_version` |
| keepalived | **2.4.3** | built from a Git tag + commit hash check | `keepalived_version` |
| Ansible | ansible-core 2.16 (with EL8) / 2.16–2.21 · ansible.posix ≥ 2.2.2 · community.crypto ≥ 2.22 | control node | `requirements.yml` |

PostgreSQL 19 was still in Beta at the time of writing, so 18 is used.

## Documentation

| File | Contents |
|---|---|
| [docs/INSTALL.md](docs/INSTALL.md) | Copy & paste installation from bare machines (Rocky / RHEL) |
| [docs/OPERATIONS.md](docs/OPERATIONS.md) | Health checks, Patroni checks, failover tests, upgrades, routine work |
| [docs/TLS.md](docs/TLS.md) | TLS/SSL with the cluster CA: enabling it on an existing cluster, application connection strings, certificate renewal |
| [docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md) | Common problems and fixes |
| [CHANGELOG.md](CHANGELOG.md) | Changes in each version |

## Quick start

```bash
ansible-galaxy collection install -r requirements.yml
python3 scripts/setup-inventory.py                       # asks for each node IP, subnet, VIP → writes inventory/hosts.yml
cp inventory/group_vars/all/vault.yml.example inventory/group_vars/all/vault.yml
vi inventory/group_vars/all/vault.yml && ansible-vault encrypt inventory/group_vars/all/vault.yml   # includes vault_tls_ca_passphrase
ansible-playbook playbooks/preflight.yml --ask-vault-pass   # readiness checks (no changes)
ansible-playbook site.yml --ask-vault-pass                  # install + verify
```

Clusters installed with v1.x / v2.0 (no TLS yet): run `ansible-playbook playbooks/enable-tls.yml --ask-vault-pass`
instead of `site.yml` the first time — see [docs/TLS.md](docs/TLS.md).

## Entering IPs / subnet / VIP

All site values live in one file, `inventory/hosts.yml`. The shipped file is a **template** (`<IP-node-1>`, `<VIP>` ...). There are 2 ways to fill it in.

**1. Interactive (recommended)** — validates every value, backs up the old file, then writes a new one

```bash
python3 scripts/setup-inventory.py
```

```
Number of nodes (odd, >= 3) [3]:
Node 1  host name [pg-node-1]:
Node 1  IP: 10.10.10.11
...
Cluster subnet (CIDR) [10.10.10.0/24]:
Application subnets allowed to reach the DB (comma separated) [10.10.10.0/24]: 10.10.20.0/24
Use a Virtual IP for applications [Y/n]:
VIP (an unused IP in 10.10.10.0/24): 10.10.10.10
```

Re-run it any time — current values become the defaults in brackets. Or pass everything on one line (scriptable):

```bash
python3 scripts/setup-inventory.py --ips 10.10.10.11,10.10.10.12,10.10.10.13 --vip 10.10.10.10 --yes
python3 scripts/setup-inventory.py --nodes db1=10.0.0.11,db2=10.0.0.12,db3=10.0.0.13 \
    --cidr 10.0.0.0/24 --app-cidrs 10.0.1.0/24,10.0.2.0/24 --vip 10.0.0.10 --router-id 60 --yes
python3 scripts/setup-inventory.py --help
```

**2. Edit by hand** — replace every `<...>` value in `inventory/hosts.yml`

Either way, `preflight` checks on the control node before any SSH: leftover `<...>`, malformed/duplicate IPs, IPs or the VIP outside
`cluster_network_cidr`, a VIP clashing with a node, an even number of nodes. It then checks on the hosts that each IP is really on its node and that the VIP is free.

**Only 2 files change for a new environment**

| File | Contents |
|---|---|
| `inventory/hosts.yml` | node names, IPs, `cluster_network_cidr`, `app_allowed_cidrs`, VIP (written by `scripts/setup-inventory.py`) |
| `inventory/group_vars/all/vault.yml` | all passwords (encrypted with ansible-vault) |

`inventory/group_vars/all/main.yml` holds defaults/tuning — leave it alone unless you want to tune something
(do not repeat site values there: `group_vars/all` takes precedence over the group vars in `hosts.yml`).

## Layout

```
.
├── ansible.cfg
├── requirements.yml                  # ansible.posix, community.crypto
├── site.yml                          # validate inventory → detect OS → preflight → roles → verify
├── inventory/
│   ├── hosts.yml                     # site values (template — fill in with scripts/setup-inventory.py)
│   ├── pki/                          # cluster CA (created on the first run — ca.key is encrypted, can be committed)
│   └── group_vars/all/
│       ├── main.yml                  # defaults / tuning / versions / per-EL-major values
│       └── vault.yml.example         # password template
├── playbooks/
│   ├── detect_os.yml                 # read /etc/os-release, check the OS, select the system Python (always runs)
│   ├── preflight.yml                 # validate the inventory + node readiness (read-only)
│   ├── verify.yml                    # cluster health check (read-only)
│   ├── switchover.yml                # planned switchover
│   ├── enable-tls.yml                # turn on TLS for an existing cluster without losing quorum / restarting PostgreSQL
│   └── tasks/etcd-peer-move.yml      # (used by enable-tls.yml)
├── roles/
│   ├── common/      # OS update, hostname, /etc/hosts, chrony, SELinux, firewalld (+ stale rule cleanup)
│   ├── postgresql/  # PGDG or AppStream (EL8/9 module stream, EL10 postgresqlNN), no initdb
│   ├── tls/         # CA on the control node, key/CSR on the node, signing + renewal, http↔https guard
│   ├── etcd/        # binary + checksum, config, systemd, upgrade guard
│   ├── patroni/     # venv (python3.12 on EL8/9), patroni.yml, ordered bootstrap, safe rolling restart
│   ├── haproxy/     # source build 3.4 LTS, write/read/stats
│   └── keepalived/  # source build, VIP, unicast VRRP, tracks haproxy
├── scripts/
│   ├── setup-inventory.py            # enter IPs / subnet / VIP interactively → inventory/hosts.yml
│   └── git-hooks/pre-commit          # blocks committing an unencrypted vault.yml / pki/*.key
└── docs/
```

## Changes from the original article

| Topic | Original (OL8) | This project | Why |
|---|---|---|---|
| OS | Oracle Linux 8 | Rocky Linux / RHEL 8, 9, 10 (values adjusted per major version automatically) | |
| PostgreSQL | PGDG EL-8, PG 16 | PG 18.6 — EL8/9: PGDG (AppStream module disabled), EL10: AppStream | EL8/9 AppStream lacks some PG 18 minors |
| PGDG on EL10 | — | baseurl pinned to `rhel-10-$basearch` + `priority=1` | no `$releasever_minor` → 404, and AppStream ships a same-named `postgresql18` |
| Patroni | `pip3` into the system Python | venv + pinned versions + psycopg3, Python ≥ 3.10 per OS | keeps the system Python clean; psycopg 3.3 needs 3.10+ |
| Ansible Python | auto | system Python (EL8 platform-python) set for every node | dnf/SELinux/firewalld bindings exist only for the system Python |
| etcd | 3.5.17 | 3.7.2 + SHA256 check + no minor skipping | |
| Firewall / SELinux | firewalld off, `setenforce 0` | firewalld with only the needed ports + restricted sources | safer |
| Auth | `md5`, plaintext passwords | `scram-sha-256`, Ansible Vault, separate `rewind_user` | |
| pg_hba | under `bootstrap` | under `postgresql` + reloaded right away on change | subnet changes apply without a new bootstrap |
| HAProxy check | `OPTIONS /master` | `GET /primary` | `/master` is deprecated |
| keepalived | `killall -0`, `weight 2` | `vrrp_track_process`, `weight 0` | in the original the VIP never moved when haproxy died (150 > 140+2) |
| keepalived auth | `patroni_vip` (11 chars) | checked ≤ 8 chars | VRRP PASS silently truncates the rest |
| VRRP | multicast, `eth0` | unicast, the interface holding the node IP | VMs/clouds often block multicast; RHEL uses `ens*` |
| IPs / VIP | hard-coded in configs | entered through `scripts/setup-inventory.py` + checked before SSH | no site values in the code |
| Pre-install checks | — | `preflight.yml`: inventory, OS, repos, subnet, free VIP, internet | catches problems before changing anything |
| Encryption | none (http / plaintext everywhere) | TLS on every channel with a cluster CA, etcd mutual TLS, `sslmode=verify-full` | protects passwords and data on the network |

## Git workflow

### Publishing to GitHub / GitLab

```bash
git remote add origin git@github.com:<org>/pg-ha-ansible.git     # or git@gitlab.com:<group>/pg-ha-ansible.git
git push -u origin main --tags
```

- CI runs yamllint, ansible-lint and syntax-check on every push: `.github/workflows/lint.yml` (GitHub) and `.gitlab-ci.yml` (GitLab)
- The repository contains **no** site values or secrets: `inventory/hosts.yml` is a template, and there is no `vault.yml` / `inventory/pki/`
- Private repository: you can commit the site's `vault.yml` (encrypted) and `inventory/pki/` — the pre-commit hook blocks unencrypted files
- Public repository: uncomment the last lines of `.gitignore` so the site's vault and CA are never pushed, and keep them elsewhere (or in a separate private inventory repository)

### General

- `vault.yml` may only be committed encrypted — enable the hook with `git config core.hooksPath scripts/git-hooks`
- Before pushing: `ansible-lint && ansible-playbook site.yml --syntax-check`
- Before changing a running cluster: `ansible-playbook site.yml --ask-vault-pass --check --diff`
- Several environments: copy the whole `inventory/` folder to `inventories/uat/` (group_vars must sit next to hosts.yml),
  then `python3 scripts/setup-inventory.py -o inventories/uat/hosts.yml` and run with `-i inventories/uat/hosts.yml`
