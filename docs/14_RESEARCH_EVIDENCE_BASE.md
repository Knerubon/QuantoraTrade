# 14 — Research Evidence Base

## 1. Purpose

เอกสารนี้เก็บหลักฐานที่ใช้กำหนดสมมติฐานและเกณฑ์ทดสอบของ QuantoraTrade
ไม่ใช่รายการสูตรทำกำไร และไม่อนุญาตให้นำผลวิจัยไปเปิด Live Trading โดยตรง

หลักฐานทุกชิ้นต้องแยกให้ออกจากผล in-sample ที่ยังไม่มี out-of-sample confirmation,
สมมติฐานที่ยังไม่ผ่านข้อมูลของระบบ และคำโฆษณาที่ไม่เปิดเผยวิธีทดลองหรือต้นทุน

## 2. Evidence Summary

| Area | Evidence | Important limitation | QuantoraTrade implication |
|---|---|---|---|
| Technical rules | กฎเทคนิคบางชุดใน FX เคยให้ผล OOS หลังต้นทุน | ผลต่างกันตามคู่เงิน ยุค และความถี่ | ทดสอบ indicator family แยกกันและล็อกพารามิเตอร์ |
| Support/Resistance | S/R และเลขกลมสัมพันธ์กับการเด้ง/การกระจุกของคำสั่งในบางข้อมูล FX | dealer levels ไม่เท่ากับ algorithmic swing zones | เทียบ zone กับ random/control levels |
| Candlesticks | บาง pattern มี predictive information ในบางตลาด | อ่อนไหวต่อนิยาม และหลักฐานส่วนใหญ่ไม่ใช่ XAUUSD | ใช้เป็น confirmation feature ไม่ใช่ standalone rule |
| Validation | การลองหลาย configuration เพิ่ม backtest overfitting | Sharpe จาก run ที่ดีที่สุดมี selection bias | ลงทะเบียนทุก trial, walk-forward และ final holdout |
| Costs | FX เป็น OTC และต้นทุนเปลี่ยนตาม venue/session/volatility | midpoint หรือ fixed spread ทำให้ผลดีเกินจริง | จำลอง bid/ask, slippage, commission, swap และ stress cost |
| Risk | volatility scaling ช่วยลดความเสี่ยงในหลายกลุ่มสินทรัพย์ | หลักฐานไม่ใช่ XAUUSD โดยตรง | ให้ Risk Engine แยกจาก signal และมีสิทธิ์ veto |
| AI/ML | โมเดลซับซ้อนไม่ชนะ baseline ง่ายอย่างสม่ำเสมอ | forecast error ต่ำไม่เท่ากับ net trading profit | ML เป็น challenger และวัดผลหลังต้นทุน |

## 3. Primary Sources and Findings

### 3.1 Technical rules in FX

- Neely, Weller และ Dittmar (1996) ใช้ genetic programming กับอัตราแลกเปลี่ยน
  6 สกุลและข้อมูลช่วง 1981–1995 พบผล OOS หลัง transaction costs ในบางกรณี
  แต่ผลต่างกันตามสกุลเงินและช่วงเวลา:
  <https://files.stlouisfed.org/files/htdocs/wp/1996/96-006.pdf>
- Neely และ Weller ศึกษา intraday rules แบบ OOS และพบว่าผลเปราะบางขึ้นเมื่อรวม
  trading hours และต้นทุนที่สมจริง:
  <https://files.stlouisfed.org/files/htdocs/wp/1999/99-016.pdf>
- งานปี 2021 ทดลอง technical rules 1,846 แบบ พร้อม data-snooping controls และ
  Bayesian/ML combination พบข้อมูลเชิงพยากรณ์บางส่วน แต่ margin มีขนาดเล็ก:
  <https://eprints.gla.ac.uk/246954/2/246954.pdf>

งานเหล่านี้ส่วนใหญ่เป็น institutional FX/futures และไม่ใช่ retail MT5 CFD หรือ
XAUUSD โดยตรง จึงใช้ตั้งสมมติฐานได้ แต่ใช้ยืนยัน profitability ของระบบไม่ได้

### 3.2 Support/Resistance and order clustering

- Osler (2000) ทดสอบระดับ S/R จากบริษัท 6 แห่ง ช่วงมกราคม 1996–มีนาคม 1998
  ใน DEM, JPY และ GBP พบ bounce frequency สูงกว่าระดับควบคุมในบางกรณี และมี
  heterogeneity สูงระหว่าง firm/currency:
  <https://www.newyorkfed.org/research/epr/00v06n2/0007osle.html>
- ข้อมูลคำสั่ง FX พบ order clustering ใกล้เลขกลม และตำแหน่ง stop-loss/take-profit
  ที่ต่างกัน ซึ่งเป็นกลไกที่อาจอธิบายพฤติกรรม S/R:
  <https://fraser.stlouisfed.org/files/docs/publications/frbnysr/frbny_sr125.pdf>

### 3.3 Candlestick patterns

- Chen et al. ทดสอบ bullish/bearish two-day patterns ในหุ้นจีนและพบ predictive
  information บางส่วน แต่ไม่ใช่หลักฐานจาก FX หรือทองคำ:
  <https://www.sciencedirect.com/science/article/abs/pii/S0378437116300796>
- Horton ทดสอบหุ้น 349 ตัว โดยรวม transaction costs, data-snooping controls และ
  OOS evaluation:
  <https://www.sciencedirect.com/science/article/abs/pii/S106297690700097X>
- ผลของ engulfing เปลี่ยนตามนิยามราคาที่ใช้ตรวจ pattern:
  <https://www.sciencedirect.com/science/article/abs/pii/S1062976920300806>

### 3.4 Backtest overfitting and leakage

