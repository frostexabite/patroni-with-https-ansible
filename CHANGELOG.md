# Changelog

## v2.2.0 — English
- All documentation, code comments, playbook messages and `scripts/setup-inventory.py` prompts are now in English
  (no functional change; `setup-inventory.py` accepts `y/yes` and `n/no`)

## v2.1.3
- Added CI for GitHub Actions and GitLab CI (yamllint, ansible-lint, syntax-check), `.gitattributes` (LF)
- Fixed: `verify.yml` reported `Not running on <node>: etcd, patroni, haproxy, keepalived` while every service was running
- Cause (ansible-core 2.19+): regexes with `\\` inside plain / `>-` YAML reach Jinja as two backslashes → the regex never matches.
  Fixed everywhere this pattern occurred: verify (service names), disabling the AppStream `postgresql` module (EL8/9 + PGDG — previously not disabled),
  module stream checks/enablement (EL8/9 + AppStream), preflight, and `enable-tls.yml` step 4a (previously re-ran even when already done)

## v2.1.2
- Fixed: `'ansible_managed' is undefined` in the etcd / Patroni "Shell helper ..." tasks and the .dcs-connection file —
  since ansible-core 2.19 `ansible_managed` exists only in the `template` module, not in `copy content:`
  (the bug dates back to v2.0.0 but only shows with newer ansible-core) → replaced with fixed text

## v2.1.1
- Fixed: preflight and certificate signing on the control node tried to `sudo` on the machine running Ansible
  (`sudo: a password is required` when that user has no passwordless sudo) — the inventory sets
  `ansible_become: true` for every host, which beats the `become: false` keyword, so it is now disabled with the variable `ansible_become: false`

## v2.1.0 — TLS on every channel with a cluster-owned CA

### TLS (`tls_enabled: true` by default) — details in `docs/TLS.md`
- New `tls` role: creates the CA on the control node (`inventory/pki/`, `ca.key` encrypted with `vault_tls_ca_passphrase`);
  node private keys are created on the nodes (never leave them) → CSR → signed by the control node (`community.crypto`)
  - SAN = every node name/IP + VIP + `tls_extra_sans` → applications can use `sslmode=verify-full` through the VIP
  - automatic renewal when fewer than `tls_cert_renew_days` remain, re-issue when the SAN/CA changes — reload, no restart
  - refuses to create a new CA while the nodes trust the old one (protects against running from a control node without pki/)
  - guard: `site.yml` stops if a running cluster uses http while `tls_enabled: true` (or the other way round)
- PostgreSQL: `ssl=on`, `ssl_min_protocol_version` TLSv1.2; Patroni connects for replication / pg_rewind / superuser with
  `sslmode=verify-full`; `tls_postgres_enforce: true` = pg_hba `hostssl` + reject unencrypted connections
- Patroni REST API over https (`ctl.cacert` for patronictl); HAProxy health checks `check-ssl verify required`
- etcd: client + peer https with client certificates (mutual TLS); `ectl` alias on the nodes
- Automatic Patroni restart in maintenance mode when the etcd connection settings change (a reload is not enough)
- HAProxy stats page over https
- `playbooks/enable-tls.yml` (new): turns on TLS for a deployed cluster — etcd peers move through the temporary port 2381
  one member at a time (quorum kept throughout), PostgreSQL `ssl=on` through a reload first, then Patroni + HAProxy switch, PostgreSQL is not restarted
  (test: an INSERT through the VIP every 0.3 seconds during the whole migration, 308/308 succeeded)
- `verify.yml`: connects through the VIP with `sslmode=verify-full`, checks that connections and replication are really encrypted
- preflight: checks `vault_tls_ca_passphrase` and python `cryptography` / `community.crypto` on the control node
- pre-commit hook: blocks committing unencrypted `pki/*.key`

### Fixes
- HAProxy: a stats password containing `#` was truncated (HAProxy treats it as a comment) → now quoted
- HAProxy: server state is saved before each reload (`server-state-file` + socat) — previously every server was considered UP
  for a moment after a reload, so some writes landed on replicas (`cannot execute INSERT in a read-only transaction`) until the first health check finished

## v2.0.0 — Rocky Linux / RHEL 8, 9, 10 + user-entered IPs

### OS
- Supports Rocky Linux and RHEL 8, 9, 10 (AlmaLinux / Oracle Linux work too) instead of Oracle Linux 10 only
- `playbooks/detect_os.yml` (new, always runs): reads `/etc/os-release` with `raw`, checks the OS is supported,
  requires the same major on every node, checks that EL8 uses ansible-core 2.16, and sets `ansible_python_interpreter`
  to the system Python (EL8 `/usr/libexec/platform-python`, EL9/10 `/usr/bin/python3`)
- The Patroni venv uses a Python per major version (`patroni_python_by_major`): EL8/9 = `python3.12`, EL10 = `python3`,
  because psycopg 3.3 needs Python ≥ 3.10 + version check and protection against a venv built with another Python
- PostgreSQL `pg_install_source: auto` (new default): EL8/9 → PGDG, EL10 → AppStream
  - PGDG: disables the AppStream `postgresql` module on EL8/9, `priority=1` instead of `excludepkgs` on the OS repos
    (repo ids differ per distro and subscription-manager overwrites them on RHEL), baseurl pinned on EL10 only, GPG key per arch
  - AppStream: EL8/9 enable the `postgresql:<ver>` module stream, EL10 uses the `postgresql<ver>` packages
  - errors that explain the fix when a package is missing from the repos
- Base packages no longer tied to Oracle Linux (system Python setuptools per major, `iputils` added)
- keepalived build adds `pkgconf-pkg-config`; package-based HAProxy checks for EL9+ (EL8 ships 1.8)
- etcd supports ppc64le / s390x as well
- `ansible.cfg`: `interpreter_python = auto_silent`

### User-entered IPs / VIP
- `inventory/hosts.yml` is a template (`<IP-node-1>`, `<CIDR-cluster>`, `<VIP>`) without any site's IPs
- `scripts/setup-inventory.py` (new): interactive or arguments (`--ips`, `--nodes`, `--vip`, ...), validates the values and writes `hosts.yml`,
  backing up the old file — reuses current values as defaults on re-runs, standard library only (Python 3.6+)
- preflight's first play validates the inventory on the control node before SSH: leftover placeholders, IPv4, duplicate IPs,
  subnet membership, VIP vs. node clash, network/broadcast, prefix, virtual_router_id, host names
- `keepalived_enabled`, `keepalived_router_id`, `keepalived_interface` moved to site values in `hosts.yml`
  (no longer declared in `group_vars/all`, so overrides actually work)
- The VIP interface is the one holding the node IP (previously the default route interface)
- preflight checks that the VIP interface exists and that the OS repos provide the required packages
- Default node names are `pg-node-N`; docs use shell variables (`$NODE1_IP`, `$VIP`) instead of fixed IPs

## v1.0.0
- Oracle Linux 10, PostgreSQL 18 (AppStream/PGDG), etcd 3.7, Patroni 4.1, HAProxy 3.4 / keepalived 2.4 from source
