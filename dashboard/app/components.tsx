"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { agents, logs, navItems, orders } from "./mock-data";

export function Shell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  return (
    <div className="app-shell">
      <aside className="sidebar">
        <Link className="brand" href="/" aria-label="QuantoraTrade home">
          <span className="brand-mark">Q</span><span><b>QUANTORA</b><small>COMMAND CENTER</small></span>
        </Link>
        <nav aria-label="เมนูหลัก">
          {navItems.map(([href, icon, label]) => (
            <Link key={href} className={pathname === href ? "nav-item active" : "nav-item"} href={href}>
              <span className="nav-icon">{icon}</span>{label}
            </Link>
          ))}
        </nav>
        <div className="sidebar-foot"><span className="pulse-dot"/><div><b>PAPER ONLINE</b><small>LIVE ถูกล็อก</small></div></div>
      </aside>
      <main className="main">
        <header className="topbar">
          <div><span className="eyebrow">CONTROL ROOM</span><h1>{titleFor(pathname)}</h1></div>
          <div className="top-actions"><div className="clock"><span>UTC+7</span><b>08:10:42</b></div><button className="mode-pill">● PAPER</button><button className="icon-button" aria-label="การแจ้งเตือน">◔<i>2</i></button></div>
        </header>
        {children}
      </main>
    </div>
  );
}

function titleFor(pathname: string) {
  return ({ "/": "ภาพรวมระบบ", "/live-market": "Live Market", "/ai-agents": "AI Agent Fleet", "/pixel-office": "Pixel Office", "/orders": "Orders", "/positions": "Positions", "/performance": "Performance", "/logs": "System Logs", "/settings": "Settings" } as Record<string, string>)[pathname] ?? "Command Center";
}

export function StatusStrip() {
  return <section className="status-strip" aria-label="สถานะระบบ">
    <Status name="MT5 Terminal" value="Connected" meta="Account ••••4338" ok />
    <Status name="Market Data" value="Streaming" meta="42 ms · XAUUSD M5" ok />
    <Status name="PAPER Worker" value="Running" meta="Heartbeat 4s ago" ok />
    <Status name="Risk Gate" value="Protected" meta="LIVE hard-locked" warn />
  </section>;
}

function Status({ name, value, meta, ok, warn }: { name: string; value: string; meta: string; ok?: boolean; warn?: boolean }) {
  return <div className="status-cell"><span className={`signal ${ok ? "ok" : warn ? "warn" : ""}`}/><div><small>{name}</small><b>{value}</b><em>{meta}</em></div></div>;
}

export function Overview() {
  return <div className="content-grid">
    <StatusStrip />
    <section className="market-card panel">
      <div className="panel-head"><div><span className="kicker">XAUUSD · M5</span><div className="quote"><b>3,489.72</b><span>+8.42 · +0.24%</span></div></div><div className="segmented"><button className="selected">M5</button><button>M15</button><button>H1</button></div></div>
      <div className="chart-wrap"><svg viewBox="0 0 760 230" role="img" aria-label="XAUUSD price chart"><defs><linearGradient id="area" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stopColor="#35d7ff" stopOpacity=".35"/><stop offset="1" stopColor="#35d7ff" stopOpacity="0"/></linearGradient></defs><path className="grid-lines" d="M0 35H760M0 90H760M0 145H760M0 200H760"/><path fill="url(#area)" d="M0 177L35 166L70 172L105 143L140 151L175 130L210 140L245 104L280 120L315 89L350 98L385 75L420 82L455 58L490 78L525 52L560 66L595 41L630 49L665 27L700 38L735 17L760 24V230H0Z"/><polyline className="price-line" points="0,177 35,166 70,172 105,143 140,151 175,130 210,140 245,104 280,120 315,89 350,98 385,75 420,82 455,58 490,78 525,52 560,66 595,41 630,49 665,27 700,38 735,17 760,24"/></svg><div className="chart-axis"><span>06:00</span><span>06:30</span><span>07:00</span><span>07:30</span><span>08:00</span></div></div>
      <div className="signal-row"><span>EMA 9 <b>3,486.40</b></span><span>EMA 21 <b>3,482.18</b></span><span>RSI <b>62.4</b></span><span>REGIME <b className="cyan">TREND</b></span><span>SIGNAL <b className="positive">BUY · 72%</b></span></div>
    </section>
    <section className="metrics"><Metric label="Equity" value="$10,124.80" delta="+1.24%"/><Metric label="Today P&L" value="+$38.42" delta="3 trades"/><Metric label="Open Risk" value="0.42%" delta="limit 2.00%"/><Metric label="Drawdown" value="0.68%" delta="safe"/></section>
    <section className="panel agents-mini"><div className="panel-head"><div><span className="kicker">AGENT ACTIVITY</span><h2>ทีมกำลังทำอะไร</h2></div><Link href="/ai-agents">ดูทั้งหมด →</Link></div>{agents.slice(0,4).map(agent => <AgentRow key={agent.name} agent={agent}/>)}</section>
    <section className="panel activity"><div className="panel-head"><div><span className="kicker">EVENT STREAM</span><h2>เหตุการณ์ล่าสุด</h2></div><span className="live-label">● LIVE</span></div>{logs.map(([time,type,message]) => <div className="log-line" key={time}><time>{time}</time><b>{type}</b><span>{message}</span></div>)}</section>
  </div>;
}

