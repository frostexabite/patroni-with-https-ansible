# PostgreSQL HA Cluster on Rocky Linux / RHEL 8 · 9 · 10 — Patroni + etcd + HAProxy + keepalived (Ansible)

Ansible project สำหรับติดตั้ง PostgreSQL High Availability แบบ **stacked topology** (ทุก node รันครบทุก component)
ดัดแปลงจากบทความ *Building a Highly Available PostgreSQL Cluster with Patroni, etcd, and HAProxy (Oracle Linux 8 & PostgreSQL 16)*
ของ Foyez Ahmed Rony ให้รันได้บน **Rocky Linux และ Red Hat Enterprise Linux ทุก version ที่ยังมี support (8, 9, 10)**
ด้วยเวอร์ชันล่าสุดของทุก component — IP, subnet และ VIP เป็นค่าที่คุณใส่เอง (ไม่มีค่าฝังในโค้ด)
และเข้ารหัสทุกช่องทางด้วย **TLS จาก CA ของ cluster เอง** (app, replication, Patroni REST API, etcd, HAProxy stats)

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

ชื่อ node และจำนวน node (3, 5, 7, ...) เปลี่ยนได้ตอนใส่ค่า

## OS ที่รองรับ

| OS | Python ของระบบ (Ansible ใช้) | Python ของ Patroni | PostgreSQL 18 (`pg_install_source: auto`) | ansible-core บน control node |
|---|---|---|---|---|
| Rocky Linux / RHEL **8** (8.10) | platform-python 3.6 | `python3.12` จาก AppStream | PGDG (+ ปิด module `postgresql`) | **2.16.x เท่านั้น** |
| Rocky Linux / RHEL **9** (9.4+) | python3 3.9 | `python3.12` จาก AppStream | PGDG (+ ปิด module `postgresql`) | 2.16 – 2.21 |
| Rocky Linux / RHEL **10** | python3 3.12 | `python3` (3.12) | AppStream `postgresql18` | 2.16 – 2.21 |

- AlmaLinux และ Oracle Linux 8/9/10 ใช้ได้เหมือนกัน (ID `almalinux`, `ol`)
- ทุก node ใน cluster ต้องเป็น major version เดียวกัน (glibc/collation ต้องตรงกันสำหรับ streaming replication)
- RHEL ต้อง register (`subscription-manager register`) และเปิด BaseOS + AppStream
- **EL8**: ansible-core 2.17 ขึ้นไปจัดการ Python 3.6 ไม่ได้ และ binding ของ dnf บน EL8 มีเฉพาะ Python 3.6 จึงต้องใช้ ansible-core 2.16
  (preflight ตรวจและแจ้งให้)
- RHEL/CentOS 7 ไม่รองรับ — หมดอายุแล้ว, ไม่มี Python ≥ 3.10 สำหรับ psycopg 3.3 (Rocky Linux ไม่มีเวอร์ชัน 7)

project เลือกค่าที่ต่างกันตาม OS ให้อัตโนมัติ (`playbooks/detect_os.yml` + `inventory/group_vars/all/main.yml`)
ปรับเองได้ผ่าน `os_system_python`, `patroni_python_by_major`, `pg_install_source`

## เวอร์ชัน (ล่าสุด ณ 12 ต.ค. 2026)

| Component  | Version | ติดตั้งจาก | ตัวแปร |
|------------|---------|-----------|--------|
| OS packages | ล่าสุดเสมอ | `dnf upgrade` + `state: latest` | `os_update_all`, `package_state` |
| PostgreSQL | **18.6** | EL8/9: PGDG · EL10: AppStream (เลือกเองได้) | `pg_version`, `pg_install_source` |
| etcd | **3.7.2** | GitHub release + ตรวจ SHA256 | `etcd_version` |
| Patroni | **4.1.5** | PyPI → venv `/opt/patroni` | `patroni_version` |
| psycopg | **3.3.6** (ต้องใช้ Python ≥ 3.10) | PyPI | `psycopg_version` |
| HAProxy | **3.4.6** (LTS) | build จาก source (haproxy.org + ตรวจ SHA256) | `haproxy_version` |
| keepalived | **2.4.3** | build จาก Git tag + ตรวจ commit hash | `keepalived_version` |
| Ansible | ansible-core 2.16 (มี EL8) / 2.16–2.21 · ansible.posix ≥ 2.2.2 · community.crypto ≥ 2.22 | control node | `requirements.yml` |

