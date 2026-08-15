# 11 — Coding Standards

## 1. Purpose

เอกสารนี้กำหนดมาตรฐานการพัฒนา QuantoraTrade เพื่อให้ Codebase อ่านง่าย ทดสอบได้ ทำซ้ำได้ และปลอดภัยสำหรับระบบ Multi-Asset Trading

คำว่า **MUST**, **MUST NOT**, **SHOULD** และ **MAY** ใช้ในความหมายเชิงข้อกำหนด:

- MUST: บังคับ
- MUST NOT: ห้าม
- SHOULD: ควรทำ เว้นแต่มีเหตุผลบันทึกไว้
- MAY: เลือกใช้ได้

## 2. Core Engineering Principles

- Correctness before speed
- Risk and safety before convenience
- Explicit contracts over implicit behavior
- Deterministic core over hidden side effects
- Fail closed for trading-critical uncertainty
- Small modules with clear ownership
- Configuration over symbol-specific hard-coding
- Tests are part of the feature
- Auditability from market event to broker fill
- Production code and research notebooks have separate boundaries

## 3. Language and Runtime

- Python 3.12 เป็น baseline
- Source encoding: UTF-8
- Package layout: `src/`
- Build metadata: `pyproject.toml`
- Dependency versions ต้อง lock สำหรับ CI/official runs
- ห้ามใช้ system Python สำหรับ deployment
- Local, CI และ container ต้องใช้ runtime family เดียวกัน
- Upgrade dependency ต้องผ่าน tests และบันทึกผลกระทบ

## 4. Repository Structure

```text
QuantoraTrade/
├── config/
│   ├── symbols.example.yaml
│   ├── strategies.example.yaml
│   └── risk.example.yaml
├── docs/
├── migrations/
├── notebooks/
├── scripts/
├── src/quantora_trade/
│   ├── api/
│   ├── application/
│   ├── backtest/
│   ├── config/
│   ├── domain/
│   ├── execution/
│   ├── features/
│   ├── infrastructure/
│   ├── market_data/
│   ├── monitoring/
│   ├── portfolio/
│   ├── risk/
│   └── strategies/
├── tests/
│   ├── unit/
│   ├── integration/
│   ├── contract/
│   ├── regression/
│   └── fixtures/
├── .env.example
├── .gitignore
├── pyproject.toml
└── README.md
```

### Boundaries

- `domain` MUST NOT import FastAPI, SQLAlchemy, MetaTrader5, Telegram หรือ provider SDK
- `application` orchestrates use cases และอ้าง domain/ports
- `infrastructure` implements external adapters
- `api` validates transport และเรียก application use cases
- `strategies` MUST NOT call broker/database โดยตรง
- `risk` MUST NOT depend on AI provider
- `backtest` ใช้ shared domain/strategy/risk contracts

## 5. Naming

### Python

- modules/functions/variables: `snake_case`
- classes/protocols/exceptions: `PascalCase`
- constants: `UPPER_SNAKE_CASE`
- private implementation: prefix `_`
- booleans: ใช้ชื่อ `is_`, `has_`, `can_`, `should_`
- async functions: ชื่อบอก action ไม่ต้องเติม `async_`

### Domain naming

ใช้ชื่อ domain ที่ชัดเจน:

- `Signal`
- `Decision`
- `RiskAssessment`
- `ApprovedOrderIntent`
- `BrokerOrder`
- `Fill`
- `Position`

ห้ามใช้ชื่อกว้าง เช่น `data`, `obj`, `manager`, `helper` โดยไม่บอกหน้าที่

## 6. Formatting and Static Checks

Standard toolchain:

- Ruff สำหรับ format และ lint
- mypy สำหรับ static type checking
- pytest สำหรับ tests
- pre-commit สำหรับ local checks

กติกา:

