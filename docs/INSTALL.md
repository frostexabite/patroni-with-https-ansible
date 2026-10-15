# Installing the PostgreSQL HA Cluster on Rocky Linux / RHEL (copy & paste)

Supports Rocky Linux / RHEL **8 (8.10), 9 (9.4+), 10** — every node must run the same major version.

## Your values

Prepare these before you start (values in brackets are only examples).

| | Value |
|---|---|
| nodes 1–3 | name + IP, e.g. `pg-node-1` (10.10.10.11), `pg-node-2` (10.10.10.12), `pg-node-3` (10.10.10.13) |
| cluster subnet | the subnet all nodes live in, e.g. `10.10.10.0/24` |
| app subnet | the subnet of the applications that connect to the database, e.g. `10.10.20.0/24` |
| VIP | an unused IP in the cluster subnet, e.g. `10.10.10.10` |
| SSH user | `ansible` (passwordless sudo) |

The commands below use shell variables — **set them once per terminal**, then copy the commands as they are:

```bash
NODE1_IP=10.10.10.11      # ← change to your real IPs
NODE2_IP=10.10.10.12
NODE3_IP=10.10.10.13
VIP=10.10.10.10
```

---

## Part A — every node (run as root on each machine)

Install Rocky Linux or RHEL (Minimal is enough) and configure a static IP.

### A1. (RHEL only) register and enable the repositories

```bash
subscription-manager register
```
```bash
subscription-manager repos --enable "rhel-$(rpm -E %rhel)-for-$(uname -m)-baseos-rpms" --enable "rhel-$(rpm -E %rhel)-for-$(uname -m)-appstream-rpms"
```

Rocky Linux does not need this step.

### A2. Create the `ansible` user

```bash
useradd -m ansible
```
```bash
passwd ansible
```
```bash
echo 'ansible ALL=(ALL) NOPASSWD: ALL' > /etc/sudoers.d/ansible
```
```bash
chmod 440 /etc/sudoers.d/ansible
```

### A3. Check the OS and repositories

```bash
cat /etc/os-release | grep -E '^(NAME|VERSION_ID)='
```
```bash
dnf repolist
```

BaseOS and AppStream must be listed.

| OS | Repositories you should see |
|---|---|
| Rocky Linux | `baseos`, `appstream` |
| RHEL | `rhel-<8/9/10>-for-x86_64-baseos-rpms`, `rhel-<8/9/10>-for-x86_64-appstream-rpms` |

EL8 must be 8.10 and EL9 must be 9.4 or newer (`python3.12` in AppStream) — if older, run `dnf upgrade -y && reboot` first.

> Do not install `postgresql-server` or run `initdb` yourself — Patroni creates the data directory.

---

## Part B — Control node (the machine that runs Ansible)

The control node can be one of the cluster nodes (e.g. node 1) or a separate machine.
The user that runs Ansible on it does not need passwordless sudo.

### B1. Install Ansible

Rocky / RHEL 8 or 9:

```bash
sudo dnf install -y git python3.12 python3.12-pip openssh-clients unzip
```
```bash
python3.12 -m venv ~/ansible-venv
```

Rocky / RHEL 10:

```bash
sudo dnf install -y git python3 python3-pip openssh-clients unzip
```
```bash
python3 -m venv ~/ansible-venv
```

Then, on any version:

```bash
source ~/ansible-venv/bin/activate
```
```bash
pip install -U pip
```

Choose the ansible-core version by the OS of the **cluster nodes** (not of the control node):

| Cluster nodes | Command |
|---|---|
| any EL8 (Rocky/RHEL 8) | `pip install 'ansible-core>=2.16,<2.17'` |
| EL9 or EL10 only | `pip install ansible-core ansible-lint` |

```bash
ansible --version
```

> Every new terminal needs `source ~/ansible-venv/bin/activate` first.
>
> Why EL8 needs 2.16: the Python with the dnf/SELinux/firewalld bindings on EL8 is platform-python 3.6,
> which ansible-core 2.17+ no longer supports — preflight stops and explains the fix if the version is wrong.

### B2. SSH key to every node

```bash
ssh-keygen -t ed25519 -f ~/.ssh/id_ed25519 -N ""
```
```bash
for ip in $NODE1_IP $NODE2_IP $NODE3_IP; do ssh-copy-id ansible@$ip; done
```

Include the control node itself if it is also a cluster node.

### B3. Prepare the project

```bash
cd ~ && git clone <repository-url> pg-ha-ansible      # or unzip the release zip
```
```bash
cd ~/pg-ha-ansible
```
```bash
git config core.hooksPath scripts/git-hooks
```
```bash
ansible-galaxy collection install -r requirements.yml -p ~/.ansible/collections
```
```bash
ansible-galaxy collection list ansible.posix community.crypto
```

With EL8 nodes (ansible-core 2.16), use community.crypto 2.x:

```bash
ansible-galaxy collection install 'community.crypto:>=2.22.0,<3.0.0' -p ~/.ansible/collections
```

If `ansible-galaxy` cannot reach galaxy.ansible.com, use `pip install -U ansible` instead (it bundles ansible.posix and community.crypto — with EL8 nodes use `pip install 'ansible>=9,<10'`, which ships ansible-core 2.16).

### B4. Enter IPs / subnet / VIP

```bash
python3 scripts/setup-inventory.py
```

Answer one question at a time (Enter = the value in brackets). The script checks the IPs, duplicates, subnet and VIP, then writes `inventory/hosts.yml`.
Or pass everything on one line from the variables above:

```bash
python3 scripts/setup-inventory.py --ips $NODE1_IP,$NODE2_IP,$NODE3_IP --vip $VIP --app-cidrs 10.10.20.0/24 --yes
```

Check the result:

```bash
cat inventory/hosts.yml
```

To change it later: re-run the script (current values become defaults) or edit the file directly — the old file is backed up as `hosts.yml.bak-<timestamp>`.

> If another keepalived/VRRP group already runs on the same LAN, pick a different `--router-id` (default 51).

### B5. Passwords (Vault)

Change the passwords before pasting — `vault_keepalived_auth_pass` is at most 8 characters,
`vault_tls_ca_passphrase` is at least 8 characters (it encrypts the CA key in `inventory/pki/ca.key` — **write it down**, certificate renewal needs it).

```bash
cat > ~/pg-ha-ansible/inventory/group_vars/all/vault.yml <<'EOF'
---
vault_pg_superuser_password: "Super#Pass2026"
vault_pg_replication_password: "Repl#Pass2026"
vault_pg_rewind_password: "Rewind#Pass2026"
vault_patroni_restapi_password: "Api#Pass2026"
vault_haproxy_stats_password: "Stats#Pass2026"
vault_keepalived_auth_pass: "Vrrp2026"
vault_tls_ca_passphrase: "CA-Key#Pass2026-ChangeMe"
EOF
```
```bash
ansible-vault encrypt ~/pg-ha-ansible/inventory/group_vars/all/vault.yml
```

> Every `ansible` / `ansible-playbook` command needs `--ask-vault-pass` (even `-m ping`).

### B6. Test the connection

```bash
ansible pg_cluster -m ping --ask-vault-pass
```
```bash
ansible pg_cluster -m command -a "cat /etc/os-release" --ask-vault-pass | grep -E 'SUCCESS|PRETTY_NAME'
```

### B7. Readiness checks (no changes made)

```bash
ansible-playbook playbooks/preflight.yml --ask-vault-pass
```

Checks: the values in `hosts.yml` (IP/subnet/VIP), the number of nodes, all passwords present, OS + same major version on every node,
the ansible-core version, `python3.12` in the repositories (EL8/9), each IP really on its node, the VIP is free,
internet access to the download sources, time sync, and that the control node can sign certificates — and finally prints each node's plan, e.g.

```
Rocky 9.6 | IP 10.10.10.11 | VRRP on ens192 | Patroni Python /usr/bin/python3.12 | PostgreSQL 18 from pgdg (/usr/pgsql-18/bin)
```

### B8. Install

```bash
ansible-playbook site.yml --ask-vault-pass
```

Takes about 15–30 minutes (HAProxy/keepalived are built from source) and ends with an automatic verify.
Expected result: `failed=0` on every node.

### B9. Commit to Git

```bash
git add -A && git commit -m "Deploy cluster $NODE1_IP,$NODE2_IP,$NODE3_IP VIP $VIP"
```

`git add -A` includes `inventory/pki/` (the cluster CA) on purpose: `ca.key` is encrypted,
and the pre-commit hook refuses unencrypted keys. For a public repository see the Git workflow section in the README.

---

## Part C — After installation

```bash
ansible-playbook playbooks/verify.yml --ask-vault-pass
```

You should see `Leader pg-node-1, replicas streaming: pg-node-2, pg-node-3` (with your node names),
the write port returning `recovery=false ssl=true`, the read port `recovery=true ssl=true`, `Replication over TLS: 2/2`, and the VIP on exactly 1 node.

Detailed checks and failover tests: [OPERATIONS.md](OPERATIONS.md)

## Application connection

| | Value |
|---|---|
| Host | your VIP (`$VIP`) |
| Write (primary) | port `5000` |
| Read (replicas, round-robin) | port `5001` |
| TLS | `sslmode=verify-full sslrootcert=ca.crt` — give `inventory/pki/ca.crt` to the application teams |
| HAProxy stats | `https://<VIP>:7000` (user `admin`) |

Example:

```bash
psql "host=$VIP port=5000 dbname=postgres user=postgres sslmode=verify-full sslrootcert=inventory/pki/ca.crt"
```

JDBC / Python / .NET and enforcing SSL for every connection: [TLS.md](TLS.md)
