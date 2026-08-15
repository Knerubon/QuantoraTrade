# 12 — Project Decisions

## 1. Purpose

เอกสารนี้เป็น Decision Log ของ QuantoraTrade ใช้บันทึกการตัดสินใจสำคัญ เหตุผล ผลกระทบ และสถานะ เพื่อไม่ให้ทีมเปลี่ยนแนวทางโดยไม่มีหลักฐานหรือย้อนกลับไปถกเรื่องเดิมโดยไม่เห็นบริบท

วันที่เริ่มต้น Decision Log: **2026-08-15**

## 2. Decision Status

- `PROPOSED`: เสนอไว้ ยังไม่อนุมัติ
- `ACCEPTED`: อนุมัติและต้องปฏิบัติตาม
- `SUPERSEDED`: ถูกแทนที่ด้วย Decision ใหม่
- `DEPRECATED`: ยังพบในระบบแต่ไม่ควรใช้กับงานใหม่
- `REJECTED`: พิจารณาแล้วไม่เลือก
- `ON_HOLD`: รอข้อมูลหรือหลักฐานเพิ่มเติม

## 3. Decision Template

```markdown
## ADR-XXX — Title

- Status:
- Date:
- Owners:
- Supersedes:
- Related:

### Context
เหตุใดต้องตัดสินใจ

### Decision
สิ่งที่เลือก

### Rationale
เหตุผล

### Consequences
ผลดี ผลเสีย และงานที่ตามมา

### Revisit when
เงื่อนไขที่ต้องนำกลับมาทบทวน
```

Decision ที่ ACCEPTED ห้ามแก้สาระสำคัญย้อนหลัง ให้สร้าง ADR ใหม่และเปลี่ยนของเดิมเป็น SUPERSEDED

## 4. Decision Index

| ID | Decision | Status |
|---|---|---|
| ADR-001 | Multi-Asset from the domain model | ACCEPTED |
| ADR-002 | Metals and Forex through MT5 for MVP | ACCEPTED |
| ADR-003 | Backtest → Paper → Controlled Live | ACCEPTED |
| ADR-004 | Modular Monolith | ACCEPTED |
| ADR-005 | Ports and Adapters | ACCEPTED |
| ADR-006 | Python 3.12 and FastAPI | ACCEPTED |
| ADR-007 | PostgreSQL as System of Record | ACCEPTED |
| ADR-008 | Deterministic Risk Engine | ACCEPTED |
| ADR-009 | AI is advisory only | ACCEPTED |
| ADR-010 | Live Trading disabled by default | ACCEPTED |
| ADR-011 | Shared logic across operating modes | ACCEPTED |
| ADR-012 | Decimal and UTC | ACCEPTED |
| ADR-013 | Event-driven bar backtesting | ACCEPTED |
| ADR-014 | Version everything affecting decisions | ACCEPTED |
| ADR-015 | Append-only trading audit | ACCEPTED |
| ADR-016 | SSE before WebSocket | ACCEPTED |
| ADR-017 | Configuration files before admin editor | ACCEPTED |
| ADR-018 | No message broker in MVP | ACCEPTED |
| ADR-019 | No microservices in MVP | ACCEPTED |
| ADR-020 | Protected main and CI gates | ACCEPTED |
| ADR-021 | Initial strategy parameters | ON_HOLD |
| ADR-022 | Approved risk limits | ON_HOLD |
| ADR-023 | Official historical data provider | ON_HOLD |
| ADR-024 | Initial production hosting | ON_HOLD |
| ADR-025 | AI model/provider selection | ON_HOLD |

## ADR-001 — Multi-Asset from the Domain Model

- Status: ACCEPTED
- Date: 2026-08-15
- Owners: Project Owner
- Related: Vision, Architecture, Trading Logic

### Context

ระบบเริ่มจาก XAUUSD แต่ต้องรองรับคู่เงินและสินทรัพย์อื่นโดยไม่เขียนระบบใหม่

### Decision

ทุก entity และ workflow ที่เกี่ยวกับตลาดต้องรองรับ `symbol`, `asset_class`, `timeframe` และ Broker Symbol Specification ตั้งแต่แรก

### Rationale

ป้องกันการ hard-code pip, tick, lot, session และ logic เฉพาะ XAUUSD

### Consequences

- Config และ tests ต้องมีหลาย symbol
- Risk ต้องรวม currency/portfolio exposure
- งานเริ่มต้นเพิ่มขึ้นเล็กน้อยแต่ลดการ rewrite

### Revisit when

มี asset class ใหม่ที่ semantics ต่างจาก Spot Forex/Metals อย่างมาก เช่น Options

## ADR-002 — Metals and Forex through MT5 for MVP

- Status: ACCEPTED
- Date: 2026-08-15
- Owners: Project Owner
- Related: Product Requirements

