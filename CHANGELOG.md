# Changelog

## v2.1.3
- เพิ่ม CI สำหรับ GitHub Actions และ GitLab CI (yamllint, ansible-lint, syntax-check), `.gitattributes` (LF)
- แก้: `verify.yml` รายงาน `Not running on <node>: etcd, patroni, haproxy, keepalived` ทั้งที่ทุก service รันอยู่
- สาเหตุ (ansible-core 2.19+): regex ที่มี `\\` ใน YAML แบบ plain / `>-` ถูกส่งถึง Jinja เป็น backslash 2 ตัว → regex ไม่ match
  แก้ทุกจุดที่เป็นแบบนี้: verify (ชื่อ service), ปิด module `postgresql` ของ AppStream (EL8/9 + PGDG — เดิมไม่ได้ปิด),
  ตรวจ/เปิด module stream (EL8/9 + AppStream), preflight, และ `enable-tls.yml` ขั้น 4a (เดิมจะรันซ้ำแม้ทำเสร็จแล้ว)

## v2.1.2
- แก้: `'ansible_managed' is undefined` ที่ task "Shell helper ..." ของ etcd / Patroni และไฟล์ .dcs-connection —
  ansible-core 2.19 ขึ้นไปมีตัวแปร `ansible_managed` เฉพาะใน module `template` ใช้ใน `copy content:` ไม่ได้
  (บั๊กนี้มีตั้งแต่ v2.0.0 แต่จะเจอเมื่อใช้ ansible-core รุ่นใหม่) → เปลี่ยนเป็นข้อความคงที่

## v2.1.1
- แก้: preflight และการเซ็น cert บน control node พยายาม `sudo` บนเครื่องที่รัน Ansible
  (`sudo: a password is required` เมื่อ user ที่รัน Ansible ไม่มี sudo แบบไม่ถามรหัส) — สาเหตุคือ inventory ตั้ง
  `ansible_become: true` ให้ทุก host ซึ่งชนะ keyword `become: false` จึงปิดด้วยตัวแปร `ansible_become: false` แทน

## v2.1.0 — TLS ทุกช่องทางด้วย CA ของ cluster เอง

### TLS (`tls_enabled: true` เป็นค่าเริ่มต้น) — รายละเอียด `docs/TLS.md`
- role ใหม่ `tls`: สร้าง CA บน control node (`inventory/pki/`, `ca.key` เข้ารหัสด้วย `vault_tls_ca_passphrase`),
  private key ของ node สร้างบน node เอง (ไม่ออกจากเครื่อง) → CSR → control node เซ็น (`community.crypto`)
  - SAN = ชื่อ/IP ทุก node + VIP + `tls_extra_sans` → app ใช้ `sslmode=verify-full` ผ่าน VIP ได้
  - ต่ออายุอัตโนมัติเมื่อเหลือน้อยกว่า `tls_cert_renew_days` และออกใหม่เมื่อ SAN/CA เปลี่ยน — reload ไม่ restart
  - ไม่ยอมสร้าง CA ใหม่เองถ้า node เชื่อ CA เดิมอยู่ (กันรันจาก control node ที่ไม่มี pki/)
  - guard: `site.yml` หยุดถ้า cluster ที่รันอยู่ใช้ http แต่ `tls_enabled: true` (หรือกลับกัน)
- PostgreSQL: `ssl=on`, `ssl_min_protocol_version` TLSv1.2; Patroni ต่อ replication / pg_rewind / superuser ด้วย
  `sslmode=verify-full`; `tls_postgres_enforce: true` = pg_hba `hostssl` + ปฏิเสธ connection ที่ไม่เข้ารหัส
- Patroni REST API https (`ctl.cacert` ให้ patronictl); HAProxy health check `check-ssl verify required`
- etcd: client + peer https พร้อม client certificate (mutual TLS); alias `ectl` บน node
- Patroni restart แบบ maintenance mode อัตโนมัติเมื่อการเชื่อมต่อ etcd เปลี่ยน (reload ไม่พอ)
- หน้า HAProxy stats เป็น https
- `playbooks/enable-tls.yml` (ใหม่): เปิด TLS บน cluster ที่ deploy แล้ว — etcd peer ย้ายผ่าน port ชั่วคราว 2381
  ทีละ member (quorum อยู่ตลอด), PostgreSQL `ssl=on` ด้วย reload ก่อน แล้วจึงสลับ Patroni + HAProxy, ไม่ restart PostgreSQL
  (ทดสอบ: INSERT ผ่าน VIP ทุก 0.3 วินาทีตลอดการย้าย สำเร็จ 308/308)
- `verify.yml`: ต่อผ่าน VIP ด้วย `sslmode=verify-full`, ตรวจว่า connection และ replication เข้ารหัสจริง
- preflight: ตรวจ `vault_tls_ca_passphrase` และ python `cryptography` / `community.crypto` บน control node
- pre-commit hook: กัน commit `pki/*.key` ที่ไม่ได้เข้ารหัส

