# ติดตั้ง PostgreSQL HA Cluster บน Rocky Linux / RHEL (copy & paste)

รองรับ Rocky Linux / RHEL **8 (8.10), 9 (9.4+), 10** — ทุก node ต้องเป็น major version เดียวกัน

## ค่าของคุณ

เตรียมค่าเหล่านี้ก่อนเริ่ม (ตัวอย่างในวงเล็บเป็นแค่ตัวอย่าง)

| | ค่า |
|---|---|
| node 1–3 | ชื่อ + IP เช่น `pg-node-1` (10.10.10.11), `pg-node-2` (10.10.10.12), `pg-node-3` (10.10.10.13) |
| cluster subnet | subnet ที่ทุก node อยู่ เช่น `10.10.10.0/24` |
| app subnet | subnet ของ application ที่จะต่อ DB เช่น `10.10.20.0/24` |
| VIP | IP ว่างใน cluster subnet เช่น `10.10.10.10` |
| SSH user | `ansible` (sudo NOPASSWD) |

คำสั่งในเอกสารนี้ใช้ตัวแปร shell — **ตั้งครั้งเดียวในแต่ละ terminal** แล้ว copy คำสั่งต่อได้เลย:

```bash
NODE1_IP=10.10.10.11      # ← แก้เป็น IP จริงของคุณ
NODE2_IP=10.10.10.12
NODE3_IP=10.10.10.13
VIP=10.10.10.10
```

---

## ส่วน A — ทุก node (รันในฐานะ root บนทุกเครื่อง)

ติดตั้ง Rocky Linux หรือ RHEL (Minimal ก็พอ) ตั้ง static IP ให้เรียบร้อย

### A1. (เฉพาะ RHEL) register และเปิด repo

```bash
subscription-manager register
```
```bash
subscription-manager repos --enable "rhel-$(rpm -E %rhel)-for-$(uname -m)-baseos-rpms" --enable "rhel-$(rpm -E %rhel)-for-$(uname -m)-appstream-rpms"
```

Rocky Linux ไม่ต้องทำขั้นนี้

### A2. สร้าง user `ansible`

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

### A3. ตรวจ OS และ repo

```bash
cat /etc/os-release | grep -E '^(NAME|VERSION_ID)='
```
```bash
dnf repolist
```

ต้องเห็น BaseOS และ AppStream

| OS | repo ที่ต้องเห็น |
|---|---|
| Rocky Linux | `baseos`, `appstream` |
| RHEL | `rhel-<8/9/10>-for-x86_64-baseos-rpms`, `rhel-<8/9/10>-for-x86_64-appstream-rpms` |

EL8 ต้องเป็น 8.10 และ EL9 ต้องเป็น 9.4 ขึ้นไป (มี `python3.12` ใน AppStream) — ถ้าเก่ากว่าให้ `dnf upgrade -y && reboot` ก่อน

> ห้ามติดตั้ง `postgresql-server` หรือรัน `initdb` เอง — Patroni จะสร้าง data directory เอง

---

## ส่วน B — Control node (เครื่องที่รัน Ansible)

ใช้เครื่องใน cluster เป็น control node ก็ได้ (เช่น node 1) หรือเครื่องแยก

### B1. ติดตั้ง Ansible

```bash
sudo dnf install -y git python3.12 python3.12-pip openssh-clients unzip
```
```bash
python3.12 -m venv ~/ansible-venv
```
```bash
source ~/ansible-venv/bin/activate
```
```bash
pip install -U pip
```

เลือกเวอร์ชัน ansible-core ตาม OS ของ **node ใน cluster** (ไม่ใช่ของ control node):

| node ใน cluster | คำสั่ง |
|---|---|
| มี EL8 (Rocky/RHEL 8) | `pip install 'ansible-core>=2.16,<2.17'` |
| EL9 หรือ EL10 เท่านั้น | `pip install ansible-core ansible-lint` |

```bash
ansible --version
```

> เปิด terminal ใหม่ทุกครั้งต้อง `source ~/ansible-venv/bin/activate` ก่อน
>
> ทำไม EL8 ต้อง 2.16: Python ที่มี binding ของ dnf/SELinux/firewalld บน EL8 คือ platform-python 3.6
> ซึ่ง ansible-core 2.17 ขึ้นไปไม่รองรับ — preflight จะหยุดพร้อมบอกวิธีแก้ถ้าใช้เวอร์ชันผิด

### B2. SSH key ไปทุก node

```bash
ssh-keygen -t ed25519 -f ~/.ssh/id_ed25519 -N ""
```
```bash
for ip in $NODE1_IP $NODE2_IP $NODE3_IP; do ssh-copy-id ansible@$ip; done
```

### B3. เตรียม project

