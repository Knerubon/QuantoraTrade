# 08 — API Specification

## 1. Purpose

QuantoraTrade API เป็น Control Plane และ Read API สำหรับตรวจสถานะ ควบคุม Worker ดูผลการวิเคราะห์ ความเสี่ยง คำสั่งซื้อขาย พอร์ต และรายงาน

API **ไม่ใช่ช่องทางข้าม Trading Logic** และไม่เปิด endpoint สำหรับส่ง Market Order แบบอิสระใน MVP

## 2. Technology and Protocol

- Framework: FastAPI
- Contract: OpenAPI 3.1
- Data validation: Pydantic
- Transport: HTTPS
- Payload: JSON UTF-8
- Internal time: UTC ในรูป ISO 8601
- API prefix: `/api/v1`
- Realtime: Server-Sent Events (SSE) ใน MVP
- Authentication: Bearer token สำหรับ development/paper และ OIDC/JWT สำหรับ production
- Request tracing: `X-Request-ID`

## 3. API Principles

- backward-compatible ภายใน major version
- ทุก response มี `request_id`
- mutation ที่ retry ได้ต้องรองรับ `Idempotency-Key`
- command response ใช้สถานะ accepted/pending เมื่อทำงาน asynchronous
- pagination ใช้ cursor
- filter เวลาใช้ `from` และ `to` เป็น UTC
- ห้ามเปิดเผย secret, broker credential หรือ raw sensitive payload
- Live Mode ใช้สิทธิ์และ approval สูงกว่า Paper Mode
- API process ห้ามรัน Trading Loop ภายใน request thread

## 4. Base URLs

| Environment | Example |
|---|---|
| Local | `http://localhost:8000/api/v1` |
| Development | `https://api-dev.example.com/api/v1` |
| Paper | `https://api-paper.example.com/api/v1` |
| Live | กำหนดภายหลังและต้องแยก environment |

OpenAPI:

- `GET /openapi.json`
- `GET /docs` เปิดเฉพาะ environment ที่อนุญาต

## 5. Authentication and Authorization

### Roles

| Role | Read | Start/Stop Paper | Risk controls | Live activation |
|---|---:|---:|---:|---:|
| viewer | ✓ | — | — | — |
| operator | ✓ | ✓ | kill switch only | — |
| risk_manager | ✓ | ✓ | ✓ | — |
| owner | ✓ | ✓ | ✓ | ✓ |
| service | scoped | scoped | — | — |

### Scopes

- `system:read`
- `system:operate`
- `market:read`
- `analysis:read`
- `trading:read`
- `trading:operate`
- `risk:read`
- `risk:operate`
- `live:approve`
- `audit:read`

ทุก mutation ต้องบันทึก actor, action, resource, request ID และเวลาใน Audit Log

## 6. Common Headers

### Request

```http
Authorization: Bearer <token>
Content-Type: application/json
X-Request-ID: req_01J...
Idempotency-Key: idem_01J...
```

`Idempotency-Key` บังคับสำหรับ command endpoints ที่ระบุไว้

### Response

```http
Content-Type: application/json
X-Request-ID: req_01J...
X-API-Version: 1
```

## 7. Common Response Shapes

### Single resource

```json
{
  "data": {},
  "meta": {
    "request_id": "req_01J...",
    "generated_at": "2026-08-15T15:00:00Z"
  }
}
```

### Collection

```json
{
  "data": [],
  "page": {
    "next_cursor": "opaque-token",
    "has_more": true
  },
  "meta": {
    "request_id": "req_01J...",
    "generated_at": "2026-08-15T15:00:00Z"
  }
}
```

### Error

```json
{
  "error": {
    "code": "SPREAD_TOO_WIDE",
    "message": "New entries are blocked for this symbol.",
    "details": {
      "symbol": "XAUUSD"
    },
    "retryable": false
  },
  "meta": {
    "request_id": "req_01J...",
    "generated_at": "2026-08-15T15:00:00Z"
  }
}
```

Error message ห้ามเปิดเผย stack trace, SQL, path ภายใน หรือ credential

## 8. HTTP Status Policy

