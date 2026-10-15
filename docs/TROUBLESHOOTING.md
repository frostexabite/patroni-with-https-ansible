# Troubleshooting

## Messages from preflight / playbooks and their fixes

| Message | Cause | Fix |
|---|---|---|
| `... is not filled in (value: '<IP-node-1>')` | `inventory/hosts.yml` is still the template | `python3 scripts/setup-inventory.py`, or replace the `<...>` values in the file |
| `... is outside cluster_network_cidr` / `already used by` / `not an IPv4` | IP/subnet/VIP values do not fit together | re-run `scripts/setup-inventory.py` (it checks every value) |
| `runs "CentOS Stream 9" ... Supported: rocky, rhel, almalinux, ol` | the OS is not on the supported list | use Rocky/RHEL 8–10, or `-e allow_unsupported_os=true` (values follow the major version) |
| `Mixed EL major versions` | nodes run different majors (e.g. 9 and 10) | install every node with the same major (glibc/collation must match for replication) |
| `EL8 nodes need ansible-core 2.16` | ansible-core ≥ 2.17 cannot manage Python 3.6 (EL8 platform-python) | on the control node: `pip install 'ansible-core>=2.16,<2.17'` |
| `enabled repositories do not provide python3.12` | RHEL not registered / EL8 < 8.10 / EL9 < 9.4 | `subscription-manager register` + enable BaseOS/AppStream, or `dnf upgrade -y && reboot`, or set `patroni_python_by_major` to `python3.11` |
| `This system is not registered with an entitlement server` | RHEL not registered | `subscription-manager register`, then `subscription-manager repos --list-enabled` |
| `AppStream ... has no postgresql:18 module stream` | `pg_install_source: appstream` on an EL8/9 without that stream | use `pg_install_source: auto` (= pgdg on EL8/9) |
| `Could not install postgresql18 ... from appstream` | an older EL10 minor has no `postgresql18` in AppStream yet | `dnf upgrade` to the latest minor, or `pg_install_source: pgdg` |
| `No match for argument: postgresql18-server` (PGDG on EL8/9) | the AppStream `postgresql` module hides the PGDG packages | the project disables the module — if it still happens: `sudo dnf -qy module disable postgresql` |
| `Failed to download metadata for repo 'pgdg...'` 404 `rhel-10.-x86_64` | EL10 has no `$releasever_minor`, which breaks the PGDG URL | fixed (pinned to `rhel-10-$basearch`) — check `pgdg_pin_major_baseurl` |
| `firewalld and its python module are required` / `Failed to import the required Python library (libselinux-python)` | Ansible uses the wrong Python (e.g. python3.12) instead of the system Python | do not set `ansible_python_interpreter` to python3.12 — let `detect_os.yml` choose (EL8 `/usr/libexec/platform-python`, EL9/10 `/usr/bin/python3`) |
| `Require Python >= 3.10 for Patroni` | `patroni_python` points at an old Python | change `patroni_python_by_major` in `group_vars/all/main.yml` |
| `uses Python 3.11 but patroni_python ... is 3.12` | Patroni's Python was changed on a running cluster | follow the steps in the error (pause → stop → remove the venv → `--tags patroni` → resume) |
| `EL8 AppStream ships HAProxy 1.8` | `haproxy_install_method: package` on EL8 | use `source` (the default) on EL8 |
| `Attempting to decrypt but no vault secrets found` | `vault.yml` is encrypted — every ansible command needs the vault password | add `--ask-vault-pass` or set `vault_password_file` in `ansible.cfg` (see below) |
| `couldn't resolve module/action 'ansible.posix.selinux'` / `'community.crypto...'` | collections not installed | `ansible-galaxy collection install -r requirements.yml -p ~/.ansible/collections` |
| `sudo: a password is required` on `localhost` | releases before v2.1.1 used sudo on the control node | update to v2.1.1 or newer (the control node needs no sudo) |
| `'ansible_managed' is undefined` | releases before v2.1.2 on ansible-core 2.19+ | update to v2.1.2 or newer |
| verify: `Not running on <node>: etcd, patroni, haproxy, keepalived` while everything runs | releases before v2.1.3 on ansible-core 2.19+ (regex escaping) | update to v2.1.3 or newer |
| Patroni validate: `Directory '/usr/pgsql-18/bin' does not exist` | packages came from the other source (AppStream binaries live in `/usr/bin`) | never change `pg_install_source` on an installed cluster — `dnf info --installed postgresql18-server \| grep 'From repo'` |
| Replica loops on `no pg_hba.conf entry for replication connection from host "x.x.x.x"` | `cluster_network_cidr` does not cover the node's real IP | preflight checks this before installing — fix with `scripts/setup-inventory.py` and re-run `site.yml` |
| After a subnet change the Leader still uses the old `pg_hba.conf` | the reload was deferred to the end of the play | fixed (`flush_handlers` before waiting for replicas) — `sudo systemctl reload patroni` on the Leader |
| Values lost after unzipping a new release over the old one | `hosts.yml` was replaced by the template | back up `inventory/hosts.yml`, `vault.yml` and `inventory/pki/` before updating, or use git — `scripts/setup-inventory.py` always backs up the old file as `.bak-<timestamp>` |
| Changing the VIP/router id in `group_vars/all/main.yml` or in `hosts.yml` has no effect | the site value is declared twice (`group_vars/all` beats the group vars in `hosts.yml`) | put site values in `inventory/hosts.yml` only |

