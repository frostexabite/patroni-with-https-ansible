# Operations

The commands here use shell variables — set them to match `inventory/hosts.yml` first (once per terminal):

```bash
NODE1_IP=10.10.10.11      # ← IP of node 1 (see inventory/hosts.yml)
NODE2_IP=10.10.10.12
NODE3_IP=10.10.10.13
VIP=10.10.10.10
```

The examples use the node names `pg-node-1..3` (the `scripts/setup-inventory.py` default) — replace them with yours.
Commands starting with `ansible` run on the control node inside `~/pg-ha-ansible` (after `source ~/ansible-venv/bin/activate`).

`psql` is on the PATH for both PGDG and AppStream installs — if not, use `/usr/pgsql-18/bin/psql` (PGDG).

After installation every node has helpers (log in again once to load them):
- `pctl` = `sudo patronictl -c /etc/patroni/patroni.yml`, e.g. `pctl list`, `pctl history`
- `ectl` = `sudo etcdctl` with the endpoints and client certificate (TLS), e.g. `ectl endpoint health`, `ectl member list -w table`
  (with `tls_enabled: false` plain `etcdctl` works too — `ETCDCTL_ENDPOINTS` is already set)

TLS (`tls_enabled: true`, the default): the REST API / etcd / stats page use https, so the `curl` commands below use
`--cacert /etc/pki/pgha/ca.crt` (on the control node: `inventory/pki/ca.crt`). Details: [TLS.md](TLS.md)

For `psql` through the VIP, set the TLS options once per terminal:

```bash
export PGSSLMODE=verify-full PGSSLROOTCERT=/etc/pki/pgha/ca.crt
```

---

## 1. Whole-cluster health check

```bash
ansible-playbook playbooks/verify.yml --ask-vault-pass
```

Checks: all 4 services running on every node, etcd healthy, exactly 1 Leader, every replica `streaming`,
no maintenance mode, write port → primary, read port → replica, connections and replication encrypted, VIP on 1 node.

## 2. Patroni

### 2.1 Cluster state

```bash
pctl list
```

| Look at | Normal | Abnormal |
|---|---|---|
| Leader | exactly 1 | 0 or >1 → check etcd |
| Replica State | `streaming` | stuck in `starting` / `stopped` / `creating replica` → check the logs |
| TL (timeline) | the same on every node | different → a replica has not caught up after a failover |
| Lag | 0 or close to 0 | keeps growing → the replica cannot keep up |
| Header | no `Maintenance mode` | present → `pctl resume` |

Refresh every 2 seconds: `pctl list -W` (exit with Ctrl+C)

### 2.2 Service and logs

```bash
sudo systemctl status patroni --no-pager
```
```bash
sudo journalctl -u patroni -n 30 --no-pager
```
```bash
sudo journalctl -u patroni --since "1 hour ago" --no-pager | grep -iE "error|warning|fail"
```

Normal log lines every 10 seconds:
- Leader: `no action. I am (pg-node-1), the leader with the lock`
- Replica: `no action. I am (pg-node-2), a secondary, and following a leader (pg-node-1)`

### 2.3 REST API (what HAProxy uses)

```bash
curl -s --cacert /etc/pki/pgha/ca.crt https://$NODE1_IP:8008/cluster | python3 -m json.tool
```
```bash
curl -s --cacert /etc/pki/pgha/ca.crt -o /dev/null -w "%{http_code}\n" https://$NODE1_IP:8008/primary
```
```bash
curl -s --cacert /etc/pki/pgha/ca.crt -o /dev/null -w "%{http_code}\n" https://$NODE2_IP:8008/replica
```
```bash
curl -s --cacert /etc/pki/pgha/ca.crt https://$NODE2_IP:8008/patroni | python3 -m json.tool
```

(`tls_enabled: false` → use `http://` without `--cacert`)

`/primary` = 200 only on the Leader, `/replica` = 200 only on a healthy replica

### 2.4 Replication inside PostgreSQL (on the Leader)

```bash
sudo -u postgres psql -c "SELECT application_name, client_addr, state, sync_state, write_lag, replay_lag FROM pg_stat_replication;"
```
```bash
sudo -u postgres psql -c "SELECT slot_name, active, restart_lsn FROM pg_replication_slots;"
```
```bash
sudo -u postgres psql -c "SELECT r.application_name, s.ssl, s.version FROM pg_stat_replication r JOIN pg_stat_ssl s USING (pid);"
```

Expect 2 `streaming` rows, both slots `active = t`, and `ssl = t` for both replicas.

### 2.5 Configuration and history

