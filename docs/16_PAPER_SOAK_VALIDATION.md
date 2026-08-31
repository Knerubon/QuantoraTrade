# 16 — PAPER Soak Validation Runbook

เอกสารนี้เป็น checklist สำหรับเก็บหลักฐานตาม Phase 6 exit criterion เท่านั้น ชุดประเมิน
**ไม่เริ่ม worker, ไม่เชื่อม broker และไม่ส่ง order** ผู้ดูแลต้องเริ่ม/หยุด PAPER environment
ด้วยขั้นตอนปฏิบัติการที่อนุมัติแยกต่างหาก และห้ามใช้เอกสารนี้เป็นสิทธิ์เปิด LIVE

## Preconditions (owner ต้องอนุมัติและกำหนดค่า)

- [ ] ระบุ owner, run ID และระยะเวลา soak ที่ต้องการจริง ห้ามใช้ค่าที่ระบบเลือกเอง
- [ ] ยืนยัน environment เป็น `PAPER`; หากเป็น `LIVE` ต้องยุติทันที
- [ ] บันทึก Git commit (`code_version`), immutable config version และ SHA-256
- [ ] บันทึก MT5 broker/server/account แบบไม่เปิดเผย secret และช่วง current-data (`data_version`)
- [ ] ผ่าน `13_MT5_TERMINAL_VALIDATION.md` สำหรับ terminal/broker ปัจจุบัน
- [ ] เปิด durable database logging, reconciliation, health checks และ alert route
- [ ] ทดสอบ kill switch และขั้นตอนหยุดระบบก่อนเริ่มจับเวลา
- [ ] กำหนด sample interval และเกณฑ์ล่วงหน้า ห้ามปรับเกณฑ์หลังเห็นผล; target duration ต้องไม่น้อยกว่าและหารด้วย interval ลงตัว

## Observation contract

เก็บ JSON object ที่มี `manifest`, cumulative `samples`, `incidents` และ pre-registered `gates`.
แต่ละ sample ต้องใช้ UTC และบันทึก readiness, orders observed, duplicate order count,
unknown lifecycle count, audited order count และ critical event count. Incident log ต้องเก็บ UTC,
severity, stable reason code และ sanitized summary โดยห้ามบันทึก token/password/account secret.

ตัว evaluator ต้องถูกเรียกโดยผู้ดูแลอย่างชัดเจนหลังจบ run:

```powershell
python scripts/evaluate_paper_soak.py `
  --input artifacts/paper-soak/observations.json `
  --output-directory artifacts/paper-soak/reports `
  --acknowledge-paper-only
```

คำสั่งคืน exit code `0` เมื่อทุก gate ผ่าน, `2` เมื่อมี gate ไม่ผ่าน และไม่ overwrite รายงานเดิม.
JSON/Markdown report มี checksum ของ input evidence เพื่อรองรับ audit/reproduction.

## Windows runner

`scripts/paper-soak.ps1` เป็น bounded runner สำหรับ control plane ที่เปิดอยู่แล้ว โดยทำงานตามลำดับ
preflight PAPER → enqueue Start → รอ acknowledgement → เก็บ `/status`, `/dashboard` และ events
ตาม cadence → enqueue controlled Stop ใน `finally` → เรียก evaluator ตัว runner ไม่เปิด LIVE,
ไม่รับ token ผ่าน command line และไม่บันทึก token ลง evidence.

ก่อนใช้ให้ติดตั้ง package, เปิด PostgreSQL/API/PAPER worker ตาม operational configuration และผ่าน
MT5 checklist จากนั้นตั้ง token เฉพาะใน PowerShell session:

```powershell
cd D:\QuantoraTrade
python -m pip install -e ".[mt5]"
$env:QUANTORA_API_TOKEN = Read-Host "PAPER API token"
$commit = git rev-parse HEAD
```

### Windows runtime smoke environment

ชุด runtime ใน `docker-compose.windows.yml` และ `scripts/windows-runtime.ps1` ใช้ตรวจ
PostgreSQL → migration → authenticated API → durable Start/Stop queue → worker lifecycle
บน Windows เท่านั้น ตัว worker สังเกต candle ที่ persist แล้วแต่ไม่มี signal/order source และติด
reason code `SMOKE_ONLY_NO_ORDER_EXECUTION` จึง **ห้ามใช้ output เป็น empirical soak evidence**.

```powershell
$env:QUANTORA_POSTGRES_PASSWORD = Read-Host "PostgreSQL password"
$env:QUANTORA_DATABASE_URL = "postgresql+psycopg://quantora:$env:QUANTORA_POSTGRES_PASSWORD@localhost:5432/quantora"
$env:QUANTORA_API_TOKEN = Read-Host "Local API token (24+ characters)"
$env:QUANTORA_TRADING_MODE = "paper"
$env:QUANTORA_SMOKE_ONLY = "true"