- line length ใช้ค่าที่กำหนดใน `pyproject.toml`
- imports ให้ tool จัดลำดับ
- ห้าม disable rule ทั้งไฟล์โดยไม่มีเหตุผล
- `# noqa` ต้องระบุ code
- generated files ต้องแยกและไม่ตรวจแบบเดียวกับ handwritten code
- CI เป็นผู้ตัดสินสุดท้ายเรื่อง formatting

## 7. Type Safety

- Public functions MUST มี type annotations
- Domain/Application code MUST ผ่าน mypy strict ตาม scope ที่กำหนด
- ห้ามใช้ `Any` ใน trading-critical path เว้นแต่ adapter boundary
- ข้อมูลภายนอกต้อง parse/validate ก่อนเข้า domain
- ใช้ `Protocol` สำหรับ ports
- ใช้ `Enum` หรือ `Literal` สำหรับ closed value sets
- ใช้ `NewType`/value objects เมื่อ ID หรือหน่วยอาจสับสน
- Optional ต้อง handle ชัดเจน ห้ามใช้ implicit None
- cast ใช้เมื่อมี invariant ที่อธิบายและ test แล้ว

ตัวอย่าง:

```python
from typing import Protocol

class BrokerPort(Protocol):
    def submit(self, order: "ApprovedOrderIntent") -> "BrokerOrder":
        ...
```

BrokerPort รับเฉพาะ `ApprovedOrderIntent` เพื่อปิดทางส่ง Decision ที่ยังไม่ผ่าน Risk

## 8. Domain Models

- Domain entity/value object SHOULD ใช้ immutable dataclass เมื่อเหมาะสม
- Pydantic ใช้ที่ API/config boundary
- SQLAlchemy models ไม่ควรถูกส่งทั่ว domain
- Mapping ระหว่าง API ↔ Domain ↔ Persistence ต้องชัดเจน
- Domain invariant validate ตอนสร้าง object
- Identifier ต้องสร้างครั้งเดียวและไม่เปลี่ยน
- Signal/Decision/RiskAssessment เป็น immutable records

## 9. Decimal and Units

### Money, price and volume

- MUST ใช้ `Decimal`
- MUST สร้าง Decimal จาก string
- MUST NOT สร้างจาก float เช่น `Decimal(0.1)`
- การ quantize ต้องอิง tick size/volume step
- rounding สำหรับ risk volume MUST ปัดลง
- database ใช้ NUMERIC
- JSON ส่ง decimal เป็น string

### Units

ชื่อ variable ต้องบอกหน่วยเมื่อไม่ชัด:

- `timeout_seconds`
- `latency_ms`
- `spread_points`
- `risk_amount_usd`
- `duration_bars`

ห้ามใช้คำว่า `pips` เป็นหน่วยกลางโดยไม่ผูกกับ Instrument specification

## 10. Time

- ใช้ timezone-aware `datetime`
- เก็บและส่งข้อมูลภายในเป็น UTC
- naive datetime MUST NOT เข้า domain
- ใช้ injected `ClockPort` แทน `datetime.now()` ใน logic
- Backtest ใช้ Simulation Clock
- deadline/timeout ภายใน process ใช้ monotonic clock
- timeframe alignment ต้องใช้ shared utility ที่ test แล้ว
- ห้ามใช้เวลาท้องถิ่นของเครื่องตัดสิน session โดยตรง

## 11. Configuration

- Config ใช้ YAML + Pydantic Settings/schema
- Environment variables ใช้สำหรับ secret และ environment override
- Config ต้อง validate ก่อน application start
- ทุก required Live limit ห้ามเป็น null
- Symbol-specific values อยู่ใน symbol profile
- ห้ามใช้ `os.getenv` กระจายทั่ว codebase; อ่านผ่าน Settings object
- ห้ามเปลี่ยน config object ระหว่าง run
- บันทึก redacted config snapshot/hash
- ค่า default ที่กระทบ risk ต้อง explicit ไม่ซ่อนใน function

Priority ที่กำหนด:

1. schema defaults ที่ไม่กระทบ risk
2. versioned config file
3. environment-specific non-secret override
4. environment/secret store
5. explicit runtime command เฉพาะค่าที่อนุญาต

## 12. Error Handling

### Exceptions

สร้าง hierarchy เช่น:

- `QuantoraError`
- `ConfigurationError`
- `DataQualityError`
- `RiskRejected`
- `BrokerUnavailableError`
- `ReconciliationError`
- `InvariantViolation`

กติกา:

- ห้าม `except Exception: pass`
- Catch exception ที่ boundary ซึ่งจัดการได้
- Preserve cause ด้วย `raise ... from exc`
- Domain rejection ที่คาดหมายใช้ typed result/reason code ไม่ใช้ exception เพื่อ flow ปกติ
- Error message ห้ามมี secret
- Retry เฉพาะ error ที่จำแนกว่า retryable
- Risk calculation error ต้องกลายเป็น rejection ไม่ใช่ default approval

## 13. Result and Reason Codes

Trading decisions ใช้ stable reason codes:

```python
class RiskRejectionCode(str, Enum):
    STALE_DATA = "STALE_DATA"
    SPREAD_TOO_WIDE = "SPREAD_TOO_WIDE"
    DAILY_LOSS_LIMIT = "DAILY_LOSS_LIMIT"
    KILL_SWITCH_ACTIVE = "KILL_SWITCH_ACTIVE"
```

- code ใช้ใน database/API/log/report เหมือนกัน
- การ rename/remove ถือเป็น contract change
- human message แยกจาก stable code
- ห้าม parse log text เพื่อหา business result

## 14. Functions and Classes

- function ทำหน้าที่เดียว
- side effects ต้องชัดจาก boundary
- pure functions ใช้กับ indicators, scoring และ risk formulas
- dependency injection ผ่าน constructor/function arguments
- หลีกเลี่ยง global mutable state
- หลีกเลี่ยง inheritance ลึก ใช้ composition
- class ชื่อ `Service` ต้องมี use case ชัดเจน
- method ที่ยาวหรือมีหลาย branch ควรแยกและเพิ่ม tests
- ห้าม utility dumping ground เช่น `utils.py` ขนาดใหญ่

## 15. Async and Concurrency

- ใช้ async สำหรับ network I/O เมื่อ adapter รองรับ
- CPU-heavy feature/model work ห้าม block API event loop
- Trading event order ต้อง deterministic
- shared mutable state ต้องมี owner เดียวหรือ synchronization ชัดเจน
- database transaction ห้ามครอบ network call
- ทุก command ที่ retry ได้ต้อง idempotent
- task/background job ต้องมี correlation ID
- cancellation ต้อง cleanup โดยไม่สร้าง order ครึ่งทาง
- ห้าม retry Broker submit แบบ blind หลัง timeout; reconcile ก่อน

## 16. Database Code

- ใช้ SQLAlchemy repository adapters
- migration ใช้ Alembic
- parameterized query เท่านั้น
- session/transaction scope ต่อ use case
- domain ไม่รู้จัก ORM session
- N+1 query ต้องตรวจใน endpoints สำคัญ
- append-only table ห้าม update/delete จาก app role
- optimistic locking สำหรับ mutable order/position read model
- `SELECT FOR UPDATE` ใช้เฉพาะ transaction boundary ที่จำเป็น
- schema migration ต้อง backward-compatible ตาม rollout plan

## 17. API Code

- router ทำ transport validation และ authorization
- business logic อยู่ใน application/domain
- Pydantic request/response แยกจาก ORM
- common error envelope ใช้ทุก endpoint
- mutation สำคัญตรวจ Idempotency-Key
- decimal serialize เป็น string
- timestamps มี timezone
- list endpoints ใช้ cursor pagination
- ห้าม return exception/stack trace
- OpenAPI examples ต้องผ่าน schema tests
- Live endpoint ต้องตรวจ dedicated scope และ approval workflow