- Probability of Backtest Overfitting และ CSCV:
  <https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2326253>
- จำนวน configurations ที่มากขึ้นสัมพันธ์กับ OOS degradation:
  <https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2308659>
- Deflated Sharpe Ratio ปรับ selection bias, multiple trials และ non-normality:
  <https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2460551>
- Multiple testing ทำให้เกณฑ์ t-statistic แบบเดิมอ่อนเกินไป:
  <https://academic.oup.com/rfs/article/29/1/5/1843824>

### 3.5 Costs, liquidity and execution

- BIS อธิบายโครงสร้าง FX แบบ OTC ที่กระจายหลาย venue และ execution path:
  <https://www.bis.org/publ/qtrpdf/r_qt1912g.htm>
- Algorithmic trading อาจลด average spread แต่เพิ่ม liquidity fragility ใน tail:
  <https://www.bis.org/publ/work1229.pdf>
- BIS รายงาน FX turnover เดือนเมษายน 2025 ที่ประมาณ USD 9.5 trillion ต่อวัน
  แต่ volume สูงไม่ได้ทำให้ต้นทุนคงที่ทุก symbol/session:
  <https://www.bis.org/publ/qtrpdf/r_qt2512b.htm>

### 3.6 Regime and risk

- หลักฐาน Adaptive Markets ใน FX แสดงว่าประสิทธิภาพของ trading rules เปลี่ยนตามเวลา:
  <https://fraser.stlouisfed.org/files/docs/publications/frbsl_wp/2006-046.pdf>
- Moreira และ Muir (2016) พบประโยชน์ของ volatility-managed portfolios ในหลาย
  factor portfolios แต่ไม่ใช่หลักฐานตรงสำหรับ XAUUSD:
  <https://www.nber.org/system/files/working_papers/w22208/w22208.pdf>
- แนวทางของธนาคารแห่งประเทศไทยครอบคลุมการวัดและควบคุม FX, liquidity และ
  settlement risks:
  <https://www.bot.or.th/content/dam/bot/fipcs/documents/FPG/2548/EngPDF/25480154.pdf>

### 3.7 AI/ML

- งาน CNN-Bi-LSTM สำหรับทองคำเน้น price forecast; RMSE ที่ดีไม่ยืนยัน net profit:
  <https://pmc.ncbi.nlm.nih.gov/articles/PMC10919698/>
- Commodity forecasting พบ LSTM ไม่ได้ชนะ ARIMA แบบ OOS อย่างสม่ำเสมอ:
  <https://arxiv.org/abs/2101.03087>
- BIS/FSI ระบุ model, data, validation และ governance risks ของ AI/ML:
  <https://www.bis.org/fsi/publ/insights63.htm>

### 3.8 Thailand-specific evidence

งาน BOT ที่ใช้รายงานธุรกรรม FX รายวันของธนาคารไทยพบว่า order flow ของผู้เล่นบางกลุ่ม
สัมพันธ์กับอัตราแลกเปลี่ยนทั้ง contemporaneous และ long-run แม้ข้อมูล THB institutional
flow จะไม่ใช่ XAUUSD retail:
<https://www.bot.or.th/content/dam/bot/documents/th/research-and-publications/research/discussion-paper-and-policy-paper/dp200905-default_thai.pdf>

## 4. Pre-Registered Hypotheses

- **H1:** EMA trend + S/R breakout มี positive net expectancy เฉพาะ trend และ
  high-liquidity regimes
- **H2:** Candlestick เพียงลำพังไม่มี edge หลัง costs แต่เพิ่ม precision เมื่อประกบ
  location และ trend
- **H3:** Round-number proximity เพิ่มข้อมูลสำหรับ bounce/break classification ใน XAUUSD
- **H4:** Volatility sizing ลด drawdown โดยไม่ทำลาย OOS risk-adjusted return
- **H5:** ML เพิ่ม calibration และ net utility เหนือ deterministic baseline หลัง costs

## 5. Mandatory Research Gates

Strategy หรือ model ต้องอยู่ในสถานะ `HOLD / RESEARCH ONLY` จนกว่าจะผ่านทั้งหมด:

1. positive net expectancy ในหลาย chronological walk-forward folds
2. final untouched OOS ผ่านเกณฑ์ที่ลงทะเบียนล่วงหน้า
3. parameter neighborhood มีเสถียรภาพ ไม่ใช่ isolated optimum
4. รายงานทุก trial และใช้ PBO/Deflated Sharpe เมื่อจำนวน trials เหมาะสม
5. ยังไม่พังภายใต้ cost stress 1.0x, 1.5x และ 2.0x
6. maximum drawdown, turnover และ coverage อยู่ใน limits
7. ไม่มี look-ahead, label leakage หรือการ fit transform บน validation/test
8. ผ่าน Paper Trading ก่อนพิจารณา Live และ Live ต้องได้รับอนุมัติจากเจ้าของโครงการ

## 6. Knowledge Maintenance Policy

- เพิ่มแหล่งต้นฉบับ วารสาร มหาวิทยาลัย ธนาคารกลาง หรือหน่วยงานกำกับเป็นหลัก
- บันทึกปี วิธีทดลอง ตลาด ช่วงข้อมูล ผล ข้อจำกัด และความเกี่ยวข้องกับ XAUUSD/Forex
- บทความการตลาดใช้เป็นเบาะแสค้นคว้าเท่านั้น ไม่ใช้เป็น evidence gate
- เมื่อหลักฐานใหม่ขัดกับเอกสารเดิม ให้เก็บทั้งสองด้านและระบุเหตุผล
- การเปลี่ยน strategy จากหลักฐานใหม่ต้องผ่าน review, reproducible test และ OOS gate
