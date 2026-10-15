# TLS / SSL ด้วย CA ของ cluster เอง

ตั้งแต่ v2.1.0 ถ้าเปิด `tls_enabled: true` (ค่าเริ่มต้น) ทุกช่องทางจะเข้ารหัสด้วย certificate ที่ออกโดย **CA ของ cluster เอง**
ไม่ต้องซื้อ cert และไม่ต้องมี PKI ขององค์กร

| ช่องทาง | port | ก่อน | หลังเปิด TLS |
|---|---|---|---|
| App → VIP → HAProxy → PostgreSQL | 5000 / 5001 | plaintext | TLS ไปจบที่ PostgreSQL (HAProxy ส่งผ่าน TCP เฉย ๆ) — `sslmode=verify-full` ได้ |
| Replication / pg_rewind ระหว่าง node | 5432 | plaintext | TLS + `sslmode=verify-full` |
| Patroni REST API + HAProxy health check | 8008 | http | https (`check-ssl verify required`) |
| etcd client (Patroni, etcdctl) | 2379 | http | https + **client certificate** (mutual TLS) |
| etcd peer | 2380 | http | https + mutual TLS |
| หน้า HAProxy stats | 7000 | http | https |

## CA และไฟล์ cert

```
control node                                  แต่ละ node (/etc/pki/pgha)
inventory/pki/ca.crt   ── copy ──────────────▶ ca.crt          0644 root
inventory/pki/ca.key   (เข้ารหัสด้วย            node.crt        0644 root      ← เซ็นโดย ca.key
                        vault_tls_ca_passphrase) node.key        0600 root      ← สร้างบน node, ไม่เคยออกจากเครื่อง
inventory/pki/ca.csr                            postgres.key    0600 postgres  (สำเนาให้ PostgreSQL/Patroni)
                                                etcd.key        0600 etcd      (สำเนาให้ etcd)
                                                haproxy.pem     0600 root      (cert + key ให้หน้า stats)
```

- **CA** สร้างครั้งแรกที่รัน playbook — RSA 3072, อายุ 10 ปี (`tls_ca_valid_days`)
- **private key ของ node** สร้างบน node เอง แล้วส่งแค่ CSR มาให้ control node เซ็น → key ไม่ผ่าน network
- **cert ของ node** อายุ ~2 ปี (`tls_cert_valid_days: 825`) — SAN มีชื่อ + IP ของ **ทุก node** + **VIP** + `tls_extra_sans`
  เพราะ app ที่ต่อผ่าน VIP หรือ HAProxy ของ node ใดก็ตาม อาจไปจบที่ PostgreSQL บน node ไหนก็ได้
- TLS ขั้นต่ำ 1.2 (`tls_min_protocol`)

> **เก็บ `inventory/pki/` ให้ดี** — commit เข้า Git ได้ (ca.key ถูกเข้ารหัส และ pre-commit hook กัน key ที่ไม่ได้เข้ารหัส)
> ถ้า `ca.key` หรือ `vault_tls_ca_passphrase` หาย จะต่ออายุ cert ไม่ได้และต้องออก CA ใหม่ (app ต้องเปลี่ยน ca.crt)
> playbook จะ **ไม่ยอม** สร้าง CA ใหม่เองถ้า node ยังเชื่อ CA เดิมอยู่ (กันรันจากเครื่องอื่นที่ไม่มี pki/ แล้ว cert เปลี่ยนทั้ง cluster)

## ติดตั้ง cluster ใหม่ (มี TLS ตั้งแต่ต้น)

1. ติดตั้ง collection (มี `community.crypto` เพิ่ม):

   ```bash
   ansible-galaxy collection install -r requirements.yml
   ```

   ถ้ามี node EL8 (ใช้ ansible-core 2.16) ให้ใช้ community.crypto 2.x:

   ```bash
   ansible-galaxy collection install 'community.crypto:>=2.22.0,<3.0.0'
   ```

2. เพิ่มรหัสของ CA ใน vault (อย่างน้อย 8 ตัวอักษร — **จดเก็บไว้**):

   ```bash
   ansible-vault edit inventory/group_vars/all/vault.yml
   ```
   ```yaml
   vault_tls_ca_passphrase: "ใส่รหัสยาว ๆ ที่นี่"
   ```

3. (ถ้า app ต่อด้วยชื่อ DNS) ใส่ชื่อใน `inventory/group_vars/all/main.yml`:

   ```yaml
   tls_extra_sans: ["DNS:pgdb.example.local"]
   ```

4. ติดตั้งตามปกติ:

   ```bash
   ansible-playbook site.yml --ask-vault-pass
   ```

## เปิด TLS บน cluster ที่ deploy ไปแล้ว (v1.x / v2.0 ที่ยังไม่มี TLS)

