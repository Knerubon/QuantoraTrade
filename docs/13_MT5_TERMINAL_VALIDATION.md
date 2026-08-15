# 13 — MT5 Terminal Validation Checklist

ใช้ checklist นี้เพื่อรับรอง MT5 Terminal และบัญชี broker แต่ละ environment ก่อนนำข้อมูลไปใช้กับ Backtest, Paper Trade หรือ Live Pilot

## Preconditions

- [ ] ใช้ Windows host ที่ติดตั้ง MetaTrader 5 และล็อกอินบัญชีที่ต้องการตรวจแล้ว
- [ ] เปิด symbols ที่ทดสอบใน Market Watch: XAUUSD, EURUSD, GBPUSD และ USDJPY ตามที่ broker รองรับ
- [ ] ติดตั้ง package ด้วย `python -m pip install -e ".[mt5]"`
- [ ] ไม่มี credential, account number หรือ server secret ถูก commit ลง repository

## Read-only smoke test

รันคำสั่งต่อ symbol และ timeframe ที่เปิดใช้งาน:

```powershell
python scripts/check_mt5_market_data.py --symbol XAUUSD --timeframe M15 --limit 100
python scripts/check_mt5_market_data.py --symbol EURUSD --timeframe M15 --limit 100
python scripts/check_mt5_market_data.py --symbol EURUSD --timeframe H1 --limit 100
```

- [ ] ทุกคำสั่งจบด้วย exit code 0
- [ ] symbol และ timeframe ในผลลัพธ์ตรงกับคำสั่ง
- [ ] ได้เฉพาะ closed candles และจำนวนไม่เกิน limit
- [ ] timestamps เป็น UTC, เรียงจากเก่าไปใหม่ และไม่มี duplicate
- [ ] gap หรือ stale candle ถูกแจ้งโดย data-quality validator

## Symbol specification

- [ ] ตรวจ digits, point, pip size และ tick size กับหน้า Specification ใน MT5
- [ ] ตรวจ tick value, contract size, min/max/step volume
- [ ] ตรวจ observed spread และเปรียบเทียบกับ `max_spread_points` ใน config
- [ ] ยืนยัน `session_timezone` และ `session_profile` ให้ตรงกับ broker/server

## Storage replay

- [ ] รัน PostgreSQL migration ด้วย `alembic upgrade head`
- [ ] นำเข้า XAUUSD และ EURUSD ในช่วงเวลาทับซ้อนกัน
- [ ] replay batch เดิมแล้วจำนวน Raw Rates และ Candles ไม่เพิ่มซ้ำ
- [ ] query แต่ละ symbol/timeframe แล้วไม่พบข้อมูลข้ามกัน
- [ ] บันทึก broker, account type, server, package version, เวลา และผลทดสอบไว้ใน deployment log

## Approval

- [ ] ผู้ทดสอบยืนยันว่าเป็น read-only ไม่มีการสร้าง แก้ไข หรือยกเลิก order
- [ ] เจ้าของโครงการอนุมัติ environment สำหรับ Market Data เท่านั้น

การผ่าน checklist นี้ไม่ใช่การอนุมัติ Live Trading ซึ่งยังต้องผ่าน Backtest, Paper Trade, Risk และ Controlled Live Pilot ตาม Roadmap
