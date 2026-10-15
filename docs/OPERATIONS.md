# งาน Operation

คำสั่งในเอกสารนี้ใช้ตัวแปร shell — ตั้งให้ตรงกับ `inventory/hosts.yml` ก่อน (ครั้งเดียวต่อ terminal):

```bash
NODE1_IP=10.10.10.11      # ← IP ของ node 1 (ดูใน inventory/hosts.yml)
NODE2_IP=10.10.10.12
NODE3_IP=10.10.10.13
VIP=10.10.10.10
```

ชื่อ node ในตัวอย่างคือ `pg-node-1..3` (ค่า default ของ `scripts/setup-inventory.py`) — แทนด้วยชื่อที่คุณตั้ง
คำสั่งที่ขึ้นต้นด้วย `ansible` รันบน control node ในโฟลเดอร์ `~/pg-ha-ansible` (และ `source ~/ansible-venv/bin/activate` แล้ว)

`psql` อยู่ใน PATH ทั้งแบบ PGDG และ AppStream — ถ้าไม่เจอให้ใช้ `/usr/pgsql-18/bin/psql` (PGDG)

หลังติดตั้ง ทุก node มี helper ให้ (login ใหม่ครั้งแรกเพื่อโหลด):
- `pctl` = `sudo patronictl -c /etc/patroni/patroni.yml` เช่น `pctl list`, `pctl history`
- `ectl` = `sudo etcdctl` พร้อม endpoints และ client cert (TLS) เช่น `ectl endpoint health`, `ectl member list -w table`
  (ถ้า `tls_enabled: false` ใช้ `etcdctl` ตรง ๆ ได้ — `ETCDCTL_ENDPOINTS` ตั้งไว้แล้ว)

TLS (`tls_enabled: true`, ค่าเริ่มต้น): REST API / etcd / หน้า stats เป็น https — คำสั่ง `curl` ด้านล่างจึงใช้
`--cacert /etc/pki/pgha/ca.crt` (บน control node คือ `inventory/pki/ca.crt`) รายละเอียด: [TLS.md](TLS.md)

---

## 1. ตรวจสุขภาพทั้ง cluster

```bash
ansible-playbook playbooks/verify.yml --ask-vault-pass
```

ตรวจ: service ทั้ง 4 รันอยู่ทุก node, etcd healthy, Leader 1 ตัว, replica `streaming` ทุกตัว,
ไม่อยู่ใน maintenance mode, write port → primary, read port → replica, VIP อยู่ 1 node

## 2. ตรวจ Patroni

### 2.1 สถานะ cluster

```bash
pctl list
```

| ดู | ปกติ | ผิดปกติ |
|---|---|---|
| Leader | 1 ตัว | 0 หรือ >1 → ดู etcd |
| Replica State | `streaming` | `starting` / `stopped` / `creating replica` ค้าง → ดู log |
| TL (timeline) | เท่ากันทุก node | ไม่เท่า → replica ยังไม่ตามหลัง failover |
| Lag | 0 หรือใกล้ 0 | เพิ่มขึ้นเรื่อย ๆ → replica ตามไม่ทัน |
| หัวตาราง | ไม่มี `Maintenance mode` | มี → `pctl resume` |

ดูแบบ refresh ทุก 2 วินาที: `pctl list -W` (ออกด้วย Ctrl+C)

### 2.2 Service และ log

```bash
sudo systemctl status patroni --no-pager
```
```bash
sudo journalctl -u patroni -n 30 --no-pager
```
```bash
sudo journalctl -u patroni --since "1 hour ago" --no-pager | grep -iE "error|warning|fail"
```

log ปกติทุก 10 วินาที:
- Leader: `no action. I am (pg-node-1), the leader with the lock`
- Replica: `no action. I am (pg-node-2), a secondary, and following a leader (pg-node-1)`

### 2.3 REST API (สิ่งที่ HAProxy ใช้)

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

(`tls_enabled: false` → ใช้ `http://` และไม่ต้องมี `--cacert`)

`/primary` = 200 เฉพาะ Leader, `/replica` = 200 เฉพาะ replica ที่ปกติ

### 2.4 Replication ฝั่ง PostgreSQL (บน Leader)

```bash
sudo -u postgres psql -c "SELECT application_name, client_addr, state, sync_state, write_lag, replay_lag FROM pg_stat_replication;"
```
```bash
sudo -u postgres psql -c "SELECT slot_name, active, restart_lsn FROM pg_replication_slots;"
```

ต้องมี 2 แถว `streaming` และ slot `active = t` ทั้ง 2

### 2.5 Config และประวัติ

```bash
pctl show-config
```
```bash
pctl history
```
```bash
pctl list -e
```

`list -e` แสดงคอลัมน์ `Pending restart` — มีค่าแปลว่าต้อง restart เพื่อให้ parameter ใหม่มีผล

## 3. ตรวจ etcd / HAProxy / VIP

```bash
ectl endpoint health --cluster -w table
```
```bash
ectl member list -w table
```
```bash
ip -brief addr show to $VIP
```

