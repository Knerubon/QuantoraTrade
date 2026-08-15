# QuantoraTrade

แพลตฟอร์มช่วยวิเคราะห์และพัฒนาระบบเทรดเชิงปริมาณ รองรับสินทรัพย์หลายประเภท โดยเริ่มทดสอบกับ **XAUUSD และตลาด Forex** และออกแบบให้ขยายตลาดได้โดยไม่ผูก logic กับสัญลักษณ์ใดสัญลักษณ์หนึ่ง

> สถานะ: เริ่มต้นวางรากฐานโครงการ (Planning & Documentation)

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

เอกสาร Foundation ชุดแรกครบแล้ว ขั้นถัดไปคือสร้าง Python project scaffold, configuration schemas, domain contracts และ CI.

## สถานะสำคัญ

โครงการนี้อยู่ในขั้นออกแบบและวิจัย ห้ามเปิด Live Trade จนกว่าจะผ่านเกณฑ์ Backtest, Paper Trade และการอนุมัติจากเจ้าของโครงการ