## 18. Broker and External Adapters

- SDK-specific types ต้องไม่รั่วเข้า domain
- map provider errors เป็น internal error taxonomy
- timeout/retry/circuit breaker กำหนดต่อ operation
- ทุก request/response redact secret ใน log
- Broker submit ต้องมี idempotency/external reference
- adapter ต้องรองรับ reconciliation
- unknown provider response ให้ mark uncertain และ block new risk
- test adapter ด้วย recorded/synthetic responses ที่ไม่มี credential

## 19. AI Code

- Agent output ต้องผ่าน Pydantic/JSON Schema
- input ต้องมี `as_of`
- prompt/model/version ต้อง trace ได้
- external text เป็น untrusted data
- Agent ไม่มี access ถึง BrokerPort หรือ secret
- timeout, token และ cost budget ต้องกำหนด
- invalid/stale output กลายเป็น UNKNOWN/HOLD
- cached output key ต้องรวม model/prompt/input hash
- AI confidence ห้ามเพิ่ม risk amount ใน MVP
- production prompt change ต้องผ่าน version/review/test

## 20. Logging

ใช้ structured JSON logs พร้อม:

- timestamp
- level
- service/component
- environment และ mode
- request/correlation/analysis/decision/order IDs
- symbol/timeframe/strategy เมื่อเกี่ยวข้อง
- event code
- safe context
- exception type

ห้าม log:

- password/token
- full Authorization header
- secret config
- sensitive account data ที่ไม่จำเป็น
- raw provider payload โดยไม่ redact
- entire DataFrame/candle history ใน production log

ใช้ log level:

- DEBUG: development detail
- INFO: state transition ปกติ
- WARNING: degraded/recoverable
- ERROR: operation failed
- CRITICAL: risk/security/live incident

## 21. Observability

- metrics label ต้องมี cardinality จำกัด
- ห้ามใช้ order ID เป็น metric label
- traces/logs เชื่อมด้วย correlation ID
- health/live ไม่ตรวจ dependency
- health/ready ตรวจ dependency ที่จำเป็น
- trading-critical alerts ต้องมี stable event code
- monitoring failure ห้ามทำให้ Risk Gate approve
- clock/data/broker/database freshness ต้องวัดได้

## 22. Security

- ห้าม commit `.env`, credential, token, key, account file
- มี `.env.example` เฉพาะชื่อ variable และค่าปลอม
- secret scanning ใน CI
- dependency vulnerability scanning
- least-privilege database/broker/API roles
- production debug mode ปิด
- logs/artifacts ต้อง redact
- input ทุก boundary validate
- deserialization ห้ามใช้ unsafe formats
- subprocess หลีกเลี่ยง shell; ถ้าจำเป็นใช้ argument list
- security issue ห้ามใส่รายละเอียด exploitable ใน public log

## 23. Testing Standards

### Test naming

`test_<behavior>_<condition>_<expected>()`

ตัวอย่าง:

```python
def test_position_size_when_below_minimum_rejects_order() -> None:
    ...
```

### Test structure

Arrange → Act → Assert และหนึ่ง behavior หลักต่อ test

### Requirements

- deterministic
- ไม่มี network จริงใน unit tests
- freeze/inject time
- fixed random seed
- fixtures เล็กและอ่านง่าย
- assert business result/reason code ไม่ assert implementation detail
- regression bug ต้องมี test ก่อน/พร้อม fix
- test ห้ามขึ้นกับลำดับการรัน

## 24. Test Layers

| Layer | Purpose |
|---|---|
| Unit | pure logic, indicators, risk, state transitions |
| Property-based | invariants และ ranges |
| Contract | API/adapter/schema contracts |
| Integration | PostgreSQL, repositories, orchestrators |
| Regression | known bugs และ golden datasets |
| End-to-end | Paper flow ด้วย fake/sandbox broker |
| Replay | event/candle ถึง trade result |
| Chaos/Failure | timeout, disconnect, restart, partial fill |