| Status | Usage |
|---|---|
| 200 | Query/command สำเร็จ |
| 201 | สร้าง resource สำเร็จ |
| 202 | รับ asynchronous command แล้ว |
| 204 | สำเร็จและไม่มี body |
| 400 | Request ผิดรูปแบบเชิง business |
| 401 | ไม่ได้ authenticate |
| 403 | ไม่มีสิทธิ์ |
| 404 | ไม่พบ resource |
| 409 | state conflict หรือ idempotency conflict |
| 422 | schema validation ไม่ผ่าน |
| 429 | rate limit |
| 503 | dependency/system ไม่พร้อม |

## 9. Health and Readiness

### `GET /health/live`

ตรวจว่า API process ทำงาน ไม่ตรวจ dependency

Response `200`:

```json
{
  "data": {
    "status": "alive",
    "service": "quantora-api",
    "version": "0.1.0"
  },
  "meta": {
    "request_id": "req_01J...",
    "generated_at": "2026-08-15T15:00:00Z"
  }
}
```

### `GET /health/ready`

ตรวจ database และ dependency ที่จำเป็นต่อ API

- `200`: ready
- `503`: not ready

### `GET /status`

Scope: `system:read`

คืน:

- mode: backtest/paper/live
- worker state
- kill switch
- broker/data connection
- database status
- enabled symbols/timeframes
- latest market timestamp ต่อ symbol
- active strategy/config/code versions
- open positions/orders summary
- current daily P&L และ drawdown
- degraded reasons

## 10. System Control

### `POST /system/start`

Scope: `system:operate`  
Idempotency-Key: required

Request:

```json
{
  "mode": "paper",
  "symbols": ["XAUUSD", "EURUSD"],
  "strategy_codes": ["trend-pullback"],
  "reason": "Start paper validation run"
}
```

Rules:

- MVP endpoint อนุญาต `paper` เท่านั้น
- `live` ต้องใช้ Live Activation Workflow
- config และ dependency ต้องผ่าน preflight
- ถ้า worker ทำงานด้วย config เดิมแล้วให้คืนผลเดิม
- ถ้า state ขัดแย้งให้คืน `409 SYSTEM_STATE_CONFLICT`

Response `202`:

```json
{
  "data": {
    "command_id": "cmd_01J...",
    "status": "accepted",
    "target_state": "running",
    "mode": "paper"
  },
  "meta": {
    "request_id": "req_01J...",
    "generated_at": "2026-08-15T15:00:00Z"
  }
}
```

### `POST /system/stop`

Scope: `system:operate`  
Idempotency-Key: required

Request:

```json
{
  "behavior": "stop_new_entries",
  "reason": "Scheduled maintenance"
}
```

Behaviors:

- `stop_new_entries`: หยุดคำสั่งใหม่ แต่ยังดูแลสถานะเปิด
- `graceful`: หยุด loop หลังจัดการงานค้าง
- `emergency`: ใช้ Kill Switch policy

การ Stop ไม่ควรปิด position โดยอัตโนมัติ เว้นแต่ request และ policy ระบุชัดเจน

### `GET /commands/{command_id}`

ดูสถานะ asynchronous command:

- accepted
- running
- succeeded
- failed
- cancelled

## 11. Kill Switch

### `POST /risk/kill-switch/activate`

Scope: `risk:operate`  
Idempotency-Key: required

Request:

```json
{
  "scope": "global",
  "symbol": null,
  "reason": "Broker reconciliation mismatch",
  "close_positions": false
}
```

Rules:

- activation ต้องทำงานแม้ AI unavailable
- หยุดสร้าง OrderIntent ใหม่ทันที
- `close_positions=true` เป็น operation แยกที่ต้อง authorization สูงและ confirmation
- บันทึก audit event แบบ append-only

### `POST /risk/kill-switch/deactivate`

Scope: `risk:operate`

Request ต้องมี:

- reason
- incident reference
- confirmation string
- preflight result ที่ยังไม่หมดอายุ

Operator ธรรมดา activate ได้ แต่ deactivate ไม่ได้

### `GET /risk/kill-switch`

Scope: `risk:read`

คืนสถานะ global และ per-symbol พร้อม actor/reason/timestamps

## 12. Live Activation Workflow

Live Mode ห้ามเปิดผ่าน `POST /system/start` โดยตรง

### Step 1 — `POST /live/preflight`

Scope: `live:approve`

ตรวจ:

- strategy status = live_approved
- config hash
- broker connectivity
- symbol specifications
- database/audit availability
- Risk Engine tests
- Kill Switch
- open positions และ pending orders
- clock synchronization

คืน `preflight_id`, checks และ `expires_at`