### แก้ไข
- HAProxy: รหัสหน้า stats ที่มี `#` ถูกตัดทิ้ง (HAProxy ถือว่าเป็น comment) → quote ให้แล้ว
- HAProxy: บันทึกสถานะ server ก่อน reload (`server-state-file` + socat) — เดิมหลัง reload ทุก server ถูกถือว่า UP
  ชั่วขณะ ทำให้ write บางส่วนไปตก replica (`cannot execute INSERT in a read-only transaction`) จนกว่า health check จะรอบแรกเสร็จ

## v2.0.0 — Rocky Linux / RHEL 8, 9, 10 + IP ที่ใส่เอง

### OS
- รองรับ Rocky Linux และ RHEL 8, 9, 10 (AlmaLinux / Oracle Linux ใช้ได้ด้วย) แทน Oracle Linux 10 อย่างเดียว
- `playbooks/detect_os.yml` (ใหม่, รันเสมอ): อ่าน `/etc/os-release` ด้วย `raw`, ตรวจ OS ที่รองรับ,
  บังคับให้ทุก node เป็น major เดียวกัน, ตรวจว่า EL8 ใช้ ansible-core 2.16 และตั้ง `ansible_python_interpreter`
  เป็น Python ของระบบ (EL8 `/usr/libexec/platform-python`, EL9/10 `/usr/bin/python3`)
- Patroni venv ใช้ Python ตาม major version (`patroni_python_by_major`): EL8/9 = `python3.12`, EL10 = `python3`
  เพราะ psycopg 3.3 ต้องใช้ Python ≥ 3.10 + ตรวจเวอร์ชันและป้องกัน venv ที่สร้างด้วย Python คนละตัว
- PostgreSQL `pg_install_source: auto` (ค่าเริ่มต้นใหม่): EL8/9 → PGDG, EL10 → AppStream
  - PGDG: ปิด module `postgresql` ของ AppStream บน EL8/9, `priority=1` แทน `excludepkgs` บน repo ของ OS
    (repo id ต่างกันทุก distro และ RHEL ถูก subscription-manager เขียนทับ), pin baseurl เฉพาะ EL10, GPG key ตาม arch
  - AppStream: EL8/9 enable module stream `postgresql:<ver>`, EL10 ใช้แพ็กเกจ `postgresql<ver>`
  - error ที่บอกวิธีแก้เมื่อแพ็กเกจไม่มีใน repo
- base packages ไม่ผูกกับ Oracle Linux (setuptools ของ Python ระบบตาม major, เพิ่ม `iputils`)
- keepalived build เพิ่ม `pkgconf-pkg-config`, HAProxy แบบ package ตรวจว่าเป็น EL9+ (EL8 มี 1.8)
- etcd รองรับ ppc64le / s390x เพิ่ม
- `ansible.cfg`: `interpreter_python = auto_silent`

### IP / VIP ใส่เอง
- `inventory/hosts.yml` เป็นแม่แบบ (`<IP-node-1>`, `<CIDR-cluster>`, `<VIP>`) ไม่มี IP ของไซต์ใดฝังอยู่
- `scripts/setup-inventory.py` (ใหม่): ถาม-ตอบหรือ argument (`--ips`, `--nodes`, `--vip`, ...) ตรวจค่าแล้วเขียน `hosts.yml`
  พร้อมสำรองไฟล์เดิม — ใช้ค่าเดิมเป็น default เมื่อรันซ้ำ, ใช้แค่ standard library (Python 3.6+)
- preflight มี play แรกที่ตรวจค่าใน inventory บน control node ก่อน SSH: placeholder ที่เหลือ, IPv4, IP ซ้ำ,
  อยู่ใน subnet, VIP ไม่ชน node, network/broadcast, prefix, virtual_router_id, ชื่อ host
- `keepalived_enabled`, `keepalived_router_id`, `keepalived_interface` ย้ายไปเป็นค่าของไซต์ใน `hosts.yml`
  (ไม่ประกาศใน `group_vars/all` แล้ว เพื่อให้ override ได้จริง)
- interface ของ VIP เลือกจาก interface ที่มี IP ของ node (เดิมใช้ interface ของ default route)
- preflight ตรวจว่า interface ของ VIP มีอยู่จริง และ repo ของ OS มีแพ็กเกจที่ต้องใช้
- ชื่อ node default เป็น `pg-node-N`, เอกสารใช้ตัวแปร shell (`$NODE1_IP`, `$VIP`) แทน IP ตายตัว

## v1.0.0
- Oracle Linux 10, PostgreSQL 18 (AppStream/PGDG), etcd 3.7, Patroni 4.1, HAProxy 3.4 / keepalived 2.4 จาก source