Live credentials ห้ามใช้ใน CI

## 25. Coverage and Critical Paths

Coverage percentage ไม่ใช่เป้าหมายเดียว แต่ critical paths MUST มี branch coverage สูง:

- Risk approval/rejection
- position sizing
- Kill Switch
- order idempotency
- order state machine
- reconciliation
- daily loss/drawdown
- Backtest timing/alignment
- configuration validation
- Live activation guards

Code coverage ลดลงใน critical module ต้อง block merge เว้นแต่มี reviewed justification

## 26. Test Data

- synthetic data เป็นค่าเริ่มต้น
- golden datasets เก็บขนาดเล็กใน Git
- dataset ใหญ่เก็บ Artifact Store พร้อม checksum
- production data ต้อง anonymize ก่อนใช้
- fixture ต้องระบุ timezone/symbol specification
- edge cases ครอบคลุม XAUUSD, EURUSD และ USDJPY precision
- ห้าม snapshot output ใหญ่โดยไม่ตรวจสาระสำคัญ

## 27. Documentation

Public module/class/function ที่ไม่ชัดเจนต้องมี docstring ระบุ:

- purpose
- inputs/outputs
- units
- invariants
- side effects
- raised exceptions เมื่อเกี่ยวข้อง

เอกสาร architecture/trading/risk/API ต้องอัปเดตพร้อม code เมื่อ contract เปลี่ยน

Comment ใช้อธิบาย **why** ไม่ใช่แปลสิ่งที่ code ทำ

## 28. Git Workflow

### Branches

- `main`: protected และ deployable
- feature: `feat/<short-name>`
- fix: `fix/<short-name>`
- docs: `docs/<short-name>`
- chore: `chore/<short-name>`

ห้าม push code implementation เข้า `main` โดยตรงเมื่อเปิด development workflow แล้ว ใช้ Pull Request

### Commits

ใช้ Conventional Commits:

- `feat:`
- `fix:`
- `docs:`
- `test:`
- `refactor:`
- `chore:`
- `ci:`

Commit ต้องเล็ก มีเจตนาเดียว และไม่รวม generated/noisy changes โดยไม่จำเป็น

## 29. Pull Requests

PR ต้องมี:

- problem และ scope
- design/decision
- tests
- risk impact
- config/schema/API changes
- migration/rollback
- screenshots/report เมื่อเกี่ยวข้อง
- linked issue/decision
- checklist secret scan

PR ที่กระทบ Risk, Execution, Live activation หรือ database migration ต้องมี review เพิ่มตาม CODEOWNERS ที่จะกำหนด

Self-review ก่อนขอ review และ resolve feedback ด้วย code/test ไม่ใช่เพียง comment

## 30. Code Review Checklist

### Correctness

- logic ตรง requirement
- no look-ahead
- Decimal/UTC/units ถูกต้อง
- edge cases และ state transitions ครบ

### Safety

- Risk Gate ข้ามไม่ได้
- idempotency/reconciliation
- fail-closed behavior
- secret/redaction
- Live disabled by default

### Maintainability

- module boundary
- types
- naming
- duplication
- tests/docs
- migration compatibility

### Performance

- query plan/N+1
- memory/dataframe copies
- blocking I/O
- metric cardinality
- deterministic concurrency

## 31. CI Quality Gates

ทุก PR ต้องรันอย่างน้อย:

1. dependency lock consistency
2. Ruff format check
3. Ruff lint
4. mypy
5. unit/property tests
6. contract tests
7. integration tests กับ PostgreSQL
8. migration upgrade test
9. coverage/critical-path thresholds
10. secret scan
11. dependency vulnerability scan
12. OpenAPI compatibility check
13. small golden backtest regression

Protected `main` ต้อง require checks และ review ตาม policy

