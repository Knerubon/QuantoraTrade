# QuantoraTrade

แพลตฟอร์มช่วยวิเคราะห์และพัฒนาระบบเทรดเชิงปริมาณ โดยเริ่มจากตลาดทองคำ **XAUUSD** และออกแบบให้ทดสอบได้ก่อนใช้งานจริง

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

- สินทรัพย์: XAUUSD
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

เอกสารลำดับถัดไปจะครอบคลุม System Architecture, AI Agents, Trading Logic, Database, API, Risk Management, Backtesting, Coding Standards และ Project Decisions.

## สถานะสำคัญ

โครงการนี้อยู่ในขั้นออกแบบและวิจัย ห้ามเปิด Live Trade จนกว่าจะผ่านเกณฑ์ Backtest, Paper Trade และการอนุมัติจากเจ้าของโครงการ
