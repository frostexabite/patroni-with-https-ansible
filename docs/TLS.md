# TLS / SSL with a cluster-owned CA

Since v2.1.0, with `tls_enabled: true` (the default) every channel is encrypted with certificates issued by **the cluster's own CA**.
No purchased certificates and no corporate PKI are needed.

| Channel | Port | Before | With TLS |
|---|---|---|---|
| App → VIP → HAProxy → PostgreSQL | 5000 / 5001 | plaintext | TLS terminates at PostgreSQL (HAProxy only passes TCP through) — `sslmode=verify-full` works |
| Replication / pg_rewind between nodes | 5432 | plaintext | TLS + `sslmode=verify-full` |
| Patroni REST API + HAProxy health checks | 8008 | http | https (`check-ssl verify required`) |
| etcd client (Patroni, etcdctl) | 2379 | http | https + **client certificate** (mutual TLS) |
| etcd peer | 2380 | http | https + mutual TLS |
| HAProxy stats page | 7000 | http | https |

## CA and certificate files

```
control node                                  each node (/etc/pki/pgha)
inventory/pki/ca.crt   ── copy ──────────────▶ ca.crt          0644 root
inventory/pki/ca.key   (encrypted with          node.crt        0644 root      ← signed by ca.key
                        vault_tls_ca_passphrase) node.key        0600 root      ← created on the node, never leaves it
inventory/pki/ca.csr                            postgres.key    0600 postgres  (copy for PostgreSQL/Patroni)
                                                etcd.key        0600 etcd      (copy for etcd)
                                                haproxy.pem     0600 root      (cert + key for the stats page)
```

- The **CA** is created on the first playbook run — RSA 3072, valid for 10 years (`tls_ca_valid_days`)
- Each **node private key** is created on the node itself; only the CSR goes to the control node for signing → keys never cross the network
- **Node certificates** are valid for ~2 years (`tls_cert_valid_days: 825`) — the SAN holds the name + IP of **every node** + the **VIP** + `tls_extra_sans`,
  because an application connecting through the VIP or any node's HAProxy may land on PostgreSQL on any node
- Minimum TLS version 1.2 (`tls_min_protocol`)
- Node private keys have no passphrase (services must read them unattended at start); they are protected by 0600 file permissions

> **Keep `inventory/pki/` safe** — it can be committed to a private Git repository (ca.key is encrypted and the pre-commit hook blocks unencrypted keys).
> If `ca.key` or `vault_tls_ca_passphrase` is lost, certificates can no longer be renewed and a new CA is needed (applications must switch to the new ca.crt).
> The playbook **refuses** to create a new CA while the nodes still trust the old one (protects against running from a machine without pki/ and replacing every certificate).

## New cluster (TLS from the start)

1. Install the collections (now including `community.crypto`):

   ```bash
   ansible-galaxy collection install -r requirements.yml
   ```

   With EL8 nodes (ansible-core 2.16), use community.crypto 2.x:

   ```bash
   ansible-galaxy collection install 'community.crypto:>=2.22.0,<3.0.0'
   ```

2. Add the CA passphrase to the vault (at least 8 characters — **write it down**):

   ```bash
   ansible-vault edit inventory/group_vars/all/vault.yml
   ```
   ```yaml
   vault_tls_ca_passphrase: "a long passphrase here"
   ```

3. (If applications connect by DNS name) add the name in `inventory/group_vars/all/main.yml`:

   ```yaml
   tls_extra_sans: ["DNS:pgdb.example.local"]
   ```

4. Install as usual:

   ```bash
   ansible-playbook site.yml --ask-vault-pass
   ```

## Enabling TLS on an existing cluster (v1.x / v2.0 without TLS)

**Do not run `site.yml` directly** — the `tls` role stops and points to the playbook below, because switching the etcd peers to https
on every node at once would lose quorum, and HAProxy would not see the primary while Patroni has not switched yet.

Do steps 1–3 above first, then:

```bash
ansible-playbook playbooks/enable-tls.yml --ask-vault-pass
```

What the playbook does (safe to re-run — finished steps are skipped):

| Step | What | Impact |
|---|---|---|
| 1 | Create the CA + key/certificate for every node | none |
| 2 | etcd: clients served over both http and https on the same port, peers open https on the temporary port 2381 → restart one node at a time → move peer URLs to `https://<ip>:2381` | none (quorum kept throughout) |
| 3 | etcd: peers open https on 2380 → restart one node at a time → move peer URLs back to `https://<ip>:2380` | none |
| 4a | PostgreSQL `ssl=on` through a Patroni reload (no restart) | none |
| 4b | Pause Patroni → new patroni.yml (etcd https, REST API https, internal sslmode) → restart all Patroni daemons together → reload HAProxy with https health checks immediately | PostgreSQL is **not** restarted; HAProxy keeps server state across the reload |
| 5 | etcd drops http (one node at a time) → resume Patroni → close port 2381 → verify | none |

Test on a 3-node cluster (etcd 3.7.2, Patroni 4.1.5, HAProxy 3.4, PostgreSQL 16) with an INSERT through the VIP every 0.3 seconds during the whole migration:
**308/308 inserts succeeded, none failed** — a maintenance window is still recommended.

Afterwards `site.yml` runs as usual (nothing else changes):

```bash
ansible-playbook site.yml --ask-vault-pass --check --diff
```

## Connecting applications over TLS

Give `inventory/pki/ca.crt` to the application teams (it is a public certificate, not a secret).

| Client | Connection string |
|---|---|
| psql / libpq | `psql "host=<VIP> port=5000 dbname=app user=app sslmode=verify-full sslrootcert=ca.crt"` |
| JDBC | `jdbc:postgresql://<VIP>:5000/app?ssl=true&sslmode=verify-full&sslrootcert=/path/ca.crt` |
| Python (psycopg) | `psycopg.connect(host="<VIP>", port=5000, dbname="app", user="app", sslmode="verify-full", sslrootcert="ca.crt")` |
| .NET (Npgsql) | `Host=<VIP>;Port=5000;Database=app;Username=app;SSL Mode=VerifyFull;Root Certificate=ca.crt` |

- `verify-full` = encrypted + certificate signed by the cluster CA + the host name/IP matches the SAN (prevents man-in-the-middle)
- A DNS name used by applications must be listed in `tls_extra_sans`
- Check that a connection is really encrypted:

  ```sql
  select ssl, version, cipher from pg_stat_ssl where pid = pg_backend_pid();
  ```

### Requiring SSL for every connection

The default `tls_postgres_enforce: false` keeps accepting unencrypted connections so applications can move over one at a time.
Once every application uses SSL:

```yaml
# inventory/group_vars/all/main.yml
tls_postgres_enforce: true
```
```bash
ansible-playbook site.yml --ask-vault-pass --tags patroni
```

pg_hba switches to `hostssl` + `hostnossl ... reject` (Patroni reloads, no restart) — non-SSL clients get
`pg_hba.conf rejects connection ... no encryption`.

## Day-to-day

```bash
# etcd (needs the node client certificate → use the ectl alias, which runs through sudo)
ectl member list -w table
ectl endpoint health --cluster -w table
ectl endpoint status --cluster -w table

# Patroni REST API
curl -s --cacert /etc/pki/pgha/ca.crt https://$NODE1_IP:8008/cluster | python3 -m json.tool

# HAProxy stats
https://<VIP>:7000/      (browsers warn about the CA until ca.crt is imported)

# Certificate details and expiry
sudo openssl x509 -in /etc/pki/pgha/node.crt -noout -subject -enddate -ext subjectAltName
openssl verify -CAfile /etc/pki/pgha/ca.crt /etc/pki/pgha/node.crt

# Certificate a service actually presents (must end with "Verify return code: 0 (ok)")
openssl s_client -connect <VIP>:5000 -starttls postgres -CAfile /etc/pki/pgha/ca.crt </dev/null 2>/dev/null | grep -E "subject=|Verify return"

# Check the CA passphrase (prints "Key is valid")
read -rsp 'CA passphrase: ' CAPW; echo; export CAPW
openssl pkey -in inventory/pki/ca.key -passin env:CAPW -noout -check; unset CAPW
```

