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