PostgreSQL 19 ยังเป็น Beta ณ วันที่จัดทำ จึงใช้ 18

## เอกสาร

| ไฟล์ | เนื้อหา |
|---|---|
| [docs/INSTALL.md](docs/INSTALL.md) | ขั้นตอนติดตั้งแบบ copy & paste ตั้งแต่เครื่องเปล่า (Rocky / RHEL) |
| [docs/OPERATIONS.md](docs/OPERATIONS.md) | ตรวจสุขภาพ, ตรวจ Patroni, ทดสอบ failover, อัปเกรด, งานประจำ |
| [docs/TLS.md](docs/TLS.md) | TLS/SSL ด้วย CA ของ cluster: เปิดบน cluster เดิม, connection string ของ app, ต่ออายุ cert |
| [docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md) | ปัญหาที่เจอบ่อยและวิธีแก้ |
| [CHANGELOG.md](CHANGELOG.md) | สิ่งที่เปลี่ยนในแต่ละเวอร์ชัน |

## เริ่มต้นแบบย่อ

```bash
ansible-galaxy collection install -r requirements.yml
python3 scripts/setup-inventory.py                       # ถาม IP ของแต่ละ node, subnet, VIP → เขียน inventory/hosts.yml
cp inventory/group_vars/all/vault.yml.example inventory/group_vars/all/vault.yml
vi inventory/group_vars/all/vault.yml && ansible-vault encrypt inventory/group_vars/all/vault.yml   # รวม vault_tls_ca_passphrase
ansible-playbook playbooks/preflight.yml --ask-vault-pass   # ตรวจความพร้อม (ไม่แก้เครื่อง)
ansible-playbook site.yml --ask-vault-pass                  # ติดตั้ง + verify
```

cluster ที่ติดตั้งด้วย v1.x / v2.0 (ยังไม่มี TLS): ใช้ `ansible-playbook playbooks/enable-tls.yml --ask-vault-pass`
แทน `site.yml` ครั้งแรก — ดู [docs/TLS.md](docs/TLS.md)

## ใส่ IP / subnet / VIP

ค่าของไซต์อยู่ที่ `inventory/hosts.yml` ไฟล์เดียว ไฟล์ที่ให้มาเป็น**แม่แบบ** (`<IP-node-1>`, `<VIP>` ...) ใส่ค่าได้ 2 วิธี

**1. ถาม-ตอบ (แนะนำ)** — ตรวจความถูกต้องทุกค่า สำรองไฟล์เดิม แล้วเขียนไฟล์ใหม่

```bash
python3 scripts/setup-inventory.py
```

```
จำนวน node (เลขคี่ >= 3) [3]:
Node 1  ชื่อ host [pg-node-1]:
Node 1  IP: 10.10.10.11
...
Subnet ของ cluster (CIDR) [10.10.10.0/24]:
Subnet ของ application ที่ให้ต่อ DB (คั่นด้วย , ) [10.10.10.0/24]: 10.10.20.0/24
ใช้ Virtual IP สำหรับ application หรือไม่ [Y/n]:
VIP (IP ว่างใน 10.10.10.0/24): 10.10.10.10
```

รันซ้ำเมื่อไรก็ได้ ค่าเดิมจะเป็นค่า default ในวงเล็บ หรือใส่ทุกค่าในบรรทัดเดียว (ใช้ใน script ได้):

```bash
python3 scripts/setup-inventory.py --ips 10.10.10.11,10.10.10.12,10.10.10.13 --vip 10.10.10.10 --yes
python3 scripts/setup-inventory.py --nodes db1=10.0.0.11,db2=10.0.0.12,db3=10.0.0.13 \
    --cidr 10.0.0.0/24 --app-cidrs 10.0.1.0/24,10.0.2.0/24 --vip 10.0.0.10 --router-id 60 --yes
python3 scripts/setup-inventory.py --help
```

**2. แก้ไฟล์เอง** — แทนทุกค่าใน `<...>` ของ `inventory/hosts.yml`

