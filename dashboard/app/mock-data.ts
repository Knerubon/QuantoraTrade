export const agents = [
  { name: "ริน", role: "Lead · Orchestrator", icon: "👩🏻‍💻", status: "กำลังวางแผน", tone: "cyan", task: "Command Center v0.1", progress: 82 },
  { name: "มิน", role: "Research Agent", icon: "🔬", status: "กำลังวิจัย", tone: "violet", task: "Market regime evidence", progress: 64 },
  { name: "Atlas", role: "Strategy Agent", icon: "📐", status: "พร้อมทำงาน", tone: "blue", task: "รอ market snapshot", progress: 100 },
  { name: "Shield", role: "Risk Agent", icon: "🛡️", status: "กำลังตรวจสอบ", tone: "amber", task: "Validate PAPER limits", progress: 46 },
  { name: "Pulse", role: "Execution Agent", icon: "⚡", status: "หยุดรอ", tone: "slate", task: "LIVE blocked", progress: 0 },
];

export const navItems = [
  ["/", "⌂", "ภาพรวม"],
  ["/live-market", "⌁", "ตลาด"],
  ["/ai-agents", "◇", "AI Agents"],
  ["/pixel-office", "▦", "Pixel Office"],
  ["/orders", "⇅", "Orders"],
  ["/positions", "◎", "Positions"],
  ["/performance", "↗", "Performance"],
  ["/logs", "≡", "Logs"],
  ["/settings", "⚙", "Settings"],
] as const;

export const orders = [
  { time: "08:07:21", symbol: "XAUUSD", side: "BUY", volume: "0.01", price: "3,487.62", status: "FILLED", agent: "Atlas" },
  { time: "07:42:10", symbol: "EURUSD", side: "SELL", volume: "0.02", price: "1.16642", status: "PAPER", agent: "Atlas" },
  { time: "07:16:04", symbol: "XAUUSD", side: "HOLD", volume: "—", price: "3,481.20", status: "FILTERED", agent: "Shield" },
];

export const logs = [
  ["08:09:14", "RISK", "Position size approved · XAUUSD 0.01 lot"],
  ["08:09:02", "STRATEGY", "EMA 9/21 alignment detected · confidence 0.72"],
  ["08:08:47", "MARKET", "M5 candle persisted · XAUUSD"],
  ["08:08:31", "SYSTEM", "Worker heartbeat received · latency 42 ms"],
] as const;