```bash
cd ~ && unzip psg-ha-ansible-v2.1.3.zip
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

ถ้ามี node EL8 (ansible-core 2.16) ให้ใช้ community.crypto 2.x:

```bash
ansible-galaxy collection install 'community.crypto:>=2.22.0,<3.0.0' -p ~/.ansible/collections
```

ถ้า `ansible-galaxy` ต่อ galaxy.ansible.com ไม่ได้ ให้ใช้ `pip install -U ansible` แทน (มี ansible.posix และ community.crypto มาในตัว — ถ้ามี EL8 ใช้ `pip install 'ansible>=9,<10'` ซึ่งมากับ ansible-core 2.16)

### B4. ใส่ IP / subnet / VIP

```bash
python3 scripts/setup-inventory.py
```

ตอบคำถามทีละข้อ (Enter = ใช้ค่าในวงเล็บ) สคริปต์ตรวจ IP, ความซ้ำ, subnet, VIP ให้ แล้วเขียน `inventory/hosts.yml`
หรือใส่ทั้งหมดในบรรทัดเดียวจากตัวแปรที่ตั้งไว้:

```bash
python3 scripts/setup-inventory.py --ips $NODE1_IP,$NODE2_IP,$NODE3_IP --vip $VIP --app-cidrs 10.10.20.0/24 --yes
```

ตรวจผล:

```bash
cat inventory/hosts.yml
```

ต้องการแก้ทีหลัง: รันสคริปต์ซ้ำ (ค่าเดิมเป็น default) หรือแก้ไฟล์ตรง ๆ — ไฟล์เดิมถูกสำรองเป็น `hosts.yml.bak-<วันเวลา>`

### B5. รหัสผ่าน (Vault)

แก้รหัสผ่านก่อน paste — `vault_keepalived_auth_pass` ไม่เกิน 8 ตัวอักษร,
`vault_tls_ca_passphrase` อย่างน้อย 8 ตัวอักษร (เข้ารหัส key ของ CA ใน `inventory/pki/ca.key` — **จดเก็บไว้** ต้องใช้ตอนต่ออายุ cert)

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

> ทุกคำสั่ง `ansible` / `ansible-playbook` ต้องมี `--ask-vault-pass` (แม้แต่ `-m ping`)

### B6. ทดสอบการเชื่อมต่อ

```bash
ansible pg_cluster -m ping --ask-vault-pass
```
```bash
ansible pg_cluster -m command -a "cat /etc/os-release" --ask-vault-pass | grep -E 'SUCCESS|PRETTY_NAME'
```

### B7. ตรวจความพร้อม (ไม่แก้ไขเครื่อง)

```bash
ansible-playbook playbooks/preflight.yml --ask-vault-pass
```

ตรวจ: ค่าใน `hosts.yml` (IP/subnet/VIP), จำนวน node, รหัสผ่านครบ, OS + major version ตรงกันทุก node,
เวอร์ชัน ansible-core, repo มี `python3.12` (EL8/9), IP อยู่บน node จริง, VIP ยังว่าง,
ออก internet ไปแหล่งดาวน์โหลดได้, เวลา sync — ท้ายสุดแสดงแผนของแต่ละ node เช่น

```
Rocky 9.6 | IP 10.10.10.11 | VRRP on ens192 | Patroni Python /usr/bin/python3.12 | PostgreSQL 18 from pgdg (/usr/pgsql-18/bin)
```

### B8. ติดตั้ง

```bash
ansible-playbook site.yml --ask-vault-pass
```

ใช้เวลาประมาณ 15–30 นาที (build HAProxy/keepalived) — จบด้วย verify อัตโนมัติ
ผลที่ถูกต้อง: `failed=0` ทุก node

### B9. เก็บเข้า Git

```bash
git add -A && git commit -m "Deploy cluster $NODE1_IP,$NODE2_IP,$NODE3_IP VIP $VIP"
```

`git add -A` รวม `inventory/pki/` (CA ของ cluster) ด้วย — ตั้งใจให้เก็บไว้ใน Git: `ca.key` ถูกเข้ารหัส
และ pre-commit hook จะไม่ยอมให้ commit key ที่ไม่ได้เข้ารหัส

---

## ส่วน C — ตรวจหลังติดตั้ง

```bash
ansible-playbook playbooks/verify.yml --ask-vault-pass
```

ต้องเห็น `Leader pg-node-1, replicas streaming: pg-node-2, pg-node-3` (ตามชื่อที่ตั้ง),
write port ได้ `recovery=false`, read port ได้ `recovery=true` และ VIP อยู่ 1 node

รายละเอียดการตรวจและทดสอบ failover: [OPERATIONS.md](OPERATIONS.md)

## Application connection

| | ค่า |
|---|---|
| Host | VIP ของคุณ (`$VIP`) |
| Write (primary) | port `5000` |
| Read (replica, round-robin) | port `5001` |
| TLS | `sslmode=verify-full sslrootcert=ca.crt` — แจก `inventory/pki/ca.crt` ให้ทีม app |
| HAProxy stats | `https://<VIP>:7000` (user `admin`) |

ตัวอย่าง:

```bash
psql "host=$VIP port=5000 dbname=postgres user=postgres sslmode=verify-full sslrootcert=inventory/pki/ca.crt"
```

JDBC / Python / .NET และการบังคับให้ทุก connection ใช้ SSL: [TLS.md](TLS.md)