### Decision

MVP รองรับ XAUUSD และ Forex majors ผ่าน MetaTrader 5 โดยเริ่มตัวอย่างที่ EURUSD, GBPUSD และ USDJPY

### Consequences

- MT5 เป็น adapter แรก ไม่ใช่ dependency ของ core
- ยังไม่รองรับ Exchange, Crypto หรือหลาย Broker ใน runtime เดียว
- รายการ symbol ที่เปิดจริงยังต้องกำหนดจากข้อมูลและ Backtest

## ADR-003 — Backtest → Paper → Controlled Live

- Status: ACCEPTED
- Date: 2026-08-15
- Owners: Project Owner
- Related: Roadmap, Backtesting, Risk

### Decision

Strategy ต้องเลื่อนสถานะตามลำดับ:

1. research
2. backtest approved
3. paper approved
4. live approved

ห้ามข้ามขั้น

### Consequences

Live approval ต้องมี evidence, version และ Owner confirmation

## ADR-004 — Modular Monolith

- Status: ACCEPTED
- Date: 2026-08-15
- Owners: Engineering
- Related: System Architecture

### Decision

เริ่มด้วย Modular Monolith และแยก runtime เป็น Worker กับ API process โดยใช้ codebase เดียวกัน

### Rationale

ลด deployment/operations complexity ในระยะที่ product และ strategy ยังเปลี่ยนเร็ว

### Revisit when

- deployment scale ต่างกันชัดเจน
- fault isolation เป็นปัญหาจริง
- team ownership แยก
- measured bottleneck พิสูจน์ว่าต้องแยก service

## ADR-005 — Ports and Adapters

- Status: ACCEPTED
- Date: 2026-08-15
- Owners: Engineering

### Decision

Domain/Application อ้าง interfaces เช่น `BrokerPort`, `MarketDataPort`, `TradeRepositoryPort` และ `ClockPort` ส่วน MT5/PostgreSQL/Telegram/FastAPI อยู่ใน adapter boundaries

### Consequences

- Backtest/Paper/Fake adapters สลับได้
- มี mapping code เพิ่ม
- ห้าม SDK type รั่วเข้า domain

## ADR-006 — Python 3.12 and FastAPI

- Status: ACCEPTED
- Date: 2026-08-15
- Owners: Engineering

### Decision

ใช้ Python 3.12 เป็นภาษาหลัก และ FastAPI + Pydantic สำหรับ API/control plane

### Consequences

ใช้ ecosystem เดียวกับ data/AI และสร้าง typed OpenAPI contract ได้

### Revisit when

มีข้อจำกัด performance/latency ที่วัดแล้วและแก้ด้วย profiling/optimization ไม่ได้

## ADR-007 — PostgreSQL as System of Record

- Status: ACCEPTED
- Date: 2026-08-15
- Owners: Engineering
- Related: Database Design

### Decision

PostgreSQL เก็บ transactional state, metadata และ audit ส่วนไฟล์ใหญ่เก็บ Artifact Store พร้อม checksum/URI

### Consequences

- ใช้ SQLAlchemy + Alembic
- แยก DB ต่อ environment
- TimescaleDB ยังไม่เพิ่มจนมีหลักฐานด้าน volume/query

## ADR-008 — Deterministic Risk Engine

- Status: ACCEPTED
- Date: 2026-08-15
- Owners: Project Owner, Risk

### Decision

Position sizing, limits, margin, exposure, Stop validation, Drawdown และ Kill Switch ต้องเป็น deterministic code/config

### Consequences

AI ไม่มีสิทธิ์ approve, override หรือเพิ่ม risk ค่าคำนวณผิดต้อง reject

## ADR-009 — AI Is Advisory Only

- Status: ACCEPTED
- Date: 2026-08-15
- Owners: Project Owner
- Related: AI Agents

### Decision

AI เสนอ regime, evidence, confidence และ conflict ได้ แต่เรียก BrokerPort สร้าง lot หรือข้าม Risk Gate ไม่ได้

### Consequences

AI provider ล่มแล้วระบบยัง fail safely ได้ และผล AI ต้องมี structured schema/version/audit

## ADR-010 — Live Trading Disabled by Default

- Status: ACCEPTED
- Date: 2026-08-15
- Owners: Project Owner, Risk

### Decision

Default mode ต้องไม่ใช่ Live การเปิด Live ต้องผ่าน Preflight, dedicated authorization, approved versions และ Owner confirmation

### Consequences

`/system/start` ไม่เปิด Live โดยตรง

## ADR-011 — Shared Logic across Operating Modes

- Status: ACCEPTED
- Date: 2026-08-15
- Owners: Engineering

### Decision

