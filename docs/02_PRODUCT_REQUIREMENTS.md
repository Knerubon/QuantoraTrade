# 02 — Product Requirements (MVP)

## 1. เป้าหมาย MVP

สร้างระบบต้นแบบสำหรับ XAUUSD ที่รับข้อมูลจาก MetaTrader 5 วิเคราะห์สัญญาณ ประเมินความเสี่ยง ตัดสินใจ BUY/SELL/HOLD และทดสอบผ่าน Backtest กับ Paper Trade ได้ โดยยังไม่เปิด Live Trade เป็นค่าเริ่มต้น

## 2. Functional Requirements

### FR-01 Market Data

- รับ OHLCV จาก MT5
- รองรับ timeframe อย่างน้อย M5, M15 และ H1
- ตรวจ timestamp, missing candles, duplicate candles และลำดับข้อมูล
- เก็บข้อมูลดิบแยกจากข้อมูลที่ผ่านการแปลง

### FR-02 Technical Analysis

- คำนวณ EMA 9/21/50
- คำนวณ RSI, MACD และ ATR
- ตรวจ Support/Resistance และ Candlestick Pattern
- ไม่ใช้ข้อมูลแท่งอนาคตในการคำนวณ

### FR-03 Signal Engine

- คืนค่า BUY, SELL หรือ HOLD
- มี confidence, reasons, strategy version และ timestamp
- ผลลัพธ์จากข้อมูลเดียวกันและ config เดียวกันต้องทำซ้ำได้

### FR-04 AI Filter

- รับ features ที่กำหนดเวอร์ชันไว้
- ส่ง prediction/confidence ให้ Decision Engine
- ห้ามส่ง Order โดยตรง
- เมื่อ model ใช้งานไม่ได้ ระบบต้อง fallback เป็น HOLD หรือกฎที่กำหนดไว้อย่างปลอดภัย

### FR-05 Risk Management

- คำนวณ position size จาก equity และ risk per trade
- กำหนด Stop Loss และ Take Profit
- จำกัด daily loss, consecutive losses และจำนวนสถานะพร้อมกัน
- ปฏิเสธ order เมื่อข้อมูลไม่ครบ ตลาดผิดปกติ หรือเกินขีดจำกัด

### FR-06 Decision Engine

- รวม Technical Signal, AI, Risk และสถานะตลาด
- สร้าง final decision พร้อมเหตุผล
- HOLD เมื่อข้อมูลขัดแย้งหรือคุณภาพต่ำ

### FR-07 Backtesting

- รองรับค่าธรรมเนียม spread และ slippage
- ป้องกัน look-ahead bias
- รายงาน Return, Max Drawdown, Win Rate, Profit Factor, Expectancy และจำนวน Trade
- เก็บ config และผลการทดสอบทุกครั้ง

### FR-08 Paper Trading

- จำลองคำสั่งด้วยข้อมูลปัจจุบัน
- บันทึก order lifecycle และ P&L
- ใช้ logic เดียวกับ Live Trade เท่าที่ทำได้

### FR-09 Execution

- แยก paper และ live adapter
- ป้องกันคำสั่งซ้ำ
- รองรับ retry แบบจำกัดและตรวจสถานะ order
- Live adapter ต้อง disabled by default

### FR-10 Monitoring

- API ขั้นต้น: `/health`, `/status`, `/start`, `/stop`, `/trades`, `/report`
- Telegram แจ้งเตือน signal, order, risk rejection และ critical error
- Dashboard แสดงสถานะ P&L, drawdown, open positions และเหตุการณ์ล่าสุด

## 3. Non-functional Requirements

- Python เป็นภาษาหลักของระบบวิเคราะห์และ backend
- Config แยกจาก source code และตรวจ schema ก่อนเริ่มระบบ
- Secret อ่านจาก environment หรือ secret manager เท่านั้น
- มี unit, integration และ backtest regression tests
- Log ต้องเป็น structured log และมี correlation ID
- เวลาในระบบเก็บเป็น UTC และแปลงเฉพาะตอนแสดงผล
- component สำคัญต้องหยุดอย่างปลอดภัยเมื่อ dependency ล้มเหลว

## 4. Acceptance Gate

### Backtest → Paper Trade

- ไม่มี look-ahead bias ที่ตรวจพบ
- มีผลทดสอบ out-of-sample
- Drawdown อยู่ในเพดานที่เจ้าของโครงการกำหนด
- ผลลัพธ์หลังรวมต้นทุนยังผ่านเกณฑ์

### Paper Trade → Live Trade

- Paper Trade ต่อเนื่องตามช่วงเวลาที่กำหนด
- ไม่มีคำสั่งซ้ำหรือ incident ระดับ critical
- Risk Guard และ Kill Switch ผ่านการทดสอบ
- เจ้าของโครงการอนุมัติด้วยตนเอง

## 5. Open Decisions

ค่าต่อไปนี้ยังต้องตัดสินใจก่อน implement กลยุทธ์จริง:

- เงินทุนอ้างอิง
- risk per trade
- daily loss limit
- maximum drawdown
- session ที่อนุญาตให้เทรด
- spread/slippage สูงสุด
- เกณฑ์ผ่าน Backtest และ Paper Trade