### Step 2 — `POST /live/activation-requests`

Scope: `live:approve`  
Idempotency-Key: required

```json
{
  "preflight_id": "pre_01J...",
  "symbols": ["XAUUSD"],
  "strategy_codes": ["trend-pullback"],
  "capital_limit": "500.00",
  "confirmation": "ENABLE LIVE TRADING",
  "reason": "Controlled live pilot"
}
```

สร้าง activation request แต่ยังไม่เริ่มเทรด

### Step 3 — `POST /live/activation-requests/{id}/approve`

Scope: `live:approve`

MVP ต้องการ owner confirmation และ request ที่ยังไม่หมดอายุ หลังอนุมัติจึงส่ง asynchronous command ให้ Worker

### `POST /live/deactivate`

Scope: `live:approve`

หยุด new entries ใน Live ทันที การจัดการ positions ใช้ policy แยก

## 13. Instruments and Market Data

### `GET /symbols`

Scope: `market:read`

Filters:

- `asset_class`
- `enabled`
- `broker`
- `cursor`
- `limit` สูงสุด 200

คืน canonical symbol, broker symbol, digits, point, tick size/value, contract size, volume limits, enabled timeframes และ last observed time

### `GET /symbols/{symbol}`

คืน specification และสถานะ readiness ของ symbol

### `GET /candles`

Scope: `market:read`

Required:

- `symbol`
- `timeframe`
- `from`
- `to`

Optional:

- `source`
- `closed_only=true`
- `limit` สูงสุด 5,000

Response ต้องระบุ timezone = UTC และ data-quality flags

### `GET /market-data/issues`

Filters: symbol, timeframe, severity, resolved, from, to

## 14. Analysis and Signals

### `POST /analysis/runs`

Scope: `analysis:read` สำหรับ offline/manual analysis  
Idempotency-Key: required

Request:

```json
{
  "symbol": "EURUSD",
  "timeframe": "M15",
  "as_of": "2026-08-15T15:00:00Z",
  "strategy_code": "trend-pullback",
  "mode": "paper",
  "persist": true
}
```

ข้อจำกัด:

- endpoint นี้สร้าง analysis เท่านั้น
- ไม่มีสิทธิ์สร้าง order
- Live ต้องเรียกจาก Trading Worker ตาม schedule ที่อนุมัติ
- `as_of` ในอนาคตไม่อนุญาต

### `GET /analysis/runs/{analysis_id}`

คืนสถานะ, versions, input window/hash, agent opinions, signal และ error

### `GET /signals`

Filters:

- symbol
- timeframe
- action
- strategy_code/version
- mode
- from/to
- minimum_confidence
- cursor/limit

### `GET /signals/{signal_id}`

คืน evidence, conflicts, reason codes, versions และ linked decision

## 15. Decisions and Risk

### `GET /decisions`

Filters: symbol, action, blocked, mode, from/to, cursor

### `GET /decisions/{decision_id}`

คืน signal, policy version, reason codes, blocking factors และ expiry

### `GET /risk/assessments`

Filters: approved, rejection_code, symbol, mode, from/to

### `GET /risk/assessments/{risk_id}`

คืน calculation inputs และผลลัพธ์ แต่ redact sensitive account details ตาม role

ไม่มี endpoint สำหรับเปลี่ยน RiskAssessment จาก rejected เป็น approved

## 16. Orders, Fills and Positions

### `GET /orders`

Scope: `trading:read`

Filters:

- mode
- symbol
- status
- side
- strategy
- from/to
- cursor/limit

### `GET /orders/{order_id}`

คืน order lifecycle, requested/filled volume, broker references, risk assessment และ trade events

### `POST /orders/{order_id}/cancel`

Scope: `trading:operate`  
Idempotency-Key: required

ยกเลิกเฉพาะ pending/cancellable order และต้องตรวจ broker status ก่อนตอบสำเร็จ

### `GET /fills`

Filters: order_id, symbol, from/to

### `GET /positions`

Filters: mode, symbol, status, strategy

### `GET /positions/{position_id}`

คืน current state, fills, protection status, P&L และ reconciliation timestamp

### `POST /positions/{position_id}/close-requests`

Scope: `trading:operate`  
Idempotency-Key: required

สร้างคำขอปิด position ผ่าน Risk/Execution policy ไม่ส่ง order โดยตรง

Request:

```json
{
  "volume": null,
  "reason": "Manual risk reduction",
  "confirmation": "CLOSE POSITION"
}
```

## 17. Portfolio

### `GET /portfolio/summary`

Scope: `trading:read`

คืน:

- balance/equity/free margin
- realized/unrealized P&L
- daily P&L
- drawdown
- gross/net exposure
- exposure ตาม symbol, asset class และ currency
- open position/order counts
- last reconciled timestamp

### `GET /portfolio/exposure`

รองรับ `group_by=symbol|currency|asset_class|strategy`

## 18. Backtests

### `POST /backtests`

Scope: `trading:operate`  
Idempotency-Key: required

Request:

```json
{
  "strategy_code": "trend-pullback",
  "strategy_version": "1.0.0",
  "symbols": ["XAUUSD", "EURUSD", "GBPUSD"],
  "entry_timeframe": "M15",
  "context_timeframe": "H1",
  "from": "2024-01-01T00:00:00Z",
  "to": "2025-12-31T23:59:59Z",
  "config_snapshot_id": "cfg_01J..."
}
```

Response `202`: backtest ID และ command status

### `GET /backtests/{backtest_id}`

คืน status, versions, dataset hash, assumptions และ summary metrics

### `GET /backtests/{backtest_id}/metrics`

Filters: scope_type, symbol, timeframe, regime

### `GET /backtests/{backtest_id}/trades`

Cursor pagination; trade จำนวนมากให้คืน artifact download reference ที่มีอายุจำกัด

### `POST /backtests/{backtest_id}/cancel`

ยกเลิก run ที่ยังทำงาน ไม่ลบผลลัพธ์ที่สร้างแล้ว

## 19. Reports

### `GET /reports/performance`

Parameters:

- mode
- from/to
- symbols
- strategies
- group_by
- benchmark optional

Metrics:

- net return
- max drawdown
- win rate
- profit factor
- expectancy
- trade count
- costs และ slippage

### `GET /reports/daily`

รายงานประจำวันสำหรับ Dashboard/Telegram

## 20. Configuration Read API

### `GET /config/active`

คืน config ที่ redact secret แล้ว พร้อม hash และ version

### `GET /strategies`

คืน strategy versions และ approval status

### `GET /models`

คืน model metadata, evaluation metrics และ status ไม่คืน artifact โดยตรง

MVP ไม่มี generic endpoint สำหรับแก้ config แบบ arbitrary JSON การเปลี่ยน config ใช้ versioned file/review workflow

## 21. Events

### `GET /events`

Filters:

- severity
- component
- event_code
- symbol
- correlation_id
- from/to

### `GET /stream/events`

SSE endpoint สำหรับ:

- system status
- signals
- decisions
- risk rejections
- order updates
- position updates
- alerts

Client ต้อง reconnect ด้วย `Last-Event-ID`; server ไม่รับประกันว่า SSE เป็น System of Record ให้ query REST เพื่อ reconcile

## 22. Pagination

Request:

- `limit`: default 50, max 200
- `cursor`: opaque token

Sort เริ่มต้นใช้ `created_at DESC, id DESC`

ห้ามใช้ offset pagination กับ orders/events/candles ขนาดใหญ่ เพราะผลอาจเลื่อนและช้า

## 23. Filtering and Time

- timestamp ทุกตัวต้องมี timezone
- server normalize เป็น UTC
- `from` inclusive
- `to` exclusive
- maximum query range ต่อ endpoint ต้องกำหนดใน config
- invalid symbol/timeframe คืน `422`
- symbol ใช้ canonical code ใน API และ map ไป broker symbol ภายใน

## 24. Idempotency

ใช้กับ mutation ที่อาจ retry:

- system start/stop
- kill switch
- live activation
- order cancel
- position close request
- backtest create/cancel

Server เก็บ:

- actor
- endpoint
- idempotency key
- request hash
- response/status
- expiry

กฎ:

- key เดิม + request เดิม: คืน response เดิม
- key เดิม + request ต่างกัน: `409 IDEMPOTENCY_CONFLICT`
- key ต้อง scoped ตาม actor + endpoint
- execution-related key ต้องเก็บนานพอสำหรับ reconciliation และ incident review

## 25. Rate Limits

ตัวอย่างเริ่มต้น:

- query: 120 requests/minute ต่อ actor
- command: 20 requests/minute
- analysis trigger: 10 requests/minute
- backtest creation: 5 requests/hour
- live/risk commands: จำกัดเข้มและ alert ทุกครั้ง