ไม่ว่าวิธีไหน `preflight` จะตรวจบน control node ก่อน SSH: ยังมี `<...>` เหลือ, IP ผิดรูปแบบ/ซ้ำ, IP หรือ VIP อยู่นอก
`cluster_network_cidr`, VIP ชนกับ node, จำนวน node ไม่ใช่เลขคี่ จากนั้นตรวจบนเครื่องจริงว่า IP อยู่บน node และ VIP ยังว่าง

**ไฟล์ที่ต้องแก้สำหรับ environment ใหม่มีแค่ 2 ไฟล์**

| ไฟล์ | ใส่อะไร |
|---|---|
| `inventory/hosts.yml` | ชื่อ node, IP, `cluster_network_cidr`, `app_allowed_cidrs`, VIP (`scripts/setup-inventory.py` เขียนให้) |
| `inventory/group_vars/all/vault.yml` | รหัสผ่านทั้งหมด (เข้ารหัสด้วย ansible-vault) |

`inventory/group_vars/all/main.yml` เป็นค่า default/tuning — ไม่ต้องแก้ถ้าไม่ได้ต้องการปรับจูน
(ค่าของไซต์อย่าใส่ซ้ำในไฟล์นี้ เพราะ `group_vars/all` มี precedence สูงกว่า group vars ใน `hosts.yml`)

## โครงสร้าง

```
.
├── ansible.cfg
├── requirements.yml                  # ansible.posix, community.crypto
├── site.yml                          # ตรวจ inventory → detect OS → preflight → roles → verify
├── inventory/
│   ├── hosts.yml                     # ค่าของไซต์ (แม่แบบ — ใส่ค่าด้วย scripts/setup-inventory.py)
│   ├── pki/                          # CA ของ cluster (สร้างตอนรันครั้งแรก — ca.key เข้ารหัส, commit ได้)
│   └── group_vars/all/
│       ├── main.yml                  # default / tuning / versions / ค่าตาม EL major
│       └── vault.yml.example         # template รหัสผ่าน
├── playbooks/
│   ├── detect_os.yml                 # อ่าน /etc/os-release, ตรวจ OS, เลือก Python ของระบบ (รันเสมอ)
│   ├── preflight.yml                 # ตรวจค่าใน inventory + ความพร้อมของ node (read-only)
│   ├── verify.yml                    # ตรวจสุขภาพ cluster (read-only)
│   ├── switchover.yml                # planned switchover
│   ├── enable-tls.yml                # เปิด TLS บน cluster เดิมแบบไม่เสีย quorum / ไม่ restart PostgreSQL
│   └── tasks/etcd-peer-move.yml      # (ใช้โดย enable-tls.yml)
├── roles/
│   ├── common/      # OS update, hostname, /etc/hosts, chrony, SELinux, firewalld (+ ลบ rule ค้าง)
│   ├── postgresql/  # PGDG หรือ AppStream (EL8/9 module stream, EL10 postgresqlNN), ไม่ initdb
│   ├── tls/         # CA บน control node, key/CSR บน node, เซ็น + ต่ออายุ cert, guard http↔https
│   ├── etcd/        # binary + checksum, config, systemd, upgrade guard
│   ├── patroni/     # venv (python3.12 บน EL8/9), patroni.yml, ordered bootstrap, safe rolling restart
│   ├── haproxy/     # source build 3.4 LTS, write/read/stats
│   └── keepalived/  # source build, VIP, unicast VRRP, track haproxy
├── scripts/
│   ├── setup-inventory.py            # ใส่ IP / subnet / VIP แบบถาม-ตอบ → inventory/hosts.yml
│   └── git-hooks/pre-commit          # กัน commit vault.yml / pki/*.key ที่ยังไม่เข้ารหัส
└── docs/
```

## สิ่งที่ปรับจากบทความต้นฉบับ

