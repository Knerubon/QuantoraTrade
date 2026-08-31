# 03 — Roadmap

## Phase 0 — Foundation

- [x] ตั้งชื่อ QuantoraTrade
- [x] สร้าง GitHub repository
- [x] กำหนด Vision
- [x] กำหนด Product Requirements เบื้องต้น
- [x] กำหนด Architecture และ Technology Stack แบบ Multi-Asset
- [x] กำหนด Coding Standards และ branching strategy
- [x] สร้างโครง Python project และ CI

## Phase 1 — Data Layer

- [x] สร้าง MT5 Market Data Adapter แบบ read-only
- [x] รองรับการดึงและ normalize OHLCV ของหลาย symbols ผ่าน adapter
- [x] สร้าง Symbol Specification สำหรับ digits, pip/tick size, tick value, contract size, session และ spread
- [x] ตรวจ data quality แบบ fail closed
- [x] จัดเก็บ Raw Rates และ Normalized Candles ลง PostgreSQL แบบ idempotent
- [x] สร้าง Fake Gateway และ unit tests
- [x] เพิ่ม PostgreSQL integration tests สำหรับ multi-symbol isolation และ idempotent replay
- [x] จัดทำ checklist สำหรับตรวจ MT5 Terminal จริงแบบ read-only

**Exit criteria:** นำเข้าข้อมูลหลาย symbols ซ้ำได้ ผลตรงกัน และตรวจจับ missing/duplicate candles แยกตาม symbol ได้

**สถานะ:** Phase 1 implementation complete; การรับรอง broker/terminal แต่ละ environment ให้ทำตาม `13_MT5_TERMINAL_VALIDATION.md` ก่อนใช้งาน

## Phase 2 — Technical Strategy

- [x] EMA 9/21/50
- [x] RSI, MACD และ ATR
- [x] Support/Resistance
- [x] Candlestick Pattern
- [x] Signal schema: symbol + timeframe + BUY/SELL/HOLD, confidence, reason codes และ expiry
- [x] Strategy configuration แบบ global และ per-symbol override
- [x] Unit tests สำหรับ schema, validation, determinism และ anti-look-ahead

**Exit criteria:** ไม่มี look-ahead bias และ signal ของแต่ละ symbol ทำซ้ำได้จากข้อมูล/config เดิม

**สถานะ:** Phase 2 implementation complete และ merge เข้า `main` แล้ว

## Phase 3 — Backtesting

- [x] Backtest engine
- [x] Simulation clock และ deterministic multi-symbol candle ordering
- [x] Next-bar market fill พร้อม spread, commission และ slippage foundation
- [x] Immutable position lifecycle และ multi-symbol portfolio accounting
- [x] Intrabar SL/TP และ conservative ambiguity policy
- [x] Event orchestration สำหรับ pending signal → fill → exit → portfolio mark
- [x] Broker volume rounding, rejected/partial fills และ liquidity cap
- [x] Margin used/free margin จาก broker-provided margin-per-lot แยก symbol
- [x] Weekday swap financing และ configurable triple-swap day
- [x] Core metrics และ deterministic trade journal
- [x] Train/validation/out-of-sample split พร้อม purge/embargo
- [x] Machine-readable baseline report และ reproducibility artifacts
- [x] Complete experiment runner และ event replay journal
- [x] Deterministic HTML report และ atomic artifact persistence พร้อม checksum verification
- [x] Multi-symbol golden regression ครบ Training/Validation/Test พร้อม locked report hash

**Exit criteria:** รายงานผลครบ ตรวจย้อนหลังได้ และมี out-of-sample result

**สถานะ:** Phase 3 engineering implementation complete ใน PR #9 โดยผล golden OOS ยืนยัน
pipeline/determinism เท่านั้น การยืนยัน edge ของกลยุทธ์ยังต้องใช้ approved historical dataset,
walk-forward และ cost stress ใน Phase 4 ก่อนพิจารณา Paper Trade

## Phase 4 — AI Research

- [x] Point-in-time feature pipeline พร้อม schema hash และ prefix-invariance tests
- [x] Immutable dataset versioning พร้อม explicit label windows และ checksum
- [x] Deterministic logistic baseline พร้อม calibration metrics
- [x] Purged/embargoed walk-forward validation เทียบ no-skill prior
- [x] Research-only model registry และ advisory inference interface