**ห้ามรัน `site.yml` ตรง ๆ** — role `tls` จะหยุดพร้อมบอกให้ใช้ playbook ด้านล่าง เพราะถ้าเปลี่ยน etcd peer เป็น https
พร้อมกันทุก node จะเสีย quorum และ HAProxy จะมองไม่เห็น primary ระหว่างที่ Patroni ยังไม่เปลี่ยน

ทำตามข้อ 1–3 ด้านบนก่อน แล้ว:

```bash
ansible-playbook playbooks/enable-tls.yml --ask-vault-pass
```

ลำดับที่ playbook ทำ (รันซ้ำได้ — ขั้นที่เสร็จแล้วจะถูกข้าม):

| ขั้น | ทำอะไร | ผลกระทบ |
|---|---|---|
| 1 | สร้าง CA + key/cert ของทุก node | ไม่มี |
| 2 | etcd: client รับทั้ง http และ https บน port เดิม, peer เปิด https ที่ port ชั่วคราว 2381 → restart ทีละ node → ย้าย peer URL ไป `https://<ip>:2381` | ไม่มี (quorum อยู่ตลอด) |
| 3 | etcd: peer เปิด https ที่ 2380 → restart ทีละ node → ย้าย peer URL กลับ `https://<ip>:2380` | ไม่มี |
| 4a | PostgreSQL `ssl=on` ด้วย Patroni reload (ไม่ restart) | ไม่มี |
| 4b | Patroni pause → patroni.yml ใหม่ (etcd https, REST API https, sslmode ภายใน) → restart Patroni พร้อมกัน → reload HAProxy เป็น health check https ทันที | PostgreSQL **ไม่** restart; HAProxy เก็บสถานะ server ไว้ข้าม reload |
| 5 | etcd ปิด http (restart ทีละ node) → Patroni resume → ปิด port 2381 → verify | ไม่มี |

ผลทดสอบบน cluster 3 node (etcd 3.7.2, Patroni 4.1.5, HAProxy 3.4, PostgreSQL 16) ที่มี INSERT ผ่าน VIP ทุก 0.3 วินาทีตลอดการย้าย:
**insert สำเร็จ 308/308 ครั้ง ไม่มีครั้งไหนล้ม** — แต่แนะนำให้ทำในช่วง maintenance window เผื่อไว้

หลังจากนี้รัน `site.yml` ได้ตามปกติ (ไม่มีอะไรเปลี่ยนเพิ่ม):

```bash
ansible-playbook site.yml --ask-vault-pass --check --diff
```

## ให้ app ต่อแบบ TLS

แจก `inventory/pki/ca.crt` ให้ทีม app (เป็น public cert ไม่ใช่ความลับ)

| client | connection string |
|---|---|
| psql / libpq | `psql "host=<VIP> port=5000 dbname=app user=app sslmode=verify-full sslrootcert=ca.crt"` |
| JDBC | `jdbc:postgresql://<VIP>:5000/app?ssl=true&sslmode=verify-full&sslrootcert=/path/ca.crt` |
| Python (psycopg) | `psycopg.connect(host="<VIP>", port=5000, dbname="app", user="app", sslmode="verify-full", sslrootcert="ca.crt")` |
| .NET (Npgsql) | `Host=<VIP>;Port=5000;Database=app;Username=app;SSL Mode=VerifyFull;Root Certificate=ca.crt` |

- `verify-full` = เข้ารหัส + ตรวจว่า cert ออกโดย CA ของ cluster + ชื่อ/IP ที่ต่อตรงกับ SAN (กัน man-in-the-middle)
- ถ้าต่อด้วยชื่อ DNS ชื่อนั้นต้องอยู่ใน `tls_extra_sans`
- ตรวจว่า connection เข้ารหัสจริง:

  ```sql
  select ssl, version, cipher from pg_stat_ssl where pid = pg_backend_pid();
  ```

### บังคับให้ทุก connection ใช้ SSL

ค่าเริ่มต้น `tls_postgres_enforce: false` — PostgreSQL ยังรับ connection ที่ไม่เข้ารหัส เพื่อให้ app ย้ายทีละตัวได้
เมื่อ app ทุกตัวใช้ SSL แล้ว:

```yaml
# inventory/group_vars/all/main.yml
tls_postgres_enforce: true
```
```bash
ansible-playbook site.yml --ask-vault-pass --tags patroni
```

pg_hba เปลี่ยนเป็น `hostssl` + `hostnossl ... reject` (Patroni reload ให้ ไม่ restart) — client ที่ไม่ใช้ SSL จะได้
`pg_hba.conf rejects connection ... no encryption`

## ใช้งานประจำ

```bash
# etcd (ต้องใช้ client cert ของ node → ใช้ alias ectl ซึ่งรันผ่าน sudo)
ectl member list -w table
ectl endpoint health --cluster -w table

# Patroni REST API
curl -s --cacert /etc/pki/pgha/ca.crt https://$NODE1_IP:8008/cluster | python3 -m json.tool

# HAProxy stats
https://<VIP>:7000/      (browser จะเตือนเรื่อง CA จนกว่าจะ import ca.crt เข้าเครื่อง)

# ดูอายุ cert
sudo openssl x509 -in /etc/pki/pgha/node.crt -noout -subject -enddate -ext subjectAltName
```