HAProxy stats: `https://<VIP>:7000` (user `admin`, รหัส `vault_haproxy_stats_password` — browser เตือนเรื่อง CA จนกว่าจะ import `ca.crt`)
- `postgres_write`: UP เฉพาะ Leader
- `postgres_read`: UP เฉพาะ replica

## 4. ทดสอบ replication จริง

```bash
psql -h $VIP -p 5000 -U postgres -c "CREATE TABLE ha_test(id int, note text); INSERT INTO ha_test VALUES (1, 'hello from leader');"
```
```bash
psql -h $VIP -p 5001 -U postgres -c "SELECT inet_server_addr(), * FROM ha_test;"
```
```bash
psql -h $VIP -p 5000 -U postgres -c "DROP TABLE ha_test;"
```

## 5. ทดสอบ failover (ทำก่อนเปิดใช้งานจริง)

### 5.1 Planned switchover

```bash
ansible-playbook playbooks/switchover.yml -e switchover_candidate=pg-node-2 --ask-vault-pass
```
```bash
psql -h $VIP -p 5000 -U postgres -Atc "SELECT inet_server_addr(), pg_is_in_recovery();"
```

ต้องได้ IP ของ node 2 ตามด้วย `|f`

### 5.2 Automatic failover (จำลอง Leader ล่ม) — บน Leader ปัจจุบัน

```bash
sudo systemctl stop patroni
```

รอ ~30 วินาที แล้วบน node อื่น:

```bash
pctl list
```

ต้องมี Leader ใหม่ จากนั้นกลับไปเครื่องเดิม:

```bash
sudo systemctl start patroni
```

node เดิมกลับมาเป็น Replica เอง (ใช้ pg_rewind ถ้าจำเป็น)

### 5.3 VIP failover — บน node ที่ถือ VIP

```bash
sudo systemctl stop haproxy
```
```bash
ip -brief addr show to $VIP
```

VIP ต้องหายจากเครื่องนี้และไปอยู่ node ถัดไป แล้วคืนค่า:

```bash
sudo systemctl start haproxy
```

## 6. เปลี่ยนค่า / อัปเกรด

| งาน | ทำอย่างไร |
|---|---|
| ดูก่อนว่าจะเปลี่ยนอะไร | `ansible-playbook site.yml --ask-vault-pass --check --diff` |
| PostgreSQL parameter | `pctl edit-config` แล้ว `pctl restart postgres-cluster` ถ้าต้อง restart (`pg_dcs_parameters` ใช้ตอน bootstrap ครั้งแรกเท่านั้น) |
| subnet / pg_hba / app subnet / VIP | `python3 scripts/setup-inventory.py` (หรือแก้ `inventory/hosts.yml`) → `ansible-playbook site.yml --ask-vault-pass` (Patroni reload ให้ทันที, rule firewall เก่าถูกลบ) |
| PostgreSQL minor | `package_state: latest` อัปเดต RPM ให้ → `pctl restart postgres-cluster pg-node-2 --force` ทีละ replica แล้ว switchover แล้ว restart ตัวเดิม |
| Patroni / psycopg | แก้ `patroni_version` / `psycopg_version` → รัน `--tags patroni` (pause → restart ทีละ node → resume, ไม่ failover) |
| etcd | แก้ `etcd_version` → `--tags etcd` (restart ทีละ node, บล็อกถ้าข้าม minor) — snapshot ก่อน: `ectl snapshot save /root/etcd-$(date +%F).db` |
| HAProxy | แก้ `haproxy_version` → `--tags haproxy` (build ใหม่ + reload ไม่ตัด connection) |
| keepalived | แก้ `keepalived_version` และ `keepalived_git_commit` → `--tags keepalived` |
| OS minor (เช่น 9.5 → 9.6) | `os_update_all: true` อัปเดตให้ตอนรัน `site.yml` → reboot ทีละ node ตามแถวล่าง |
| OS major (8 → 9 → 10) | ไม่ทำแบบ in-place — สร้าง cluster ใหม่บน major ใหม่แล้วย้ายข้อมูล (ทุก node ต้อง major เดียวกัน) |
| TLS: เปิดบน cluster เดิม / ต่ออายุ cert / บังคับ SSL | ดู [TLS.md](TLS.md) — `playbooks/enable-tls.yml`, `site.yml --tags tls`, `tls_postgres_enforce` |
| reboot OS | ทีละ node เริ่มจาก replica: ถ้าเป็น Leader ให้ switchover ออกก่อน → `sudo reboot` → รอ `pctl list` กลับมา streaming → node ถัดไป |

หาเวอร์ชันล่าสุด:

```bash
pip index versions patroni
```
```bash
git ls-remote --tags https://github.com/etcd-io/etcd.git 'v3.*' | grep -v '\^{}' | awk -F/ '{print $3}' | grep -vE 'rc|alpha|beta' | sort -V | tail -1
```
```bash
git ls-remote --tags https://github.com/acassen/keepalived.git | grep -v '\^{}' | awk -F/ '{print $3}' | sort -V | tail -1
```

HAProxy: ดูสาย LTS ล่าสุดที่ https://www.haproxy.org/
