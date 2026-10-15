# Troubleshooting

## ข้อความจาก preflight / playbook และวิธีแก้

| อาการ | สาเหตุ | วิธีแก้ |
|---|---|---|
| `... is not filled in (value: '<IP-node-1>')` | `inventory/hosts.yml` ยังเป็นแม่แบบ | `python3 scripts/setup-inventory.py` หรือแทนค่า `<...>` ในไฟล์ |
| `... is outside cluster_network_cidr` / `already used by` / `not an IPv4` | ค่า IP/subnet/VIP ไม่สัมพันธ์กัน | รัน `scripts/setup-inventory.py` ใหม่ (ตรวจให้ทีละค่า) |
| `runs "CentOS Stream 9" ... Supported: rocky, rhel, almalinux, ol` | OS ไม่อยู่ในรายการที่รองรับ | ใช้ Rocky/RHEL 8–10 หรือ `-e allow_unsupported_os=true` (ใช้ค่าตาม major version) |
| `Mixed EL major versions` | node ต่าง major version กัน (เช่น 9 กับ 10) | ติดตั้งทุก node เป็น major เดียวกัน (glibc/collation ต้องตรงกันสำหรับ replication) |
| `EL8 nodes need ansible-core 2.16` | ansible-core ≥ 2.17 จัดการ Python 3.6 (platform-python ของ EL8) ไม่ได้ | บน control node: `pip install 'ansible-core>=2.16,<2.17'` |
| `enabled repositories do not provide python3.12` | RHEL ยังไม่ register / EL8 < 8.10 / EL9 < 9.4 | `subscription-manager register` + เปิด BaseOS/AppStream, หรือ `dnf upgrade -y && reboot`, หรือแก้ `patroni_python_by_major` เป็น `python3.11` |
| `This system is not registered with an entitlement server` | RHEL ยังไม่ register | `subscription-manager register` แล้ว `subscription-manager repos --list-enabled` |
| `AppStream ... has no postgresql:18 module stream` | ตั้ง `pg_install_source: appstream` บน EL8/9 ที่ไม่มี stream นั้น | ใช้ `pg_install_source: auto` (= pgdg บน EL8/9) |
| `Could not install postgresql18 ... from appstream` | EL10 รุ่นเก่ายังไม่มี `postgresql18` ใน AppStream | `dnf upgrade` ให้เป็น minor ล่าสุด หรือ `pg_install_source: pgdg` |
| `No match for argument: postgresql18-server` (PGDG บน EL8/9) | module `postgresql` ของ AppStream บังแพ็กเกจ PGDG | project ปิด module ให้แล้ว — ถ้ายังเจอ: `sudo dnf -qy module disable postgresql` |
| `Failed to download metadata for repo 'pgdg...'` 404 `rhel-10.-x86_64` | EL10 ไม่มี `$releasever_minor` ทำให้ URL ของ PGDG ผิด | แก้แล้ว (pin เป็น `rhel-10-$basearch`) — ตรวจ `pgdg_pin_major_baseurl` |
| `firewalld and its python module are required` / `Failed to import the required Python library (libselinux-python)` | Ansible ใช้ Python ผิดตัว (เช่น python3.12) แทน Python ของระบบ | อย่ากำหนด `ansible_python_interpreter` เป็น python3.12 — ให้ `detect_os.yml` เลือก (EL8 `/usr/libexec/platform-python`, EL9/10 `/usr/bin/python3`) |
| `Require Python >= 3.10 for Patroni` | `patroni_python` ชี้ไป Python เก่า | แก้ `patroni_python_by_major` ใน `group_vars/all/main.yml` |
| `uses Python 3.11 but patroni_python ... is 3.12` | เปลี่ยน Python ของ Patroni บน cluster ที่รันอยู่ | ทำตามขั้นตอนใน error (pause → stop → ลบ venv → `--tags patroni` → resume) |
| `EL8 AppStream ships HAProxy 1.8` | `haproxy_install_method: package` บน EL8 | ใช้ `source` (ค่าเริ่มต้น) บน EL8 |
| `Attempting to decrypt but no vault secrets found` | `vault.yml` เข้ารหัส — ทุกคำสั่ง ansible ต้องมีรหัส vault | เติม `--ask-vault-pass` หรือตั้ง `vault_password_file` ใน `ansible.cfg` (ดูด้านล่าง) |
| `couldn't resolve module/action 'ansible.posix.selinux'` | ยังไม่ได้ติดตั้ง collection | `ansible-galaxy collection install -r requirements.yml -p ~/.ansible/collections` |
| Patroni validate: `Directory '/usr/pgsql-18/bin' does not exist` | แพ็กเกจมาจากอีกแหล่ง (AppStream binary อยู่ `/usr/bin`) | ห้ามเปลี่ยน `pg_install_source` บน cluster ที่ติดตั้งแล้ว — `dnf info --installed postgresql18-server \| grep 'From repo'` |
| Replica วน `no pg_hba.conf entry for replication connection from host "x.x.x.x"` | `cluster_network_cidr` ไม่ครอบคลุม IP จริงของ node | preflight ตรวจให้ก่อนติดตั้ง — แก้ด้วย `scripts/setup-inventory.py` แล้วรัน `site.yml` ใหม่ |
| แก้ subnet แล้ว Leader ยังใช้ `pg_hba.conf` เก่า | reload ถูกเลื่อนไปท้าย play | แก้แล้ว (`flush_handlers` ก่อนรอ replica) — `sudo systemctl reload patroni` บน Leader |
| แตก zip ใหม่ทับแล้วค่าที่ใส่หาย | `hosts.yml` ถูกแทนด้วยแม่แบบ | สำรอง `inventory/hosts.yml` และ `vault.yml` ก่อนอัปเดต หรือใช้ git — `scripts/setup-inventory.py` สำรองไฟล์เดิมเป็น `.bak-<วันเวลา>` ให้ทุกครั้ง |
| แก้ค่า VIP/router id ใน `group_vars/all/main.yml` แล้วไม่มีผล หรือใน `hosts.yml` แล้วไม่มีผล | ค่าของไซต์ประกาศซ้ำ 2 ที่ (`group_vars/all` ชนะ group vars ใน `hosts.yml`) | ใส่ค่าของไซต์ที่ `inventory/hosts.yml` เท่านั้น |