## ต่ออายุ cert

ไม่ต้องทำอะไรพิเศษ: ทุกครั้งที่รัน `site.yml` (หรือ `--tags tls`) ถ้า cert ของ node เหลืออายุน้อยกว่า `tls_cert_renew_days` (30 วัน)
จะออกใบใหม่ให้ แล้ว reload PostgreSQL / Patroni / HAProxy ให้เอง (etcd อ่าน cert ใหม่เองทุก connection) — ไม่มี restart

```bash
ansible-playbook site.yml --ask-vault-pass --tags tls
```

แนะนำตั้งรันทุกเดือน หรือเปลี่ยน `tls_cert_renew_days` ให้มากขึ้นถ้ารันไม่บ่อย

cert จะถูกออกใหม่อัตโนมัติด้วยเมื่อ: SAN เปลี่ยน (เพิ่ม node, เปลี่ยน VIP, แก้ `tls_extra_sans`) หรือ CA เปลี่ยน

## เปลี่ยน CA (เมื่อ CA หายหรือรั่ว)

1. แจ้งทีม app ล่วงหน้า — ต้องเปลี่ยน `ca.crt` พร้อมกัน
2. ย้าย `inventory/pki/` เดิมออก แล้วรัน `ansible-playbook site.yml --ask-vault-pass --tags tls -e tls_allow_ca_change=true`
3. แจก `inventory/pki/ca.crt` ใหม่

ช่วงที่ node ได้ cert จาก CA ใหม่ไม่พร้อมกัน etcd/Patroni ระหว่าง node จะคุยกันไม่ได้ชั่วคราว → ทำใน maintenance window
และ `pctl pause` ก่อน

## ตัวแปร

| ตัวแปร | ค่าเริ่มต้น | ความหมาย |
|---|---|---|
| `tls_enabled` | `true` | เปิด/ปิด TLS ทั้งหมด |
| `tls_postgres_enforce` | `false` | `true` = PostgreSQL รับเฉพาะ SSL |
| `tls_extra_sans` | `[]` | ชื่อเพิ่มใน cert เช่น `DNS:pgdb.example.local` |
| `tls_cert_valid_days` / `tls_cert_renew_days` | 825 / 30 | อายุ cert ของ node / ต่ออายุเมื่อเหลือน้อยกว่า |
| `tls_ca_valid_days` | 3650 | อายุ CA |
| `tls_key_type` / `tls_key_size` | `RSA` / 3072 | หรือ `ECC` (secp384r1) |
| `tls_min_protocol` | `TLSv1.2` | TLS ขั้นต่ำ (PostgreSQL, etcd, HAProxy) |
| `tls_postgres_internal_sslmode` | `verify-full` | sslmode ของ Patroni ↔ PostgreSQL (replication / rewind / superuser) |
| `tls_pki_dir` | `<inventory>/pki` | ที่เก็บ CA บน control node |
| `tls_dir` | `/etc/pki/pgha` | ที่เก็บ cert บน node |
| `tls_allow_ca_change` | `false` | อนุญาตให้เปลี่ยน CA |
| `vault_tls_ca_passphrase` | — (vault) | รหัสของ `ca.key` |

## ปัญหาที่อาจเจอ

| อาการ | สาเหตุ / วิธีแก้ |
|---|---|
| `runs etcd over http ... but tls_enabled=True wants https` | cluster เดิมยังไม่มี TLS → ใช้ `playbooks/enable-tls.yml` |
| `node(s) already have /etc/pki/pgha/ca.crt, but .../pki/ca.crt is missing` | รันจาก control node ที่ไม่มี `inventory/pki/` → copy จากเครื่องเดิม / Git |
| `ca.crt on <node> differs from .../pki/ca.crt` | ใช้ pki คนละชุดกับที่สร้าง cluster |
| `couldn't resolve module/action 'community.crypto...'` | ยังไม่ได้ติดตั้ง collection → `ansible-galaxy collection install -r requirements.yml` |
| app: `server certificate for "pg-node-1" ... does not match host name` | ชื่อที่ app ใช้ไม่อยู่ใน SAN → เพิ่มใน `tls_extra_sans` แล้วรัน `--tags tls` |
| app: `certificate verify failed` / `self-signed certificate in certificate chain` | app ยังไม่ได้ใช้ `ca.crt` ของ cluster (`sslrootcert`) |
| `etcdctl` ขึ้น `connection refused` / `bad certificate` | etcd ใช้ https + client cert → ใช้ `ectl` หรือใส่ `--cacert --cert --key` (ดูด้านบน) |
| HAProxy ทุก server DOWN หลังเปลี่ยน cert | `curl --cacert /etc/pki/pgha/ca.crt https://<node>:8008/primary` ต้องได้ 200 |