.\scripts\windows-runtime.ps1 Start
.\scripts\windows-runtime.ps1 Status
.\scripts\windows-runtime.ps1 Stop
```

`Stop` เก็บ PostgreSQL volume ไว้และไม่ลบหลักฐาน หากต้องการเปลี่ยนจาก smoke ไป empirical soak
ต้องมี MT5 current-data adapter, approved-intent source และ production PAPER composition แยกต่างหาก.

ตัวอย่าง smoke run 2 นาที (เป็นการตรวจ runner เท่านั้น ไม่ใช่หลักฐานปิด Phase 6):

```powershell
.\scripts\paper-soak.ps1 `
  -ApiUrl "http://127.0.0.1:8000" `
  -Owner "Nerubon" `
  -RunId "paper-smoke-$(Get-Date -Format yyyyMMdd-HHmmss)" `
  -DurationSeconds 120 `
  -IntervalSeconds 60 `
  -Symbols XAUUSD `
  -StrategyId "technical-v1" `
  -ConfigVersion "paper-candidate-v1" `
  -Config ".\config\risk.example.yaml" `
  -DataVersion "REPLACE_WITH_MT5_BROKER_SERVER_AND_UTC_RANGE" `
  -CodeVersion $commit `
  -Output ".\artifacts\paper-soak\observations-smoke.json" `
  -AcknowledgePaperOnly
```

สำหรับ owner-approved run ให้เปลี่ยน `RunId`, `DurationSeconds`, `IntervalSeconds`, config/data
version และ output ใหม่ก่อนเริ่ม ห้าม reuse output เดิม ตัวอย่าง 7 วัน/5 นาทีใช้
`DurationSeconds 604800` และ `IntervalSeconds 300` และต้องปล่อย PowerShell process ทำงานต่อเนื่อง.
หากหน้าต่างปิด, เครื่อง sleep, API/MT5 ขาดการเชื่อมต่อ หรือ cadence ไม่ครบ ให้ถือว่า run นั้น FAIL
และเริ่ม run ID ใหม่หลังแก้ incident.

Evidence จะถูกเขียนแบบ atomic ทุก sample ที่ path จาก `-Output`; รายงานอยู่ในโฟลเดอร์
`reports` ข้างไฟล์ evidence. การได้ verdict PASS ยังต้องผ่าน reconciliation และ owner/lead sign-off
ตามหัวข้อ Real MT5/current-data closeout ด้านล่าง.

## Pass/fail gates

- sample แรกต้องอยู่ใน `10%` ของ owner-set interval หลังเวลาเริ่ม (อย่างน้อยให้ tolerance 1 วินาที)
- gap ทุกคู่ต้องอยู่ใน `interval +/- 10%` (อย่างน้อย 1 วินาที) เพื่อปฏิเสธทั้ง sample ที่กองรวมกันและช่วงข้อมูลขาด
- sample สุดท้ายต้องอยู่ใน `10%` ของ interval จาก target end; ระยะเวลาและจำนวน sample (`ceil(duration/interval)+1`) ต้องครบด้วย
- unhealthy samples, duplicates, unknown orders และ critical incidents ต้องไม่เกินค่าที่ลงทะเบียนไว้
- order ทุกตัวต้องมี audit trail หากเปิด `require_complete_audit` (ค่าเริ่มต้นคือเปิด)
- การผ่านหมายถึงเฉพาะ run/config/code/data ที่อยู่ใน manifest เท่านั้น

## Real MT5/current-data closeout

- [ ] หยุด PAPER worker ด้วย controlled stop และยืนยันว่าไม่มี pending lifecycle ค้าง
- [ ] reconcile orders/fills/positions/accounting กับ PAPER account snapshot
- [ ] export sanitized observations และ incident log แบบ append-only
- [ ] รัน evaluator และเก็บ JSON/Markdown พร้อม checksum ใน approved evidence store
- [ ] ให้ owner/lead review incident ทุกระดับและลงนามผลแยกจากตัว evaluator
- [ ] หาก FAIL ให้เปิด incident/corrective action และรันใหม่ด้วย run ID ใหม่ ห้ามแก้รายงานเดิม

Phase 6 ยังถือว่า **empirical soak pending** จนกว่าจะมี owner-approved duration บน terminal,
broker และ current data จริง พร้อมหลักฐาน audit ที่ reviewer ยอมรับ การมี harness หรือ unit tests
เพียงอย่างเดียวไม่ทำให้ exit criterion เสร็จ และไม่อนุญาต Phase 7/LIVE.
