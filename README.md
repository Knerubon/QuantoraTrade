# QuantoraTrade

แพลตฟอร์มช่วยวิเคราะห์และพัฒนาระบบเทรดเชิงปริมาณ รองรับสินทรัพย์หลายประเภท โดยเริ่มทดสอบกับ **XAUUSD และตลาด Forex** และออกแบบให้ขยายตลาดได้โดยไม่ผูก logic กับสัญลักษณ์ใดสัญลักษณ์หนึ่ง

> สถานะ: Phase 1 — Market Data เสร็จแล้ว และกำลังพัฒนา Phase 2 — Technical Strategy

## เป้าหมาย

QuantoraTrade จะรวมข้อมูลตลาด การวิเคราะห์ทางเทคนิค โมเดล AI การควบคุมความเสี่ยง การตัดสินใจ และการส่งคำสั่งซื้อขายไว้ในระบบเดียว โดยทุกผลลัพธ์ต้องตรวจสอบย้อนหลังได้

## หลักการสำคัญ

- เริ่มจาก Backtest ก่อน Paper Trade และ Live Trade
- Risk Management มีสิทธิ์ปฏิเสธทุกสัญญาณ
- แยก Signal, Decision และ Execution ออกจากกัน
- บันทึกข้อมูล เหตุผล และผลลัพธ์ของทุกการตัดสินใจ
- Live Trade ปิดไว้เป็นค่าเริ่มต้น
- ไม่มีระบบใดรับประกันกำไร

## ขอบเขตเวอร์ชันแรก

- สินทรัพย์: Metals และ Forex เช่น XAUUSD, EURUSD, GBPUSD และ USDJPY
- Symbol, timeframe, trading session และกฎเฉพาะสินทรัพย์กำหนดผ่าน configuration
- แหล่งข้อมูล/Execution: MetaTrader 5
- โหมด: Backtest, Paper Trade, Live Trade
- สัญญาณ: BUY, SELL, HOLD พร้อม confidence และเหตุผล
- การวิเคราะห์พื้นฐาน: EMA, RSI, MACD, ATR, Support/Resistance และ Candlestick Pattern
- การบริหารความเสี่ยง: Position Size, Stop Loss, Take Profit, Daily Loss Limit และ Consecutive Loss Guard
- การติดตาม: Database, API, Dashboard และ Telegram Alert

## Phase 1 — Market Data (เสร็จแล้ว)

Market Data Layer รองรับการแปลงข้อมูล MT5 เป็น Domain Models, ตรวจ closed candles, symbol/timeframe identity, duplicates, ordering, gaps และ stale data โดยจะหยุดแบบ fail closed เมื่อข้อมูลไม่ปลอดภัย

ทดสอบแบบ read-only บน Windows ที่ติดตั้ง MT5 Terminal:

```bash
python -m pip install -e ".[mt5]"
python scripts/check_mt5_market_data.py --symbol XAUUSD --timeframe M15 --limit 100
```

คำสั่งนี้อ่าน Symbol Specification และ OHLCV เท่านั้น ไม่มีการส่งหรือแก้ไข Order

Market Data Storage ใช้ PostgreSQL schema `quantora`:

```bash
export QUANTORA_DATABASE_URL=postgresql+psycopg://quantora:change-me@localhost:5432/quantora
alembic upgrade head
```

Storage เก็บ Raw Rates แบบ append-only/deduplicated และ Normalized Candles แบบ upsert พร้อม source และ payload checksum

Symbol Specification ครอบคลุม digits, point, pip/tick size, tick value, contract size, volume limits, observed spread และ session identity โดยค่าจำกัด spread และ session profile กำหนดแยกตาม symbol ใน configuration

ก่อนรับรอง MT5 environment ใหม่ ให้ทำตาม [MT5 Terminal Validation Checklist](docs/13_MT5_TERMINAL_VALIDATION.md)

## Phase 2 — Technical Strategy (กำลังพัฒนา)

Indicator engine คำนวณ EMA 9/21/50, RSI 14, MACD 12/26/9 และ ATR 14 ด้วย `Decimal` จาก closed candles ที่เรียงตามเวลาเท่านั้น ผลลัพธ์ทุกจุดเป็น causal และไม่อ่านข้อมูล candle ในอนาคต

## เอกสารโครงการ

1. [Vision](docs/01_VISION.md)
2. [Product Requirements](docs/02_PRODUCT_REQUIREMENTS.md)
3. [Roadmap](docs/03_ROADMAP.md)
4. [System Architecture](docs/04_SYSTEM_ARCHITECTURE.md)
5. [AI Agents](docs/05_AI_AGENTS.md)
6. [Trading Logic](docs/06_TRADING_LOGIC.md)
7. [Database Design](docs/07_DATABASE_DESIGN.md)
8. [API Specification](docs/08_API_SPECIFICATION.md)
9. [Risk Management](docs/09_RISK_MANAGEMENT.md)
10. [Backtesting Framework](docs/10_BACKTESTING_FRAMEWORK.md)
11. [Coding Standards](docs/11_CODING_STANDARDS.md)
12. [Project Decisions](docs/12_PROJECT_DECISIONS.md)
13. [MT5 Terminal Validation Checklist](docs/13_MT5_TERMINAL_VALIDATION.md)

Phase 0 Foundation และ Phase 1 Market Data เสร็จแล้ว ขั้นถัดไปคือ Phase 2 Technical Strategy

## สถานะสำคัญ

โครงการนี้อยู่ในขั้นออกแบบและวิจัย ห้ามเปิด Live Trade จนกว่าจะผ่านเกณฑ์ Backtest, Paper Trade และการอนุมัติจากเจ้าของโครงการ
