# QuantoraTrade

แพลตฟอร์มช่วยวิเคราะห์และพัฒนาระบบเทรดเชิงปริมาณ รองรับสินทรัพย์หลายประเภท โดยเริ่มทดสอบกับ **XAUUSD และตลาด Forex** และออกแบบให้ขยายตลาดได้โดยไม่ผูก logic กับสัญลักษณ์ใดสัญลักษณ์หนึ่ง

> สถานะ: Phase 1–2 merge แล้ว และ Phase 3 — Backtesting implementation เสร็จแล้วใน PR #9

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

## Phase 2 — Technical Strategy (เสร็จแล้ว)

Indicator engine คำนวณ EMA 9/21/50, RSI 14, MACD 12/26/9 และ ATR 14 ด้วย `Decimal` จาก closed candles ที่เรียงตามเวลาเท่านั้น ผลลัพธ์ทุกจุดเป็น causal และไม่อ่านข้อมูล candle ในอนาคต

Market structure engine ยืนยัน swing high/low หลังแท่งด้านขวาปิดครบก่อนสร้าง Support/Resistance zones และ pattern engine ตรวจ Doji, Hammer, Shooting Star และ Engulfing โดยไม่ใช้ข้อมูลอนาคต

Signal schema ระบุ `symbol`, `timeframe`, `BUY/SELL/HOLD`, confidence, strategy version,
reason codes แบบคงที่ และเวลา observed/expiry โดยสร้าง identity ซ้ำได้จาก input เดิม
Strategy configuration รองรับค่า global และ per-symbol override แบบ immutable พร้อม validation
เพื่อให้การคำนวณย้อนหลังและหลายสินทรัพย์ใช้ config ที่ตรวจสอบได้

## Phase 3 — Backtesting (implementation เสร็จแล้ว)

Simulation clock รวม closed candles หลาย symbol/timeframe ตาม UTC โดยให้ context timeframe
เกิดก่อน entry timeframe เมื่อปิดพร้อมกัน และใช้ canonical symbol เป็นลำดับตัดสินที่ทำซ้ำได้

Execution foundation เข้า market ได้เร็วสุดที่ราคาเปิดแท่งถัดไป พร้อม spread, slippage และ
commission แบบ adverse-cost ส่วน Portfolio Accounting ติดตาม cash, realized/unrealized P&L,
equity, open positions และ closed trades ด้วย tick specification ของแต่ละ symbol

Protective exit simulator รองรับ SL/TP และ gap โดยเลือก stop-loss ก่อนเมื่อข้อมูล OHLC
ไม่สามารถบอกลำดับการแตะ SL/TP ได้ ส่วน Backtest Engine เชื่อม pending signal เข้ากับ
next-bar fill, protective exit และ portfolio mark โดยไม่เปลี่ยน state ย้อนหลัง

Broker simulation รับ margin-per-lot และ liquidity cap แยกตาม symbol จาก specification
ที่กำหนดไว้ล่วงหน้า จากนั้นปัด volume ลงตาม min/max/step และคืนผล FULL/PARTIAL/REJECTED
พร้อม reason codes โดย Portfolio แสดง margin used/free margin และคิด swap ราย weekday
รวม triple-swap day แบบ deterministic ส่วน commission คิดตาม filled volume จริง

Trade Journal บันทึก position, signal และ opening/closing fill IDs พร้อม reference/fill prices,
holding time, gross P&L, execution cost, commission และ net P&L โดยตรวจ reconciliation กับ
final portfolio ส่วน Evaluation layer คำนวณ return, expectancy, win/loss quality, profit factor,
streak และ high-water-mark drawdown ด้วย `Decimal`

Dataset splitter แบ่ง Training/Validation/Test ตามลำดับเวลาเท่านั้น และรองรับ purge/embargo
จากช่วง label เพื่อป้องกันข้อมูลอนาคตรั่วข้าม partition

Experiment configuration ล็อก code commit, dataset checksum, strategy/risk/engine versions,
broker profile, symbols, timeframe, period, cost scenario และ random seed เพื่อสร้าง reproducibility manifest
กับ run ID แบบ deterministic ส่วน Baseline Report สรุป overall, แต่ละ partition และแต่ละ symbol
เทียบ no-trade baseline พร้อม JSON artifacts และ SHA-256 checksums โดยสถานะเป็น
`RESEARCH_ONLY` เสมอ

Complete experiment runner ผูกทุก order กับ point-in-time sample และ partition ที่ระบุไว้
ส่ง order หลัง source candle ถูกสังเกตแล้วเท่านั้น และปฏิเสธ run ที่จบพร้อม pending order หรือ
open position โดย Event Replay Journal บันทึก fills, exits, rejection reasons และ portfolio state
ทุก event สำหรับตรวจย้อนหลัง

ผลลัพธ์ถูกสร้างเป็น `summary.json`, `manifest.json`, `trades.json`, `events.json`,
`report.html` และ `checksums.json` จากนั้นเขียนผ่าน staging directory ตรวจ SHA-256 และ publish
แบบ atomic Golden regression test ครอบคลุม XAUUSD/EURUSD และ Training/Validation/Test
ด้วย report hash ที่ล็อกไว้ ทั้งหมดนี้เป็นหลักฐานด้านความถูกต้องของ framework ไม่ใช่หลักฐานว่า
กลยุทธ์ทำกำไรบนข้อมูลตลาดจริง

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
14. [Research Evidence Base](docs/14_RESEARCH_EVIDENCE_BASE.md)

Phase 0–3 implementation ครบตาม roadmap แล้ว ขั้นถัดไปคือ Phase 4 — AI Research

## สถานะสำคัญ

โครงการนี้อยู่ในขั้นออกแบบและวิจัย ห้ามเปิด Live Trade จนกว่าจะผ่านเกณฑ์ Backtest, Paper Trade และการอนุมัติจากเจ้าของโครงการ