## Certificate renewal

Nothing special to do: every run of `site.yml` (or `--tags tls`) issues a new node certificate when fewer than `tls_cert_renew_days` (30) days remain,
then reloads PostgreSQL / Patroni / HAProxy (etcd re-reads certificates on every connection) — no restarts.

```bash
ansible-playbook site.yml --ask-vault-pass --tags tls
```

Run it monthly, or raise `tls_cert_renew_days` if you run it less often.

Certificates are also re-issued automatically when the SAN changes (node added, VIP changed, `tls_extra_sans` edited) or the CA changes.

## Replacing the CA (CA lost or leaked)

1. Warn the application teams — they must switch `ca.crt` at the same time
2. Move the old `inventory/pki/` away, then run `ansible-playbook site.yml --ask-vault-pass --tags tls -e tls_allow_ca_change=true`
3. Hand out the new `inventory/pki/ca.crt`

While the nodes receive certificates from the new CA at slightly different times, etcd/Patroni traffic between nodes can fail briefly → use a maintenance window
and run `pctl pause` first.

## Variables

| Variable | Default | Meaning |
|---|---|---|
| `tls_enabled` | `true` | turn TLS on/off everywhere |
| `tls_postgres_enforce` | `false` | `true` = PostgreSQL accepts SSL only |
| `tls_extra_sans` | `[]` | extra certificate names, e.g. `DNS:pgdb.example.local` |
| `tls_cert_valid_days` / `tls_cert_renew_days` | 825 / 30 | node certificate lifetime / renew when fewer days remain |
| `tls_ca_valid_days` | 3650 | CA lifetime |
| `tls_key_type` / `tls_key_size` | `RSA` / 3072 | or `ECC` (secp384r1) |
| `tls_min_protocol` | `TLSv1.2` | minimum TLS version (PostgreSQL, etcd, HAProxy) |
| `tls_postgres_internal_sslmode` | `verify-full` | sslmode for Patroni ↔ PostgreSQL (replication / rewind / superuser) |
| `tls_pki_dir` | `<inventory>/pki` | CA directory on the control node |
| `tls_dir` | `/etc/pki/pgha` | certificate directory on the nodes |
| `tls_allow_ca_change` | `false` | allow replacing the CA |
| `vault_tls_ca_passphrase` | — (vault) | passphrase of `ca.key` |

## Possible problems

| Symptom | Cause / fix |
|---|---|
| `runs etcd over http ... but tls_enabled=True wants https` | the existing cluster has no TLS yet → use `playbooks/enable-tls.yml` |
| `node(s) already have /etc/pki/pgha/ca.crt, but .../pki/ca.crt is missing` | running from a control node without `inventory/pki/` → copy it from the original machine / Git |
| `ca.crt on <node> differs from .../pki/ca.crt` | a different pki/ directory than the one that built the cluster |
| `couldn't resolve module/action 'community.crypto...'` | collection not installed → `ansible-galaxy collection install -r requirements.yml` |
| app: `server certificate for "pg-node-1" ... does not match host name` | the name the app uses is not in the SAN → add it to `tls_extra_sans` and run `--tags tls` |
| app: `certificate verify failed` / `self-signed certificate in certificate chain` | the app does not use the cluster `ca.crt` yet (`sslrootcert`) |
| `etcdctl` reports `connection refused` / `bad certificate` | etcd uses https + client certificates → use `ectl`, or pass `--cacert --cert --key` (see above) |
| every HAProxy server DOWN after a certificate change | `curl --cacert /etc/pki/pgha/ca.crt https://<node>:8008/primary` must return 200 |