```bash
pctl show-config
```
```bash
pctl history
```
```bash
pctl list -e
```

`list -e` shows the `Pending restart` column — a value means a restart is needed for a new parameter to take effect.

## 3. etcd / HAProxy / VIP

```bash
ectl endpoint health --cluster -w table
```
```bash
ectl endpoint status --cluster -w table
```
```bash
ectl member list -w table
```
```bash
ip -brief addr show to $VIP
```

HAProxy stats: `https://<VIP>:7000` (user `admin`, password `vault_haproxy_stats_password` — browsers warn about the CA until `ca.crt` is imported)
- `postgres_write`: UP only for the Leader
- `postgres_read`: UP only for replicas

## 4. Real replication test

```bash
psql -h $VIP -p 5000 -U postgres -c "CREATE TABLE ha_test(id int, note text); INSERT INTO ha_test VALUES (1, 'hello from leader');"
```
```bash
psql -h $VIP -p 5001 -U postgres -c "SELECT inet_server_addr(), * FROM ha_test;"
```
```bash
psql -h $VIP -p 5000 -U postgres -c "DROP TABLE ha_test;"
```

## 5. Failover tests (do these before going live)

### 5.1 Planned switchover

```bash
ansible-playbook playbooks/switchover.yml -e switchover_candidate=pg-node-2 --ask-vault-pass
```
```bash
psql -h $VIP -p 5000 -U postgres -Atc "SELECT inet_server_addr(), pg_is_in_recovery();"
```

Expect node 2's IP followed by `|f`.

### 5.2 Automatic failover (simulated Leader crash) — on the current Leader

```bash
sudo systemctl stop patroni
```

Wait ~30 seconds, then on another node:

```bash
pctl list
```

There must be a new Leader. Then back on the original machine:

```bash
sudo systemctl start patroni
```

The old node rejoins as a Replica by itself (using pg_rewind when needed).

### 5.3 VIP failover — on the node holding the VIP

```bash
sudo systemctl stop haproxy
```
```bash
ip -brief addr show to $VIP
```

The VIP must leave this machine and move to the next node. Then restore:

```bash
sudo systemctl start haproxy
```

## 6. Changes / upgrades

| Task | How |
|---|---|
| Preview what would change | `ansible-playbook site.yml --ask-vault-pass --check --diff` |
| PostgreSQL parameter | `pctl edit-config`, then `pctl restart postgres-cluster` if a restart is needed (`pg_dcs_parameters` is used only at the first bootstrap) |
| subnet / pg_hba / app subnet / VIP | `python3 scripts/setup-inventory.py` (or edit `inventory/hosts.yml`) → `ansible-playbook site.yml --ask-vault-pass` (Patroni reloads right away, old firewall rules are removed) |
| PostgreSQL minor | `package_state: latest` updates the RPMs → `pctl restart postgres-cluster pg-node-2 --force` one replica at a time, then switchover, then restart the old leader |
| Patroni / psycopg | change `patroni_version` / `psycopg_version` → run `--tags patroni` (pause → restart one node at a time → resume, no failover) |
| etcd | change `etcd_version` → `--tags etcd` (restart one node at a time, blocked if a minor is skipped) — snapshot first: `ectl snapshot save /root/etcd-$(date +%F).db` |
| HAProxy | change `haproxy_version` → `--tags haproxy` (rebuild + reload without dropping connections) |
| keepalived | change `keepalived_version` and `keepalived_git_commit` → `--tags keepalived` |
| OS minor (e.g. 9.5 → 9.6) | `os_update_all: true` updates during `site.yml` → reboot one node at a time as in the last row |
| OS major (8 → 9 → 10) | not in place — build a new cluster on the new major and migrate the data (all nodes must share one major) |
| TLS: enable on an existing cluster / renew certificates / enforce SSL | see [TLS.md](TLS.md) — `playbooks/enable-tls.yml`, `site.yml --tags tls`, `tls_postgres_enforce` |
| OS reboot | one node at a time starting with replicas: switch the Leader away first → `sudo reboot` → wait until `pctl list` shows streaming again → next node |

Finding the latest versions:

```bash
pip index versions patroni
```
```bash
git ls-remote --tags https://github.com/etcd-io/etcd.git 'v3.*' | grep -v '\^{}' | awk -F/ '{print $3}' | grep -vE 'rc|alpha|beta' | sort -V | tail -1
```
```bash
git ls-remote --tags https://github.com/acassen/keepalived.git | grep -v '\^{}' | awk -F/ '{print $3}' | sort -V | tail -1
```

HAProxy: check the latest LTS branch at https://www.haproxy.org/