## Vault password file (no need to type `--ask-vault-pass`)

Replace `your-vault-password` with the real password.

```bash
echo 'your-vault-password' > ~/.vault_pass_pgha
```
```bash
chmod 600 ~/.vault_pass_pgha
```
```bash
cd ~/pg-ha-ansible && sed -i 's|^# vault_password_file = .*|vault_password_file = ~/.vault_pass_pgha|' ansible.cfg
```

## By symptom

| Symptom | Check |
|---|---|
| preflight: `NOT inside` / `outside cluster_network_cidr` | `ansible pg_cluster -m command -a "ip -brief addr show scope global" --ask-vault-pass`, then fix with `scripts/setup-inventory.py` |
| preflight: `is not an address of <node>` | `ansible_host` in `hosts.yml` is not the node's cluster-network IP (e.g. a NAT/management IP) |
| preflight: `keepalived interface ... not found` | set `keepalived_interface: <name>` in `hosts.yml` (names from `ip -brief link`) |
| preflight: `Another device already uses <VIP>` | choose a new VIP with `scripts/setup-inventory.py` |
| preflight: `cannot reach: https://...` | proxy (`/etc/dnf/dnf.conf`, environment), outbound firewall, DNS, SSL inspection (CA in `/etc/pki/ca-trust/source/anchors/` + `update-ca-trust`) |
| etcd not healthy | `sudo journalctl -u etcd -n 50`, ports 2379/2380, clocks in sync (`chronyc tracking`) |
| replica stuck in `creating replica` | `sudo journalctl -u patroni -f` on the replica; raise `patroni_replica_wait_retries` for large databases |
| replica has leftover files in its data dir | only on the broken replica (never on the Leader): `sudo systemctl stop patroni` → `sudo rm -rf /var/lib/pgsql/18/data/*` → `sudo systemctl start patroni` |
| every HAProxy server DOWN | `curl -s --cacert /etc/pki/pgha/ca.crt -o /dev/null -w '%{http_code}' https://<node>:8008/primary` (without TLS: `http://`), `getsebool haproxy_connect_any` |
| cannot log in to the HAProxy stats page with the right password (v2.0 and older) | the password contains `#` → HAProxy dropped everything after it (comment) — v2.1 quotes it, run `--tags haproxy` |
| TLS / certificate problems | see the table at the end of [TLS.md](TLS.md) |
| VIP does not come up | `sudo journalctl -u keepalived -n 50`, a `virtual_router_id` clash with another system on the L2 (change it with `scripts/setup-inventory.py`), switches blocking gratuitous ARP / VRRP; cloud VMs cannot use this kind of VIP |
| `Maintenance mode` stuck in `pctl list` | `pctl resume` |
| SELinux enforcing wanted | set `selinux_state: enforcing`, test in UAT first, then check `sudo ausearch -m avc -ts recent` |
| which Python Ansible uses | the `Detected OS` line at the start of a playbook, e.g. `Rocky Linux 8.10 → Ansible Python /usr/libexec/platform-python` |

## Logs worth reading

| Component | Command |
|---|---|
| Patroni | `sudo journalctl -u patroni -n 50 --no-pager` |
| PostgreSQL | `sudo tail -50 /var/lib/pgsql/18/data/log/*.log` |
| etcd | `sudo journalctl -u etcd -n 50 --no-pager` |
| HAProxy | `sudo journalctl -u haproxy -n 50 --no-pager` |
| keepalived | `sudo journalctl -u keepalived -n 50 --no-pager` |
| dnf (package install fails) | `sudo dnf repolist -v`, `sudo dnf module list postgresql` (EL8/9) |