Rate limit จริงต้องปรับตาม deployment และห้ามทำให้ Kill Switch ถูก block; Kill Switch ใช้ dedicated priority path พร้อม abuse protection

## 26. Error Codes

### Validation

- `VALIDATION_ERROR`
- `INVALID_SYMBOL`
- `INVALID_TIMEFRAME`
- `INVALID_TIME_RANGE`

### System

- `SYSTEM_NOT_READY`
- `SYSTEM_STATE_CONFLICT`
- `DEPENDENCY_UNAVAILABLE`
- `COMMAND_TIMEOUT`

### Trading/Risk

- `STALE_DATA`
- `SPREAD_TOO_WIDE`
- `SESSION_BLOCKED`
- `NEWS_BLOCK`
- `RISK_REJECTED`
- `DAILY_LOSS_LIMIT`
- `KILL_SWITCH_ACTIVE`
- `DECISION_EXPIRED`
- `POSITION_NOT_RECONCILED`
- `ORDER_NOT_CANCELLABLE`

### Security

- `UNAUTHENTICATED`
- `FORBIDDEN`
- `LIVE_APPROVAL_REQUIRED`
- `CONFIRMATION_MISMATCH`
- `RATE_LIMITED`

## 27. Concurrency and Consistency

- mutation ใช้ optimistic locking หรือ state transition guard
- response ที่แก้ resource คืน `version`
- client ส่ง `If-Match` เมื่อ endpoint กำหนด
- order/position status จาก API อาจเป็น eventually consistent กับ broker
- response ต้องมี `last_reconciled_at`
- API ห้ามรายงาน order success จน broker ยืนยัน; ระหว่างรอใช้ `pending_confirmation`

## 28. Security Requirements

- HTTPS เท่านั้นนอก local
- token อายุสั้นและ rotate ได้
- CORS allowlist
- request body size limit
- schema validation ก่อน business logic
- parameterized database queries
- redact secrets และ broker identifiers
- audit ทุก privileged command
- Live endpoints แยก scope และอาจจำกัด network
- ห้ามรับ broker password/API key ผ่าน generic API
- Swagger UI ปิดใน Live หรือจำกัดสิทธิ์
- dependency error ไม่คืนรายละเอียดภายใน

## 29. OpenAPI and Client Generation

- Pydantic models เป็น contract source
- CI ตรวจ OpenAPI breaking changes
- generate typed client สำหรับ Dashboard ภายหลัง
- schema ทุกตัวมี example และ field description
- monetary/price values ส่งเป็น JSON string เพื่อรักษา decimal precision
- enum และ error codes versioned

## 30. Testing Requirements

### Contract tests

- OpenAPI schema validation
- request/response examples
- error envelope ทุก endpoint
- decimal serialization
- UTC timestamps

### Security tests

- missing/expired token
- role/scope matrix
- Live endpoint denial
- secret redaction
- rate limits
- audit records

### Idempotency/concurrency tests

- duplicate start
- same key/different payload
- concurrent close requests
- cancel หลัง fill
- worker/API retry
- broker timeout และ reconciliation

### State tests

- start เมื่อ running
- stop เมื่อ stopped
- kill switch activate/deactivate
- expired preflight
- rejected risk ไม่มี order endpoint path
- stale position close request

## 31. Initial Implementation Order

1. shared response/error models
2. request ID middleware
3. authentication and scopes
4. health/readiness/status
5. symbols and market-data queries
6. signals, decisions and risk queries
7. orders, positions and portfolio queries
8. system commands and idempotency store
9. Kill Switch
10. backtest commands/reports
11. SSE events
12. Live Activation Workflow หลัง Paper ผ่านเกณฑ์

## 32. Definition of Done

API Specification พร้อม implement เมื่อ:

- OpenAPI contract ครอบคลุม MVP endpoints
- role/scope tests ครบ
- mutation สำคัญมี idempotency
- Live เปิดผ่าน start endpoint ไม่ได้
- Kill Switch มี priority path และ audit
- ไม่มี endpoint ที่ AI หรือผู้ใช้ข้าม Risk Engine ไปส่ง order ตรง
- decimal/time/cursor conventions ใช้เหมือนกันทุก endpoint
- API integration tests ทำงานกับ PostgreSQL และ fake broker adapter