function Metric({ label, value, delta }: { label: string; value: string; delta: string }) { return <div className="metric panel"><small>{label}</small><b>{value}</b><span>{delta}</span></div>; }
function AgentRow({ agent }: { agent: typeof agents[number] }) { return <div className="agent-row"><span className={`avatar ${agent.tone}`}>{agent.icon}</span><div className="agent-copy"><b>{agent.name}<small>{agent.role}</small></b><span>{agent.task}</span></div><div className="agent-state"><span>{agent.status}</span><div><i style={{width:`${agent.progress}%`}}/></div></div></div>; }

export function AgentsView() { return <div className="page-stack"><StatusStrip/><div className="agent-grid">{agents.map(agent => <section className="agent-card panel" key={agent.name}><div className="agent-card-top"><span className={`avatar large ${agent.tone}`}>{agent.icon}</span><span className="online-dot"/></div><h2>{agent.name}</h2><p>{agent.role}</p><div className="task-box"><small>CURRENT TASK</small><b>{agent.task}</b><div><i style={{width:`${agent.progress}%`}}/></div><span>{agent.progress}%</span></div><footer><span>Last event</span><time>ไม่เกิน 1 นาที</time></footer></section>)}</div></div>; }

export function PixelOffice() { return <div className="page-stack"><div className="office-toolbar"><div><b>Quantora Operations Floor</b><span>Agent movement will follow backend events</span></div><span className="live-label">● 5 AGENTS ONLINE</span></div><section className="pixel-office panel"><div className="room research"><label>RESEARCH LAB</label><Pixel agent={agents[1]}/><div className="pixel-desk">▤ ▥</div></div><div className="room strategy"><label>STRATEGY ROOM</label><Pixel agent={agents[2]}/><div className="pixel-board">EMA<br/>RSI<br/>ATR</div></div><div className="room lead"><label>CONTROL DESK</label><Pixel agent={agents[0]}/><div className="pixel-console">◼ ◼ ◼</div></div><div className="room risk"><label>RISK GATE</label><Pixel agent={agents[3]}/><div className="pixel-shield">◆</div></div><div className="room execution"><label>EXECUTION</label><Pixel agent={agents[4]}/><div className="locked-door">LIVE<br/><b>LOCKED</b></div></div><div className="office-path"><span>MARKET DATA</span><i>→</i><span>STRATEGY</span><i>→</i><span>RISK</span><i>→</i><span>EXECUTION</span></div></section></div>; }
function Pixel({agent}:{agent:typeof agents[number]}) { return <div className="pixel-agent"><span>{agent.icon}</span><b>{agent.name}</b><small>{agent.status}</small></div>; }

export function TableView({ type }: { type: "orders" | "positions" }) { return <div className="page-stack"><StatusStrip/><section className="panel table-panel"><div className="panel-head"><div><span className="kicker">PAPER ACCOUNT</span><h2>{type === "orders" ? "คำสั่งล่าสุด" : "สถานะการถือครอง"}</h2></div><button className="outline-button">Export CSV</button></div><div className="table-scroll"><table><thead><tr>{["Time","Symbol","Side","Volume","Price","Status","Agent"].map(h=><th key={h}>{h}</th>)}</tr></thead><tbody>{orders.map(row=><tr key={row.time}><td>{row.time}</td><td><b>{row.symbol}</b></td><td className={row.side === "BUY" ? "positive" : row.side === "SELL" ? "negative" : ""}>{row.side}</td><td>{row.volume}</td><td>{row.price}</td><td><span className={`tag ${row.status.toLowerCase()}`}>{row.status}</span></td><td>{row.agent}</td></tr>)}</tbody></table></div></section></div>; }

export function GenericView({ view }: { view: string }) { const copy: Record<string,[string,string]>={"live-market":["Market Scanner","XAUUSD และ EURUSD กำลังรับข้อมูลผ่าน MT5 adapter"],performance:["Performance","Equity, drawdown และผลลัพธ์ PAPER แบบตรวจสอบย้อนหลังได้"],logs:["System Logs","รวม Market → Strategy → Risk → Execution events"],settings:["Safety Settings","PAPER configuration และ guardrails — LIVE ถูกล็อก"]}; const item=copy[view] ?? [view,"QuantoraTrade operational view"]; return <div className="page-stack"><StatusStrip/><section className="panel empty-view"><span className="empty-icon">◎</span><span className="kicker">COMMAND CENTER V0.1</span><h2>{item[0]}</h2><p>{item[1]}</p><div className="coming-line"><i/><span>โครงหน้าจอพร้อมเชื่อม Backend event stream</span></div></section></div>; }