## 32. Release and Versioning

- application ใช้ Semantic Versioning ก่อน 1.0 ด้วยความระมัดระวัง
- Strategy, Risk Policy, Feature Set, Model และ Prompt version แยกจาก app version
- release tag ต้องชี้ clean commit
- changelog ระบุ breaking changes
- database migration version ผูกกับ release
- container/artifact ต้อง immutable และมี checksum
- rollback procedure ต้องมีสำหรับ production
- official Backtest ระบุ release/commit ชัดเจน

## 33. Research Notebooks

- notebooks ใช้ exploration ไม่ใช่ production execution
- production logic ต้องย้ายเข้า `src/` พร้อม tests
- notebook ต้องระบุ dataset/config/version/seed
- ห้าม commit output ขนาดใหญ่หรือ secret
- lint/check notebook ตามเครื่องมือที่เลือก
- ผลใน notebook ไม่ถือเป็น official Backtest หากไม่มี manifest/report

## 34. Performance Optimization

- profile ก่อน optimize
- benchmark ก่อน/หลัง
- correctness tests ต้องเหมือนเดิม
- vectorized feature code ต้องรักษา point-in-time semantics
- cache key ต้องรวม version/input hash
- parallel run ต้องไม่เปลี่ยน event order ภายใน portfolio
- optimization ที่ทำให้ audit/replay หายต้องไม่รับ

## 35. Dependency Policy

- เพิ่ม dependency เมื่อคุณค่ามากกว่าภาระ
- ตรวจ license, maintenance และ security
- pin/lock versions
- ห้าม import optional dependency ตอน startup ถ้า feature ปิด
- provider SDK อยู่หลัง adapter
- dependency ใหญ่สำหรับ function เล็กต้องมีเหตุผล
- upgrade แยก PR เมื่อมีความเสี่ยง
- remove dependency ที่ไม่ใช้

## 36. Definition of Done — Feature

Feature ถือว่าเสร็จเมื่อ:

- requirement และ acceptance criteria ผ่าน
- code format/lint/type checks ผ่าน
- unit/integration/contract tests ตาม scope
- negative/failure cases ครบ
- logs/metrics/audit ที่จำเป็น
- security/secret review
- docs/config/examples อัปเดต
- migration/rollback เมื่อเกี่ยวข้อง
- no unresolved critical TODO
- CI ผ่าน
- reviewer อนุมัติ

## 37. Prohibited Patterns

- hard-code XAUUSD/Forex pip values ใน business logic
- float สำหรับ money/price/volume
- naive datetime
- global mutable trading state
- direct Broker SDK call จาก Strategy/AI/API
- generic catch แล้ว approve/continue
- retry order submit แบบ blind
- log secret
- edit approved version in place
- live-by-default config
- test ที่เรียก Live broker
- merge เมื่อ critical checks fail
- `TODO` ใน Risk/Execution critical path โดยไม่มี issue และ fail-safe behavior

## 38. Initial Tooling Configuration

ไฟล์ที่ควรสร้างใน implementation phase:

- `pyproject.toml`
- dependency lock file
- `.pre-commit-config.yaml`
- `.editorconfig`
- `.gitignore`
- `.env.example`
- `mypy.ini` เฉพาะเมื่อไม่รวมใน pyproject
- `alembic.ini`
- `.github/workflows/ci.yml`
- `CODEOWNERS`
- Pull Request template
- issue templates
- security policy

## 39. Definition of Done — Standards Adoption

Coding Standards ถือว่าถูกนำไปใช้เมื่อ:

- formatter/linter/type checker ทำงาน local และ CI
- pre-commit พร้อมใช้
- protected main และ required checks เปิด
- module boundary tests/lint rules มีเท่าที่ทำได้
- test pyramid เริ่มต้นทำงาน
- critical paths มี coverage gate
- secret/dependency scans ผ่าน
- PR template และ CODEOWNERS ถูกกำหนด