Backtest, Paper และ Live ใช้ Feature, Strategy, Decision และ Risk implementation ชุดเดียวกัน แตกต่างเฉพาะ Clock, Data และ Execution adapters

### Rationale

ลดความคลาดเคลื่อนระหว่างผลทดสอบกับ production

## ADR-012 — Decimal and UTC

- Status: ACCEPTED
- Date: 2026-08-15
- Owners: Engineering

### Decision

ราคา เงิน Volume และค่าที่ต้อง exact ใช้ Decimal/NUMERIC เวลาใช้ timezone-aware UTC

### Consequences

- JSON decimal เป็น string
- ห้าม float ใน critical calculations
- ห้าม naive datetime

## ADR-013 — Event-Driven Bar Backtesting

- Status: ACCEPTED
- Date: 2026-08-15
- Owners: Research, Engineering

### Decision

MVP ใช้ event-driven bar simulation รองรับ multi-symbol queue และ closed-bar logic

### Consequences

ความเร็วอาจต่ำกว่า vector-only backtest แต่รักษา order/risk/portfolio state และความสอดคล้องกับ Paper/Live ได้ดีกว่า

### Revisit when

ต้องใช้ tick-level execution หรือ performance profiling ชี้ bottleneck ที่ชัดเจน

## ADR-014 — Version Everything Affecting Decisions

- Status: ACCEPTED
- Date: 2026-08-15
- Owners: Engineering, Research

### Decision

ทุก official run/order ต้อง trace ถึง:

- code commit
- dataset
- configuration
- strategy
- feature set
- risk policy
- model
- prompt
- execution assumptions

### Consequences

เพิ่ม metadata แต่ทำ replay/audit/comparison ได้

## ADR-015 — Append-Only Trading Audit

- Status: ACCEPTED
- Date: 2026-08-15
- Owners: Engineering, Risk

### Decision

Signals, Decisions, Risk Assessments และ Trade Events เป็น immutable/append-only ห้ามแก้ประวัติโดย application role

### Consequences

correction ต้องสร้าง event/revision ใหม่

## ADR-016 — SSE before WebSocket

- Status: ACCEPTED
- Date: 2026-08-15
- Owners: Engineering
- Related: API Specification

### Decision

MVP ใช้ Server-Sent Events สำหรับ dashboard updates และ REST สำหรับ reconcile

### Rationale

การไหลหลักเป็น server-to-client และ SSE เริ่มง่ายกว่า

### Revisit when

ต้องการ bidirectional low-latency interaction ที่ SSE + REST ไม่เพียงพอ

## ADR-017 — Configuration Files before Admin Editor

- Status: ACCEPTED
- Date: 2026-08-15
- Owners: Engineering, Risk

### Decision

Strategy/Risk/Symbol configuration เริ่มด้วย versioned YAML + schema validation ไม่มี generic Admin JSON editor ใน MVP

### Consequences

การเปลี่ยนค่า review และ audit ผ่าน Git ได้ชัดเจน ลดความเสี่ยงจาก runtime editing

## ADR-018 — No Message Broker in MVP

- Status: ACCEPTED
- Date: 2026-08-15
- Owners: Engineering

### Decision

เริ่ม synchronous/in-process orchestration และ database-backed command state โดยยังไม่เพิ่ม Kafka/RabbitMQ

### Revisit when

มี measured need ด้าน throughput, delivery isolation, fan-out หรือ independent scaling

## ADR-019 — No Microservices in MVP

- Status: ACCEPTED
- Date: 2026-08-15
- Owners: Engineering

### Decision

ไม่แยก microservices ก่อนมี team/scale/fault-isolation evidence

### Consequences

ต้องรักษา module boundaries และห้ามสร้าง distributed complexity ก่อนเวลา

## ADR-020 — Protected Main and CI Gates

- Status: ACCEPTED
- Date: 2026-08-15
- Owners: Project Owner, Engineering
- Related: Coding Standards

### Decision

เมื่อเริ่ม implementation ให้ใช้ feature branches + Pull Requests และกำหนด required CI checks สำหรับ `main`

### Required gates

- format/lint
- type check
- unit/property tests
- contract/integration tests
- migration test
- golden backtest
- secret/security scans

### Consequences

หลัง initial documentation bootstrap ห้าม push implementation ตรงเข้า `main`

## ADR-021 — Initial Strategy Parameters

- Status: ON_HOLD
- Date: 2026-08-15
- Owners: Project Owner, Research

### Known baseline

- EMA 9/21/50
- RSI 14
- MACD 12/26/9
- ATR 14
- Entry M15
- Context H1
- Trend Pullback และ Breakout variants

### Missing evidence

- thresholds
- scoring weights
- SL/TP method
- sessions
- per-symbol overrides

### Resolution criteria

