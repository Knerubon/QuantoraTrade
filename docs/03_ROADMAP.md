# 03 — Roadmap

## Phase 0 — Foundation

- [x] ตั้งชื่อ QuantoraTrade
- [x] สร้าง GitHub repository
- [x] กำหนด Vision
- [x] กำหนด Product Requirements เบื้องต้น
- [x] กำหนด Architecture และ Technology Stack แบบ Multi-Asset
- [ ] กำหนด Coding Standards และ branching strategy
- [ ] สร้างโครง Python project และ CI

## Phase 1 — Data Layer

- [ ] เชื่อมต่อ MetaTrader 5
- [ ] ดึง OHLCV ของ XAUUSD และคู่เงิน Forex หลาย symbols
- [ ] สร้าง Symbol Specification สำหรับ digits, pip/tick size, tick value, contract size, session และ spread
- [ ] ตรวจ data quality
- [ ] จัดเก็บ raw/processed data
- [ ] สร้าง test fixtures

**Exit criteria:** นำเข้าข้อมูลหลาย symbols ซ้ำได้ ผลตรงกัน และตรวจจับ missing/duplicate candles แยกตาม symbol ได้

## Phase 2 — Technical Strategy

- [ ] EMA 9/21/50
- [ ] RSI, MACD และ ATR
- [ ] Support/Resistance
- [ ] Candlestick Pattern
- [ ] Signal schema: symbol + timeframe + BUY/SELL/HOLD
- [ ] Strategy configuration แบบ global และ per-symbol override
- [ ] Unit tests

**Exit criteria:** ไม่มี look-ahead bias และ signal ของแต่ละ symbol ทำซ้ำได้จากข้อมูล/config เดิม

## Phase 3 — Backtesting

- [ ] Backtest engine
- [ ] Spread, commission และ slippage
- [ ] Metrics และ trade journal
- [ ] Train/validation/out-of-sample split
- [ ] Baseline report

**Exit criteria:** รายงานผลครบ ตรวจย้อนหลังได้ และมี out-of-sample result

## Phase 4 — AI Research

- [ ] Feature pipeline
- [ ] Dataset versioning
- [ ] Baseline model
- [ ] Walk-forward validation
- [ ] Model registry และ inference interface

**Exit criteria:** AI มีผลทดสอบเทียบ baseline และไม่เชื่อมกับ execution โดยตรง

## Phase 5 — Risk & Decision

- [ ] Position sizing
- [ ] SL/TP rules
- [ ] Daily loss และ drawdown guard
- [ ] Consecutive-loss cooldown
- [ ] Decision engine
- [ ] Kill Switch

**Exit criteria:** ทุก order ผ่าน Risk Engine และทดสอบ rejection cases ครบ

## Phase 6 — Paper Trading

- [ ] Paper execution adapter
- [ ] Order lifecycle
- [ ] Database logging
- [ ] FastAPI control plane
- [ ] Telegram alerts
- [ ] Monitoring dashboard

**Exit criteria:** Paper Trade ต่อเนื่องโดยไม่มี critical incident และผล audit ครบ

## Phase 7 — Controlled Live Pilot

- [ ] Security review
- [ ] Live execution adapter
- [ ] Small-capital configuration
- [ ] Manual approval
- [ ] Incident and rollback plan

**Exit criteria:** เปิด Live Trade ได้เฉพาะเมื่อเจ้าของโครงการอนุมัติ และสามารถหยุดระบบทันที

## เอกสารที่จะจัดทำต่อ

1. Project Decisions


## Documentation Progress

- [x] AI Agents architecture and safety policy
- [x] Multi-Asset Trading Logic
- [x] PostgreSQL Database Design
- [x] FastAPI Specification and Control Safeguards
- [x] Multi-Asset Risk Management
- [x] Reproducible Backtesting Framework
- [x] Coding Standards and CI Quality Gates