## ตั้งไฟล์รหัส vault (ไม่ต้องพิมพ์ `--ask-vault-pass`)

แทน `รหัส-vault-ของคุณ` ด้วยรหัสจริง

```bash
echo 'รหัส-vault-ของคุณ' > ~/.vault_pass_pgha
```
```bash
chmod 600 ~/.vault_pass_pgha
```
```bash
cd ~/pg-ha-ansible && sed -i 's|^# vault_password_file = .*|vault_password_file = ~/.vault_pass_pgha|' ansible.cfg
```

## ตามอาการ

| อาการ | ตรวจ |
|---|---|
| preflight: `NOT inside` / `outside cluster_network_cidr` | `ansible pg_cluster -m command -a "ip -brief addr show scope global" --ask-vault-pass` แล้วแก้ด้วย `scripts/setup-inventory.py` |
| preflight: `is not an address of <node>` | `ansible_host` ใน `hosts.yml` ไม่ใช่ IP ของ cluster network บนเครื่องนั้น (เช่นใส่ IP NAT/management) |
| preflight: `keepalived interface ... not found` | ใส่ `keepalived_interface: <ชื่อ>` ใน `hosts.yml` (ดูชื่อจาก `ip -brief link`) |
| preflight: `Another device already uses <VIP>` | เลือก VIP ใหม่ด้วย `scripts/setup-inventory.py` |
| preflight: `cannot reach: https://...` | proxy (`/etc/dnf/dnf.conf`, environment), firewall ขาออก, DNS, SSL inspection (CA ใน `/etc/pki/ca-trust/source/anchors/` + `update-ca-trust`) |
| etcd ไม่ healthy | `sudo journalctl -u etcd -n 50`, port 2379/2380, เวลาเครื่องตรงกัน (`chronyc tracking`) |
| replica ค้าง `creating replica` | `sudo journalctl -u patroni -f` บน replica, DB ใหญ่ให้เพิ่ม `patroni_replica_wait_retries` |
| replica มีไฟล์ค้างใน data dir | บน replica ที่มีปัญหาเท่านั้น (ห้ามทำบน Leader): `sudo systemctl stop patroni` → `sudo rm -rf /var/lib/pgsql/18/data/*` → `sudo systemctl start patroni` |
| HAProxy ทุก server DOWN | `curl -s --cacert /etc/pki/pgha/ca.crt -o /dev/null -w '%{http_code}' https://<node>:8008/primary` (ไม่มี TLS: `http://`), `getsebool haproxy_connect_any` |
| เข้าหน้า HAProxy stats ไม่ได้ทั้งที่รหัสถูก (v2.0 และก่อนหน้า) | รหัสมี `#` → HAProxy ตัดส่วนหลัง `#` ทิ้ง (เป็น comment) — v2.1 quote ให้แล้ว รัน `--tags haproxy` |
| ปัญหาเกี่ยวกับ TLS / cert | ดูตารางท้าย [TLS.md](TLS.md) |
| VIP ไม่ขึ้น | `sudo journalctl -u keepalived -n 50`, `virtual_router_id` ซ้ำกับระบบอื่นใน L2 (เปลี่ยนด้วย `scripts/setup-inventory.py`), switch block gratuitous ARP / VRRP, VM บน cloud ใช้ VIP แบบนี้ไม่ได้ |
| `Maintenance mode` ค้างใน `pctl list` | `pctl resume` |
| ต้องการ SELinux enforcing | ตั้ง `selinux_state: enforcing` ทดสอบใน UAT ก่อน แล้วดู `sudo ausearch -m avc -ts recent` |
| ดูว่า Ansible ใช้ Python ตัวไหน | บรรทัด `Detected OS` ตอนเริ่ม playbook เช่น `Rocky Linux 8.10 → Ansible Python /usr/libexec/platform-python` |

## log ที่ควรดู

| component | คำสั่ง |
|---|---|
| Patroni | `sudo journalctl -u patroni -n 50 --no-pager` |
| PostgreSQL | `sudo tail -50 /var/lib/pgsql/18/data/log/*.log` |
| etcd | `sudo journalctl -u etcd -n 50 --no-pager` |
| HAProxy | `sudo journalctl -u haproxy -n 50 --no-pager` |
| keepalived | `sudo journalctl -u keepalived -n 50 --no-pager` |
| dnf (ติดตั้งแพ็กเกจไม่ผ่าน) | `sudo dnf repolist -v`, `sudo dnf module list postgresql` (EL8/9) |