**Exit criteria:** AI มีผลทดสอบเทียบ baseline และไม่เชื่อมกับ execution โดยตรง

**สถานะ:** Phase 4 engineering implementation complete และ merge เข้า `main` แล้ว โดย golden
walk-forward คงผลเป็น `RESEARCH_ONLY` เพราะ Brier score ยังไม่ชนะ no-skill prior การปิด
empirical gate ต้องรัน approved historical XAUUSD/Forex dataset และ final untouched holdout

## Phase 5 — Risk & Decision

- [x] Position sizing แบบ Decimal รวม transaction costs และ broker volume rules
- [x] SL/TP และ versioned bounded exit policy พร้อม effective reward/risk
- [x] Daily loss และ drawdown guard
- [x] Consecutive-loss cooldown
- [x] Deterministic Decision Engine
- [x] Durable scoped Kill Switch และ fail-closed submission gate

**Exit criteria:** ทุก order ผ่าน Risk Engine และทดสอบ rejection cases ครบ

**สถานะ:** Phase 5 engineering implementation complete และ merge เข้า `main` ผ่าน PR #11
พร้อม PostgreSQL CI #41 แล้ว โดยรองรับ reconciled open/pending monetary risk,
multi-currency exposure ที่แปลงหน่วยอย่างชัดเจน, cost-aware sizing, PostgreSQL-backed
Kill Switch และ authoritative approval evidence ก่อนเรียก BrokerPort ระบบปฏิเสธ LIVE
และ BACKTEST ที่ submission boundary ใน Phase 5

## Phase 6 — Paper Trading

- [x] Paper execution adapter
- [x] Order lifecycle
- [x] Database logging
- [x] FastAPI control plane
- [x] Telegram alerts
- [x] Monitoring dashboard

**สถานะ:** Phase 6 engineering implementation พร้อมเปิด PR โดย control plane และ worker
รองรับเฉพาะ PAPER, ทุก submission ผ่าน authoritative Risk Engine, durable fencing,
immutable instrument specification, order/fill/accounting audit และ authenticated monitoring.
LIVE ยังถูก hard-reject ทุก boundary

- [x] PostgreSQL CI และ host-level restart/idempotency E2E ผ่านบน PR #12
- [ ] Owner-approved empirical PAPER soak ผ่านตาม `16_PAPER_SOAK_VALIDATION.md`
  พร้อม cadence-complete observations, reconciliation, incident review, immutable report
  checksum และ owner/lead sign-off

**Exit criteria:** Paper Trade ต่อเนื่องโดยไม่มี critical incident และผล audit ครบ

Phase 6 จะถือว่า exit complete เมื่อ engineering checklist/CI และ empirical soak gate
ผ่านทั้งคู่เท่านั้น Unit/integration tests และ soak harness ไม่ใช่หลักฐานแทนการรันจริง
และห้ามเริ่ม Phase 7 หรือเปิด LIVE จากผล engineering เพียงอย่างเดียว

## Phase 7 — Controlled Live Pilot

- [ ] Security review
- [ ] Live execution adapter
- [ ] Small-capital configuration
- [ ] Manual approval
- [ ] Incident and rollback plan

**Exit criteria:** เปิด Live Trade ได้เฉพาะเมื่อเจ้าของโครงการอนุมัติ และสามารถหยุดระบบทันที

## เอกสารที่จะจัดทำต่อ

Phase 0–6 engineering implementation เสร็จและ merge แล้ว โดย Windows PAPER soak runner
merge ผ่าน PR #13 ส่วน empirical PAPER soak ยังรอการรันจริง งานวิจัย Phase 4 ยังต้องรัน approved
historical market dataset และ cost stress และห้ามเริ่ม Paper/Live จาก golden fixture


## Documentation Progress

- [x] AI Agents architecture and safety policy
- [x] Multi-Asset Trading Logic
- [x] PostgreSQL Database Design
- [x] FastAPI Specification and Control Safeguards
- [x] Multi-Asset Risk Management
- [x] Reproducible Backtesting Framework
- [x] Coding Standards and CI Quality Gates
- [x] Project Decision Log
- [x] Research Evidence Base และ pre-registered hypotheses
- [x] Leakage-safe AI Research Framework และ advisory-only model boundary