กำหนด search space, dataset และ baseline Backtest โดยไม่ใช้ Live data leakage

## ADR-022 — Approved Risk Limits

- Status: ON_HOLD
- Date: 2026-08-15
- Owners: Project Owner, Risk

### Pending

- capital/equity
- risk per trade
- daily loss
- drawdown levels
- portfolio/currency exposure
- position/order limits
- cooldown
- spread/slippage limits

### Resolution criteria

Backtest stress/Monte Carlo + Paper evidence + Owner Approval

## ADR-023 — Official Historical Data Provider

- Status: ON_HOLD
- Date: 2026-08-15
- Owners: Research, Engineering

### Candidates

ยังไม่เลือก

### Evaluation criteria

- bid/ask หรือ spread history
- timestamp/timezone accuracy
- revision policy
- missing data
- symbol coverage
- license/cost
- point-in-time news availability
- reproducible download/versioning

## ADR-024 — Initial Production Hosting

- Status: ON_HOLD
- Date: 2026-08-15
- Owners: Engineering, Operations

### Constraint

MT5 integration อาจกำหนด Windows/runtime topology ขณะที่ API/PostgreSQL อาจรันแยก environment

### Resolution criteria

ทำ local Paper prototype และวัด reliability ก่อนเลือก cloud/host topology

## ADR-025 — AI Model/Provider Selection

- Status: ON_HOLD
- Date: 2026-08-15
- Owners: Research, Engineering

### Decision deferred

ยังไม่เลือก provider/model จนกว่า deterministic baseline และ Agent contracts พร้อม

### Evaluation criteria

- structured output reliability
- reproducibility/version pinning
- latency
- cost
- privacy/security
- availability
- evaluation performance
- fallback behavior

## 5. Open Decision Register

| Topic | Needed before | Evidence required |
|---|---|---|
| MVP symbol list | Data Layer implementation | MT5 availability/specs/data quality |
| Historical provider | Official Backtest | quality/license/cost comparison |
| Initial capital | Official Risk Backtest | Owner decision |
| Risk limits | Paper approval | stress + Monte Carlo + Paper |
| Strategy thresholds | Baseline Backtest | validation/out-of-sample |
| SL/TP variant | Paper candidate | robustness and cost sensitivity |
| News provider | News Agent | point-in-time coverage/security/cost |
| AI provider/model | AI shadow mode | offline evaluation |
| Hosting topology | Paper deployment | MT5/runtime reliability |
| Live broker/account | Live preflight | legal, operational and execution review |
| RPO/RTO | Live pilot | incident/business impact analysis |
| Approval roles | Protected workflow | team/ownership model |

## 6. Decision Process

1. เปิด issue หรือ draft ADR
2. ระบุ Context และ Options
3. เก็บหลักฐาน/ทดลอง
4. ประเมิน Risk, Security, Cost และ Reversibility
5. Owner/ผู้รับผิดชอบอนุมัติ
6. เปลี่ยนสถานะเป็น ACCEPTED
7. อัปเดตเอกสาร Config Tests และ Roadmap
8. เชื่อม implementation PR กับ ADR
9. ทบทวนเมื่อ trigger เกิด

## 7. Decision Quality Checklist

ก่อน ACCEPTED ต้องตอบได้:

- ปัญหาที่แก้คืออะไร
- มีทางเลือกใดบ้าง
- เหตุผลที่เลือก
- ข้อเสียและ failure modes
- reversible หรือ irreversible
- กระทบ Trading/Risk/Live หรือไม่
- ต้อง migration/rollback อย่างไร
- ใช้หลักฐานอะไร
- ใครอนุมัติ
- เมื่อใดต้องทบทวน

## 8. Superseding Decisions

เมื่อเปลี่ยน Decision:

- สร้าง ADR หมายเลขใหม่
- ระบุ `Supersedes: ADR-XXX`
- เปลี่ยนของเดิมเป็น `SUPERSEDED`
- ห้ามลบเหตุผลเดิม
- ระบุ migration/transition plan
- official runs/orders เก่ายังคงอ้าง version เดิม

## 9. Foundation Completion

เอกสาร Foundation ชุดแรกประกอบด้วย:

1. Product Vision
2. Product Requirements
3. Roadmap
4. System Architecture
5. AI Agents
6. Trading Logic
7. Database Design
8. API Specification
9. Risk Management
10. Backtesting Framework
11. Coding Standards
12. Project Decisions

ขั้นถัดไปคือ Implementation Phase 0:

- สร้าง Python project scaffold
- สร้าง configuration schemas
- สร้าง domain contracts
- ตั้ง Ruff, mypy, pytest และ pre-commit
- สร้าง CI
- สร้าง PostgreSQL/Alembic foundation
- เริ่ม Market Data adapter และ tests