| เรื่อง | ต้นฉบับ (OL8) | project นี้ | เหตุผล |
|---|---|---|---|
| OS | Oracle Linux 8 | Rocky Linux / RHEL 8, 9, 10 (ตรวจและปรับค่าตาม major version อัตโนมัติ) | |
| PostgreSQL | PGDG EL-8, PG 16 | PG 18.6 — EL8/9: PGDG (ปิด module AppStream ให้), EL10: AppStream | AppStream ของ EL8/9 ไม่มี PG 18 ทุก minor |
| PGDG บน EL10 | — | pin baseurl เป็น `rhel-10-$basearch` + `priority=1` | ไม่มี `$releasever_minor` → 404 และ AppStream มี `postgresql18` ชื่อซ้ำ |
| Patroni | `pip3` ลง system Python | venv + pin version + psycopg3, Python ≥ 3.10 ตาม OS | ไม่ปน system Python, psycopg 3.3 ต้องใช้ 3.10+ |
| Ansible Python | auto | Python ของระบบ (EL8 platform-python) ตั้งให้ทุก node | binding dnf/SELinux/firewalld มีเฉพาะ Python ของระบบ |
| etcd | 3.5.17 | 3.7.2 + ตรวจ SHA256 + กันข้าม minor | |
| Firewall / SELinux | ปิด firewalld, `setenforce 0` | firewalld เปิดเฉพาะ port + จำกัด source | ปลอดภัยกว่า |
| Auth | `md5`, รหัสใน plaintext | `scram-sha-256`, Ansible Vault, `rewind_user` แยก | |
| pg_hba | ใต้ `bootstrap` | ใต้ `postgresql` + reload ทันทีเมื่อเปลี่ยน | แก้ subnet แล้วมีผลโดยไม่ต้อง bootstrap ใหม่ |
| HAProxy check | `OPTIONS /master` | `GET /primary` | `/master` deprecated |
| keepalived | `killall -0`, `weight 2` | `vrrp_track_process`, `weight 0` | ต้นฉบับ VIP ไม่ย้ายเมื่อ haproxy ตาย (150 > 140+2) |
| keepalived auth | `patroni_vip` (11 ตัว) | ตรวจ ≤ 8 ตัว | VRRP PASS ตัดส่วนเกินทิ้ง |
| VRRP | multicast, `eth0` | unicast, ใช้ interface ที่มี IP ของ node | VM/cloud มัก block multicast; RHEL ใช้ `ens*` |
| IP / VIP | ฝังใน config | ใส่เองผ่าน `scripts/setup-inventory.py` + ตรวจก่อน SSH | ไม่มีค่าของไซต์ใดฝังในโค้ด |
| ตรวจก่อนติดตั้ง | — | `preflight.yml`: inventory, OS, repo, subnet, VIP ว่าง, internet | ดักปัญหาก่อนลงมือ |
| การเข้ารหัส | ไม่มี (http / plaintext ทุกช่องทาง) | TLS ทุกช่องทางด้วย CA ของ cluster, etcd mutual TLS, `sslmode=verify-full` | กันดักฟังรหัสผ่าน/ข้อมูลใน network |

## Git workflow

### เอาขึ้น GitHub / GitLab

```bash
git remote add origin git@github.com:<org>/pg-ha-ansible.git     # หรือ git@gitlab.com:<group>/pg-ha-ansible.git
git push -u origin main --tags
```

- CI ตรวจ yamllint, ansible-lint, syntax-check ให้ทุก push: `.github/workflows/lint.yml` (GitHub) และ `.gitlab-ci.yml` (GitLab)
- repo **ไม่มี** ค่าของไซต์หรือความลับ: `inventory/hosts.yml` เป็นแม่แบบ, ไม่มี `vault.yml` / `inventory/pki/`
- repo private: commit `vault.yml` (เข้ารหัส) และ `inventory/pki/` ของไซต์ได้ — pre-commit hook กันไฟล์ที่ไม่ได้เข้ารหัส
- repo public: เปิดบรรทัดท้าย `.gitignore` เพื่อไม่ให้ vault และ CA ของไซต์ขึ้นไป แล้วเก็บไว้ที่อื่น (หรือแยก repo private สำหรับ inventory)

### ทั่วไป

- `vault.yml` commit ได้เฉพาะตอนเข้ารหัสแล้ว — เปิด hook ด้วย `git config core.hooksPath scripts/git-hooks`
- ก่อน push: `ansible-lint && ansible-playbook site.yml --syntax-check`
- ก่อนแก้ cluster ที่รันอยู่: `ansible-playbook site.yml --ask-vault-pass --check --diff`
- หลาย environment: copy ทั้งโฟลเดอร์ `inventory/` เป็น `inventories/uat/` (group_vars ต้องอยู่ข้าง hosts.yml)
  แล้ว `python3 scripts/setup-inventory.py -o inventories/uat/hosts.yml` และรันด้วย `-i inventories/uat/hosts.yml`
