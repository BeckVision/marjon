import { useState, useEffect } from "react";

// ─── Data ────────────────────────────────────────────────────────────────────

const FLOW_LABELS = ["scope defines", "aligned by", "time-filtered by", "transformed by", "validated by", "queried from"];

const CONCEPTS = [
  {
    id: "universe",
    num: "01",
    name: "Universe Definition",
    question: "Which coins are in this dataset, and how much time does each one cover?",
    color: "#e8813a",
    status: "defined",
    statusLabel: "U-001 defined",
    icon: "◎",
    explain: "The universe draws a box around what's included. Every coin that meets the criteria enters the dataset — no cherry-picking, no exceptions. The universe is defined once and shared across all feature layers, join keys, and reference datasets.",
    whyItMatters: "Without a strict universe definition, you'd end up with an inconsistent dataset where some coins have 3 days of data and others have 7 days. Your backtest results would be meaningless because each coin was measured differently.",
    realWorld: 'In your case: every single token that graduated from pump.fun to Pumpswap enters the dataset. The coin that pumped 10,000% AND the coin that rugged in 30 seconds — both are in. This is what makes your backtest trustworthy.',
    spec: [
      { key: "Universe ID", val: "U-001", type: "id" },
      { key: "Which coins?", val: "All pump.fun → Pumpswap graduated tokens", type: "normal" },
      { key: "T0 (anchor event)", val: "Graduation time — the exact moment the coin migrated to Pumpswap", type: "highlight" },
      { key: "Observation window", val: "T0 → T0 + 5,000 minutes (~3.47 days)", type: "highlight" },
      { key: "Window boundary", val: "Candle-aligned inclusive — partial overlap at edges is included", type: "normal" },
      { key: "Exclusion criteria", val: "None. Intentional — avoids survivorship bias.", type: "good" },
    ],
    decisions: [
      { title: "No exclusions", text: "In live trading, you can't know which coins will rug. Your backtest must include them all to match reality." },
      { title: "Candle-aligned inclusive", text: "The first candle after graduation is included even if it only partially overlaps T0. This preserves the volatile first moments." }
    ],
    edgeCases: [
      { text: "Is T0 inclusive or exclusive?", resolution: "Candle-aligned inclusive — any 5-min bucket overlapping the window counts", resolved: true }
    ],
    connections: ["Feature Layer", "Join Key", "Reference Dataset"]
  },
  {
    id: "feature-layer",
    num: "02",
    name: "Feature Layer",
    question: "What numbers are we recording for each coin at each point in time?",
    color: "#3dba52",
    status: "partial",
    statusLabel: "FL-001 ✓ · FL-002 partial",
    icon: "◈",
    explain: "The universe says WHICH coins and WHEN. Feature layers say WHAT we measure. Each layer is completely independent — its own resolution, its own data source, its own gap handling rules. You can have many layers on one universe.",
    whyItMatters: "Separating measurements into independent layers means you can add new data (holder counts, liquidity depth, smart wallet activity) without touching existing layers. A strategy picks exactly which layers it needs — no more, no less.",
    realWorld: "You have two layers: FL-001 (OHLCV from DexPaprika) and FL-002 (holder snapshots from Moralis). They measure completely different things from completely different APIs, but they describe the same coins over the same time window.",
    layers: [
      {
        id: "FL-001",
        name: "OHLCV Price Data",
        status: "defined",
        fields: [
          { name: "open", desc: "Price at interval start" },
          { name: "high", desc: "Highest price in interval" },
          { name: "low", desc: "Lowest price in interval" },
          { name: "close", desc: "Price at interval end" },
          { name: "volume", desc: "Total trade volume" },
          { name: "market_cap", desc: "Fully diluted valuation (nullable)" },
        ],
        resolution: "5-minute candles",
        gap: "No candle created if no trades occurred",
        gapExplain: "If nobody traded the token between 1:10 and 1:15, that row simply doesn't exist. This is a deliberate choice — no synthetic data.",
        source: "DexPaprika / GeckoTerminal",
        refresh: "Daily"
      },
      {
        id: "FL-002",
        name: "Holder Snapshots",
        status: "blocked",
        fields: [
          { name: "total_holders", desc: "Current holder count" },
          { name: "net_holder_change", desc: "Holders gained minus lost" },
          { name: "holder_percent_change", desc: "% change from previous" },
          { name: "acquired_via_swap", desc: "New holders from trading" },
          { name: "acquired_via_transfer", desc: "New holders from transfers" },
          { name: "acquired_via_airdrop", desc: "New holders from airdrops" },
          { name: "size tiers in/out", desc: "Whales, sharks, dolphins, fish, octopus, crabs, shrimps — entering and exiting" },
        ],
        resolution: "5-minute snapshots",
        gap: "BLOCKED — need to check what Moralis API actually returns",
        gapExplain: "Holder state exists even without trades (unlike OHLCV). Whether Moralis reports every interval or only on change determines the gap rule.",
        source: "Moralis API",
        refresh: "Daily"
      }
    ],
    decisions: [
      { title: "Independent layers", text: "OHLCV and holder data live in separate tables with separate specs. They're joined at backtest time, not stored together." },
      { title: "Future FL-003", text: "Aggregated transaction summaries (tx_count, buy_vol per 5-min) may become a new feature layer later." }
    ],
    edgeCases: [
      { text: "market_cap is nullable — does the row count as 'exists' for inner join?", resolution: "Yes — row-level existence. The row counts if it exists in the table, regardless of null fields.", resolved: true },
      { text: "Strategy uses only FL-002 without FL-001 — inner join doesn't apply", resolution: "FL-002 needs standalone gap handling. Blocked on Moralis API check.", resolved: false }
    ],
    connections: ["Universe", "Join Key", "Point-in-Time", "Derived Feature"]
  },
  {
    id: "join-key",
    num: "03",
    name: "Join Key",
    question: "When a strategy asks for FL-001 + FL-002, how do the rows line up?",
    color: "#c9a0f5",
    status: "defined",
    statusLabel: "JK-001 defined",
    icon: "⊞",
    explain: "Two layers might have data at different timestamps or different speeds. The join key defines which fields match rows (coin + timestamp), what to do about gaps (inner join — drop incomplete rows), and how to handle resolution differences (forward-fill slower layers).",
    whyItMatters: "Without explicit join rules, combining layers is ambiguous. Does a missing holder snapshot at 1:10 mean the row gets dropped? Filled with the last known value? Set to null? Every choice produces different backtest results. The join key removes this ambiguity.",
    realWorld: 'Your strategy says "I want OHLCV + holders." At 1:10, FL-001 has a candle but FL-002 doesn\'t. Inner join rule: that row is dropped. Your strategy never sees incomplete data.',
    spec: [
      { key: "Join Key ID", val: "JK-001", type: "id" },
      { key: "Match rows by", val: "coin + timestamp", type: "highlight" },
      { key: "Missing data rule", val: "Inner join — row only exists when ALL requested layers have data", type: "highlight" },
      { key: "Row existence", val: "Row-level — a row counts if it exists, even if some fields are null", type: "normal" },
      { key: "Resolution mismatch", val: "Forward-fill slower layer to faster grid", type: "normal" },
      { key: "Staleness tracking", val: "{layer_id}_{short_name}_staleness_minutes (e.g. fl_001_ohlcv_staleness_minutes)", type: "highlight" },
    ],
    decisions: [
      { title: "Inner join", text: "Your strategy only sees rows where ALL requested data exists. No partial rows, no nulls to handle in strategy code." },
      { title: "Staleness field", text: "Forward-filled values get a minutes-since-last-observation field. Your strategy can decide how stale is too stale." },
      { title: "Row-level existence", text: "A candle with market_cap=null still counts as existing. Null fields within a row are the strategy's problem, not the join key's." }
    ],
    edgeCases: [
      { text: "Staleness naming convention?", resolution: "fl_001_ohlcv_staleness_minutes — both unique ID and readable name", resolved: true },
      { text: "Forward-fill before first observation?", resolution: "Quality constraint: all layers must start at or near T0. No cold start problem.", resolved: true },
      { text: "Sparse layer + inner join = shrunken dataset?", resolution: "Acknowledged, not applicable yet. All current layers share 5-min resolution.", resolved: true }
    ],
    connections: ["Universe", "Feature Layer"],
    sim: "staleness"
  },
  {
    id: "pit",
    num: "04",
    name: "Point-in-Time Semantics",
    question: "At 1:03, can my strategy see the 1:00–1:05 candle? Or must it wait until 1:05?",
    color: "#f06b63",
    status: "defined",
    statusLabel: "PIT-001 defined",
    icon: "⏱",
    explain: 'In live trading, you physically can\'t see a candle that hasn\'t closed — time enforces the rule for free. But in backtesting, ALL data sits in your database already. A 1:00–1:05 candle is right there. Nothing stops your code from reading it at simulated time 1:03. Point-in-time rules make your backtest behave like real time.',
    whyItMatters: "Look-ahead bias is the #1 reason backtests produce results that fail in live trading. Your strategy appears profitable because it was secretly using future data. PIT rules are the firewall against this. The knowledge time assumption holds for real-time market data feeds. If a future feature layer uses a source that publishes with delay or revises data retroactively, it will need its own PIT rule with an explicit knowledge time offset.",
    realWorld: "Your backtesting engine walks through time minute by minute. At simulated 1:03, it asks for the latest OHLCV. PIT rules say: 'the most recent COMPLETE candle is 12:55–1:00. The 1:00–1:05 candle is still being built.' Your strategy acts on the 12:55–1:00 candle — exactly what it would see in real life.",
    spec: [
      { key: "PIT ID", val: "PIT-001", type: "id" },
      { key: "Applies to", val: "FL-001 (OHLCV), FL-002 (Holders)", type: "normal" },
      { key: "Availability rule", val: "End-of-interval — candle for T to T+5min available at T+5min", type: "highlight" },
      { key: "Lag", val: "None (not meaningful at 5-minute resolution)", type: "normal" },
      { key: "Look-ahead protection", val: "Strategy can only see intervals that have fully closed", type: "good" },
      { key: "Knowledge time", val: "Equals as-of time — data is not revised or delayed after the interval closes", type: "highlight" },
    ],
    availabilityTypes: [
      { name: "End-of-interval", desc: "Available when interval closes", use: "Candles, snapshots", yours: true },
      { name: "Event-time", desc: "Available at exact moment it occurs", use: "Individual transactions", yours: false },
      { name: "Publication-time", desc: "Available when published, not measured", use: "Reports (not relevant for you)", yours: false },
    ],
    decisions: [
      { title: "End-of-interval", text: "A 1:00–1:05 candle becomes available at 1:05. This is the safest and most common rule for interval-based data." },
      { title: "No lag", text: "API processing delay exists in reality but is invisible at 5-minute resolution. Anything under 5 minutes rounds to the same candle." },
      { title: "Knowledge time = as-of time", text: "Once the interval closes, the data is immediately known. No revisions, no delayed publication. If a future data source revises values after first publish, it will need its own PIT rule." }
    ],
    edgeCases: [],
    connections: ["Feature Layer", "Derived Feature"],
    sim: "pit"
  },
  {
    id: "derived",
    num: "05",
    name: "Derived Feature",
    question: "What new numbers do we calculate from the raw data?",
    color: "#6db8f2",
    status: "tbd",
    statusLabel: "TBD — none defined yet",
    icon: "ƒ",
    explain: "Raw feature layers give you the building blocks: open, high, low, close, volume. But strategies often need computed values: moving averages, RSI, volume ratios, holder growth rate. These are derived — computed from raw layers, not stored from an API.",
    whyItMatters: "Without a spec, two strategies might both compute a '20-candle MA' differently — one uses close price, the other uses (high+low)/2. Both call it the same thing. Your backtest results become incomparable. The spec guarantees: one definition, one formula, one result.",
    realWorld: "You haven't defined any derived features yet — they'll emerge when you start writing strategies. If your first strategy needs 'average volume over the last 10 candles,' that becomes DF-001 with an explicit formula, source layer, and warm-up period.",
    spec: [
      { key: "Example name", val: "20-candle Simple Moving Average", type: "normal" },
      { key: "Source layer", val: "FL-001 (close price field)", type: "normal" },
      { key: "Formula", val: "mean(close[-20:])", type: "highlight" },
      { key: "Parameters", val: "window_size = 20 (configurable)", type: "normal" },
      { key: "Output field", val: "sma_20", type: "normal" },
      { key: "Warm-up period", val: "19 candles — no output until candle #20", type: "warn" },
    ],
    decisions: [
      { title: "Consistency", text: "One definition per derived feature. Every strategy that uses 'sma_20' gets the exact same calculation." },
      { title: "Warm-up period", text: "A 20-candle MA has no value for the first 19 rows. This is expected, not an error. The spec makes this explicit so strategy code handles it correctly." },
      { title: "Traceability", text: "You can always trace a derived value back to its source layer and formula. No black boxes." }
    ],
    edgeCases: [],
    connections: ["Feature Layer", "Point-in-Time", "Data Quality"],
    sim: "warmup"
  },
  {
    id: "quality",
    num: "06",
    name: "Data Quality Constraint",
    question: "What promises does this dataset make — and what breaks if they're violated?",
    color: "#f0a050",
    status: "defined",
    statusLabel: "DQ-001 to DQ-006",
    icon: "✓",
    explain: "Invariants — things that must always be true about your data. If violated, either the data is corrupt or the pipeline has a bug. Each constraint says what's guaranteed, how severe a violation is, and how to check it.",
    whyItMatters: "Corrupt data entering a backtest produces garbage results. A candle where high < low is physically impossible — if your strategy uses it, every calculation downstream is wrong. Quality constraints are the last gate before data reaches your strategy.",
    realWorld: "Your OHLCVData model already enforces DQ-001 via unique_together. The other constraints catch things the database can't enforce structurally — like price relationships within a candle.",
    constraints: [
      { id: "DQ-001", scope: "FL-001", rule: "No duplicate rows per coin per timestamp", severity: "reject", method: "unique_together DB index" },
      { id: "DQ-002", scope: "FL-001", rule: "high_price >= low_price", severity: "reject", method: "Row-level check" },
      { id: "DQ-003", scope: "FL-001", rule: "open and close between low and high", severity: "reject", method: "Row-level check" },
      { id: "DQ-004", scope: "FL-001", rule: "volume >= 0", severity: "reject", method: "Row-level check" },
      { id: "DQ-005", scope: "FL-001", rule: "Timestamp within observation window (T0 to T0+5000m)", severity: "reject", method: "Range check vs graduated_at" },
      { id: "DQ-006", scope: "All layers", rule: "First observation at or near T0", severity: "reject", method: "Compare first row to anchor" },
    ],
    decisions: [
      { title: "Sparse ≠ corrupt", text: "A coin with 12 candles out of 1000 possible is NOT a quality violation. The coin just died. This is normal — nearly all pump.fun tokens die within days." },
      { title: "All hard rejects", text: "Every current constraint is hard reject. No warnings yet. If a row violates any constraint, it cannot enter a backtest." }
    ],
    edgeCases: [],
    connections: ["Universe", "Feature Layer", "Derived Feature"],
    sim: "quality"
  },
  {
    id: "reference",
    num: "07",
    name: "Reference Dataset",
    question: "What about data that doesn't fit into fixed time intervals?",
    color: "#4dd66a",
    status: "planned",
    statusLabel: "RD-001 planned",
    icon: "⟴",
    explain: "Feature layers produce one row per fixed interval (every 5 minutes). But individual transactions happen at random times — 3 in one second, then nothing for 2 minutes. This data is too granular for the time grid, so it lives separately.",
    whyItMatters: "Some strategy hypotheses need individual trade detail: 'Did a whale buy before or after the price spike?' You can't answer that from a 5-minute aggregated candle. Reference datasets let strategies drill into the raw events when needed.",
    realWorld: "A strategy running on the 5-min OHLCV grid sees a volume spike at 1:15. It queries RD-001: 'show me all trades for this coin between 1:10 and 1:20.' It discovers one wallet bought 500 SOL worth in a single transaction. That's the signal — you can't get that from the candle alone.",
    spec: [
      { key: "Reference ID", val: "RD-001", type: "id" },
      { key: "Each row is", val: "One single trade (buy or sell)", type: "normal" },
      { key: "Timestamp", val: "Exact second of the trade, not a 5-min bucket", type: "highlight" },
      { key: "Access pattern", val: '"Get all trades for coin X between T1 and T2"', type: "highlight" },
      { key: "Auto-joined?", val: "No — strategy must explicitly request it", type: "warn" },
      { key: "Feature set", val: "TBD", type: "normal" },
      { key: "Data source", val: "TBD", type: "normal" },
    ],
    decisions: [
      { title: "Not a feature layer", text: "Transactions are event-based with unpredictable frequency. They can't be forced into fixed intervals without losing information." },
      { title: "On-demand access", text: "Strategies query reference data when they need it. It's not merged into every row — that would explode the dataset size." },
      { title: "Future FL-003", text: "Aggregated summaries (tx_count, buy_volume per 5-min bucket) may become a feature layer later. Raw events stay as a reference dataset." }
    ],
    edgeCases: [],
    connections: ["Universe"]
  },
];

// ─── Components ──────────────────────────────────────────────────────────────

function Badge({ status, label }) {
  const styles = {
    defined: { bg: "rgba(61,186,82,0.08)", border: "rgba(61,186,82,0.2)", color: "#3dba52" },
    partial: { bg: "rgba(201,160,245,0.08)", border: "rgba(201,160,245,0.2)", color: "#c9a0f5" },
    tbd: { bg: "rgba(130,140,155,0.08)", border: "rgba(130,140,155,0.2)", color: "#828c9b" },
    planned: { bg: "rgba(255,166,87,0.08)", border: "rgba(255,166,87,0.2)", color: "#ffa657" },
    blocked: { bg: "rgba(240,107,99,0.08)", border: "rgba(240,107,99,0.2)", color: "#f06b63" },
    reject: { bg: "rgba(240,107,99,0.08)", border: "rgba(240,107,99,0.15)", color: "#f06b63" },
  };
  const s = styles[status] || styles.tbd;
  return (
    <span style={{
      fontSize: 9.5, fontFamily: "mono", padding: "2px 7px", borderRadius: 4,
      background: s.bg, border: `1px solid ${s.border}`, color: s.color, whiteSpace: "nowrap",
      letterSpacing: "0.01em"
    }}>{label}</span>
  );
}

function SectionLabel({ children, color }) {
  return (
    <div style={{
      fontSize: 9, fontFamily: "mono", color: color || "#484f58",
      textTransform: "uppercase", letterSpacing: "0.06em",
      marginBottom: 6, marginTop: 14
    }}>{children}</div>
  );
}

function SpecRow({ item }) {
  const colorMap = { id: "#484f58", normal: "#c9d1d9", highlight: "#58a6ff", good: "#3dba52", warn: "#ffa657" };
  return (
    <div style={{ display: "flex", gap: 10, padding: "3px 0", fontSize: 12 }}>
      <span style={{ color: "#6e7681", minWidth: 140, fontFamily: "mono", fontSize: 11, flexShrink: 0 }}>{item.key}</span>
      <span style={{ color: colorMap[item.type] || "#c9d1d9", lineHeight: 1.5 }}>{item.val}</span>
    </div>
  );
}

// ─── Simulators ──────────────────────────────────────────────────────────────

function StalenessSimulator() {
  const [thresh, setThresh] = useState(20);
  const rows = [
    { t: "1:00", close: "1.23", liq: "100 SOL", s: 0, real: true },
    { t: "1:05", close: "1.25", liq: "100 SOL", s: 5, real: false },
    { t: "1:10", close: "1.28", liq: "100 SOL", s: 10, real: false },
    { t: "1:15", close: "1.24", liq: "100 SOL", s: 15, real: false },
    { t: "1:20", close: "1.30", liq: "100 SOL", s: 20, real: false },
    { t: "1:25", close: "1.32", liq: "100 SOL", s: 25, real: false },
    { t: "1:30", close: "1.29", liq: "100 SOL", s: 30, real: false },
    { t: "1:35", close: "1.27", liq: "100 SOL", s: 35, real: false },
    { t: "1:40", close: "1.31", liq: "100 SOL", s: 40, real: false },
    { t: "1:55", close: "1.35", liq: "100 SOL", s: 55, real: false },
    { t: "2:00", close: "1.40", liq: "120 SOL", s: 0, real: true },
  ];
  return (
    <SimBox title="Staleness Simulator" subtitle="5-min OHLCV + 60-min liquidity layer (forward-filled)">
      <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 8 }}>
        <span style={{ fontSize: 11, color: "#8b949e" }}>I trust data up to:</span>
        <input type="range" min={5} max={55} step={5} value={thresh}
          onChange={e => setThresh(+e.target.value)}
          style={{ flex: 1, accentColor: "#c9a0f5", height: 4 }} />
        <span style={{ fontSize: 12, fontFamily: "mono", color: "#c9a0f5", fontWeight: 600, minWidth: 36 }}>≤{thresh}m</span>
      </div>
      <div style={{ overflowX: "auto" }}>
        <table style={{ width: "100%", borderCollapse: "collapse", fontFamily: "mono", fontSize: 11 }}>
          <thead><tr style={{ borderBottom: "1px solid #1a1f2b" }}>
            {["Time", "Close", "Liquidity", "Staleness", "Trusted?"].map(h =>
              <th key={h} style={{ textAlign: "left", padding: "5px 6px", color: "#484f58", fontWeight: 500, fontSize: 9.5 }}>{h}</th>
            )}
          </tr></thead>
          <tbody>{rows.map((r, i) => {
            const ok = r.s <= thresh;
            return (
              <tr key={i} style={{ borderBottom: "1px solid rgba(26,31,43,0.4)", transition: "opacity 0.3s", opacity: ok ? 1 : 0.3 }}>
                <td style={{ padding: "4px 6px", color: "#8b949e" }}>{r.t}</td>
                <td style={{ padding: "4px 6px", color: "#c9d1d9" }}>${r.close}</td>
                <td style={{ padding: "4px 6px" }}>
                  <span style={{ color: r.real ? "#3dba52" : "#c9d1d9" }}>{r.liq}</span>
                  {r.real && <span style={{ color: "#3dba52", fontSize: 8, marginLeft: 4 }}>● actual</span>}
                </td>
                <td style={{ padding: "4px 6px", color: r.s === 0 ? "#3dba52" : r.s > thresh ? "#f06b63" : "#ffa657" }}>{r.s}m</td>
                <td style={{ padding: "4px 6px", color: ok ? "#3dba52" : "#f06b63", fontWeight: 500 }}>{ok ? "✓ yes" : "✗ no"}</td>
              </tr>
            );
          })}</tbody>
        </table>
      </div>
      <SimNote>Drag the slider — rows fade out when their staleness exceeds your threshold. The 1:00 and 2:00 rows are actual observations (staleness = 0). Everything between is forward-filled from 1:00.</SimNote>
    </SimBox>
  );
}

function PITSimulator() {
  const [t, setT] = useState(1);
  const times = ["1:00", "1:03", "1:05", "1:08"];
  const candles = [
    { label: "12:55 – 1:00", interval: "closed" },
    { label: "1:00 – 1:05", interval: "building" },
    { label: "1:05 – 1:10", interval: "future" },
  ];
  const visible = (ci) => {
    if (ci === 0) return true;
    if (ci === 1) return t >= 2;
    if (ci === 2) return t >= 3;
    return false;
  };
  const colors = { closed: "#3dba52", building: "#ffa657", future: "#2a2f3a" };
  return (
    <SimBox title="Point-in-Time Simulator" subtitle="Click a time to see what your strategy can see">
      <div style={{ display: "flex", gap: 4, marginBottom: 12, flexWrap: "wrap" }}>
        {times.map((label, i) => (
          <button key={i} onClick={() => setT(i)} style={{
            fontSize: 11, fontFamily: "mono", padding: "5px 14px", borderRadius: 5, cursor: "pointer",
            border: t === i ? "1px solid #f06b63" : "1px solid #1a1f2b",
            background: t === i ? "rgba(240,107,99,0.08)" : "transparent",
            color: t === i ? "#f06b63" : "#6e7681", transition: "all 0.2s", fontWeight: t === i ? 600 : 400
          }}>@ {label}</button>
        ))}
      </div>
      <div style={{ display: "grid", gap: 6 }}>
        {candles.map((c, i) => {
          const v = visible(i);
          return (
            <div key={i} style={{
              display: "flex", alignItems: "center", gap: 10, padding: "10px 14px",
              background: v ? "rgba(61,186,82,0.04)" : "rgba(42,47,58,0.15)",
              border: `1px solid ${v ? "rgba(61,186,82,0.15)" : "rgba(42,47,58,0.2)"}`,
              borderRadius: 6, transition: "all 0.3s"
            }}>
              <div style={{
                width: 8, height: 8, borderRadius: "50%",
                background: colors[c.interval], flexShrink: 0
              }} />
              <span style={{ fontFamily: "mono", fontSize: 12, color: "#c9d1d9", flex: 1 }}>{c.label}</span>
              <span style={{ fontSize: 10, color: colors[c.interval], fontFamily: "mono" }}>{c.interval}</span>
              <span style={{ fontFamily: "mono", fontSize: 11, fontWeight: 500, color: v ? "#3dba52" : "#f06b63" }}>
                {v ? "✓ visible" : "✗ hidden"}
              </span>
            </div>
          );
        })}
      </div>
      <div style={{
        marginTop: 10, padding: "8px 12px", borderRadius: 6, fontSize: 11, lineHeight: 1.6,
        background: "rgba(240,107,99,0.03)", border: "1px solid rgba(240,107,99,0.08)", color: "#8b949e"
      }}>
        {t === 0 && "At 1:00, the 12:55–1:00 candle just closed — your strategy can see it. The 1:00–1:05 candle is starting but has zero data yet."}
        {t === 1 && "At 1:03, the 1:00–1:05 candle is being built (trades from 1:03–1:05 haven't happened). Your strategy can NOT see it. This is look-ahead bias prevention in action."}
        {t === 2 && "At 1:05, the 1:00–1:05 candle just closed — now your strategy can see it. Two complete candles are available."}
        {t === 3 && "At 1:08, both closed candles are visible. The 1:05–1:10 candle is still building (trades from 1:08–1:10 haven't happened yet)."}
      </div>
    </SimBox>
  );
}

function WarmUpSimulator() {
  const [win, setWin] = useState(5);
  const prices = [1.20, 1.50, 1.30, 1.80, 1.10, 1.40, 1.60, 1.20, 1.90, 1.70, 1.50, 1.30];
  return (
    <SimBox title="Warm-Up Period Demo" subtitle="See how window size affects when a derived feature starts producing values">
      <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 8 }}>
        <span style={{ fontSize: 11, color: "#8b949e" }}>MA window:</span>
        <input type="range" min={2} max={10} value={win} onChange={e => setWin(+e.target.value)}
          style={{ flex: 1, accentColor: "#6db8f2", height: 4 }} />
        <span style={{ fontSize: 12, fontFamily: "mono", color: "#6db8f2", fontWeight: 600, minWidth: 16 }}>{win}</span>
      </div>
      <div style={{ overflowX: "auto" }}>
        <table style={{ width: "100%", borderCollapse: "collapse", fontFamily: "mono", fontSize: 11 }}>
          <thead><tr style={{ borderBottom: "1px solid #1a1f2b" }}>
            {["#", "Close", `${win}-candle MA`, "Status"].map(h =>
              <th key={h} style={{ textAlign: "left", padding: "5px 6px", color: "#484f58", fontWeight: 500, fontSize: 9.5 }}>{h}</th>
            )}
          </tr></thead>
          <tbody>{prices.map((p, i) => {
            const warm = i < win - 1;
            const ma = warm ? null : (prices.slice(i - win + 1, i + 1).reduce((a, b) => a + b) / win).toFixed(3);
            return (
              <tr key={i} style={{ borderBottom: "1px solid rgba(26,31,43,0.4)", transition: "opacity 0.3s", opacity: warm ? 0.45 : 1 }}>
                <td style={{ padding: "4px 6px", color: "#6e7681" }}>#{i + 1}</td>
                <td style={{ padding: "4px 6px", color: "#c9d1d9" }}>${p.toFixed(2)}</td>
                <td style={{ padding: "4px 6px", color: warm ? "#484f58" : "#6db8f2", fontWeight: warm ? 400 : 500 }}>{warm ? "—" : `$${ma}`}</td>
                <td style={{ padding: "4px 6px" }}>
                  {warm
                    ? <span style={{ color: "#ffa657", fontSize: 10 }}>warm-up ({i + 1}/{win})</span>
                    : <span style={{ color: "#3dba52", fontSize: 10 }}>✓ valid</span>}
                </td>
              </tr>
            );
          })}</tbody>
        </table>
      </div>
      <SimNote>Larger window = more warm-up rows with no output. This is expected behavior — the spec documents it so strategies don't treat missing values as errors.</SimNote>
    </SimBox>
  );
}

function QualitySimulator() {
  const [rows] = useState([
    { coin: "BONK", time: "1:05", o: 1.2, h: 1.5, l: 1.0, c: 1.3, v: 500, violations: [] },
    { coin: "BONK", time: "1:10", o: 1.3, h: 0.9, l: 1.1, c: 1.2, v: 300, violations: ["DQ-002: high (0.9) < low (1.1)"] },
    { coin: "DOGE", time: "1:05", o: 2.0, h: 2.5, l: 1.8, c: 2.7, v: 800, violations: ["DQ-003: close (2.7) > high (2.5)"] },
    { coin: "PEPE", time: "1:15", o: 0.5, h: 0.8, l: 0.3, c: 0.6, v: -10, violations: ["DQ-004: volume (-10) < 0"] },
    { coin: "SHIB", time: "1:20", o: 3.0, h: 3.5, l: 2.8, c: 3.2, v: 1200, violations: [] },
  ]);
  return (
    <SimBox title="Quality Gate Checker" subtitle="See which candles pass or fail the 6 quality constraints">
      <div style={{ overflowX: "auto" }}>
        <table style={{ width: "100%", borderCollapse: "collapse", fontFamily: "mono", fontSize: 11 }}>
          <thead><tr style={{ borderBottom: "1px solid #1a1f2b" }}>
            {["Coin", "Time", "O", "H", "L", "C", "Vol", "Result"].map(h =>
              <th key={h} style={{ textAlign: "left", padding: "5px 6px", color: "#484f58", fontWeight: 500, fontSize: 9.5 }}>{h}</th>
            )}
          </tr></thead>
          <tbody>{rows.map((r, i) => {
            const ok = r.violations.length === 0;
            return (
              <tr key={i} style={{
                borderBottom: "1px solid rgba(26,31,43,0.4)",
                background: ok ? "transparent" : "rgba(240,107,99,0.03)"
              }}>
                <td style={{ padding: "4px 6px", color: "#8b949e" }}>{r.coin}</td>
                <td style={{ padding: "4px 6px", color: "#8b949e" }}>{r.time}</td>
                <td style={{ padding: "4px 6px", color: "#c9d1d9" }}>{r.o}</td>
                <td style={{ padding: "4px 6px", color: !ok && r.violations[0]?.includes("DQ-002") ? "#f06b63" : "#c9d1d9" }}>{r.h}</td>
                <td style={{ padding: "4px 6px", color: !ok && r.violations[0]?.includes("DQ-002") ? "#f06b63" : "#c9d1d9" }}>{r.l}</td>
                <td style={{ padding: "4px 6px", color: !ok && r.violations[0]?.includes("DQ-003") ? "#f06b63" : "#c9d1d9" }}>{r.c}</td>
                <td style={{ padding: "4px 6px", color: !ok && r.violations[0]?.includes("DQ-004") ? "#f06b63" : "#c9d1d9" }}>{r.v}</td>
                <td style={{ padding: "4px 6px" }}>
                  {ok
                    ? <span style={{ color: "#3dba52", fontWeight: 500 }}>✓ PASS</span>
                    : <span style={{ color: "#f06b63", fontWeight: 500 }}>✗ REJECT</span>}
                </td>
              </tr>
            );
          })}</tbody>
        </table>
      </div>
      <div style={{ marginTop: 6, display: "grid", gap: 3 }}>
        {rows.filter(r => r.violations.length > 0).map((r, i) =>
          r.violations.map((v, j) => (
            <div key={`${i}-${j}`} style={{
              fontSize: 10, color: "#f06b63", padding: "4px 8px", borderRadius: 4,
              background: "rgba(240,107,99,0.04)", border: "1px solid rgba(240,107,99,0.08)",
              fontFamily: "mono"
            }}>
              {r.coin} @ {r.time}: {v}
            </div>
          ))
        )}
      </div>
      <SimNote>Rows 2, 3, and 4 have impossible values. The quality gate catches them before they enter your backtest. Row 1 and 5 pass all checks.</SimNote>
    </SimBox>
  );
}

function SimBox({ title, subtitle, children }) {
  return (
    <div style={{
      marginTop: 12, padding: 14, background: "#080b12",
      borderRadius: 8, border: "1px solid #1a1f2b"
    }}>
      <div style={{ fontSize: 12, fontWeight: 600, color: "#e2e8f0", marginBottom: 2 }}>{title}</div>
      <div style={{ fontSize: 10, color: "#484f58", fontFamily: "mono", marginBottom: 10 }}>{subtitle}</div>
      {children}
    </div>
  );
}

function SimNote({ children }) {
  return (
    <div style={{ fontSize: 10, color: "#6e7681", marginTop: 8, lineHeight: 1.5 }}>{children}</div>
  );
}

// ─── Flow Arrow ──────────────────────────────────────────────────────────────

function FlowArrow({ label }) {
  return (
    <div style={{ display: "flex", flexDirection: "column", alignItems: "center", padding: "3px 0" }}>
      <div style={{ width: 1, height: 14, background: "linear-gradient(to bottom, rgba(88,166,255,0.18), rgba(88,166,255,0.05))" }} />
      <svg width="10" height="6" viewBox="0 0 10 6" style={{ marginTop: -1 }}>
        <path d="M0 0 L5 6 L10 0" fill="none" stroke="rgba(88,166,255,0.2)" strokeWidth="1.2" />
      </svg>
      <span style={{ fontSize: 8.5, color: "#353b48", fontFamily: "mono", marginTop: 1, letterSpacing: "0.03em" }}>{label}</span>
    </div>
  );
}

// ─── Main Card ───────────────────────────────────────────────────────────────

function Card({ concept, isOpen, onToggle }) {
  return (
    <div style={{
      background: "#0c1019",
      border: `1px solid ${isOpen ? concept.color + "30" : "#151a24"}`,
      borderRadius: 10, overflow: "hidden", transition: "all 0.3s"
    }}>
      {/* Header */}
      <div onClick={onToggle} style={{
        display: "flex", alignItems: "center", gap: 10, padding: "12px 14px",
        cursor: "pointer", userSelect: "none", borderLeft: `3px solid ${concept.color}`
      }}>
        <span style={{ fontSize: 20, fontWeight: 700, color: concept.color, opacity: 0.18, fontFamily: "mono", minWidth: 26 }}>{concept.num}</span>
        <span style={{ fontSize: 16, color: concept.color, opacity: 0.5, minWidth: 18 }}>{concept.icon}</span>
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
            <span style={{ fontSize: 13.5, fontWeight: 600, color: "#e2e8f0" }}>{concept.name}</span>
            <Badge status={concept.status} label={concept.statusLabel} />
          </div>
          <div style={{ fontSize: 11.5, color: "#58a6ff", marginTop: 2, fontStyle: "italic", lineHeight: 1.4 }}>{concept.question}</div>
        </div>
        <svg width="14" height="14" viewBox="0 0 14 14"
          style={{ transform: isOpen ? "rotate(180deg)" : "none", transition: "transform 0.25s", flexShrink: 0 }}>
          <path d="M3 5 L7 9 L11 5" fill="none" stroke="#484f58" strokeWidth="1.5" />
        </svg>
      </div>

      {/* Body */}
      {isOpen && (
        <div style={{ padding: "0 14px 16px 54px", animation: "fadeSlide 0.25s ease" }}>
          {/* Explanation */}
          <p style={{ fontSize: 12.5, color: "#8b949e", lineHeight: 1.65, margin: "0 0 0 0" }}>{concept.explain}</p>

          {/* Why it matters */}
          {concept.whyItMatters && (
            <>
              <SectionLabel color="#58a6ff">Why this matters</SectionLabel>
              <p style={{ fontSize: 12, color: "#6e7681", lineHeight: 1.6, margin: 0 }}>{concept.whyItMatters}</p>
            </>
          )}

          {/* Real world */}
          {concept.realWorld && (
            <>
              <SectionLabel color="#3dba52">In your system</SectionLabel>
              <div style={{
                fontSize: 12, color: "#8b949e", lineHeight: 1.6,
                padding: "8px 12px", borderRadius: 6,
                background: "rgba(61,186,82,0.03)", border: "1px solid rgba(61,186,82,0.08)"
              }}>{concept.realWorld}</div>
            </>
          )}

          {/* Spec values */}
          {concept.spec && (
            <>
              <SectionLabel>Specification</SectionLabel>
              <div style={{ padding: "8px 10px", background: "#080b12", borderRadius: 6, border: "1px solid #1a1f2b" }}>
                {concept.spec.map((item, i) => <SpecRow key={i} item={item} />)}
              </div>
            </>
          )}

          {/* Feature layers */}
          {concept.layers && (
            <>
              <SectionLabel>Layers</SectionLabel>
              <div style={{ display: "grid", gap: 8 }}>
                {concept.layers.map((layer, li) => (
                  <div key={li} style={{ padding: 10, background: "#080b12", borderRadius: 6, border: "1px solid #1a1f2b" }}>
                    <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 8 }}>
                      <span style={{ fontSize: 12, fontWeight: 600, color: "#c9d1d9" }}>{layer.id}: {layer.name}</span>
                      <Badge status={layer.status} label={layer.status} />
                    </div>
                    <div style={{ fontSize: 10, color: "#484f58", fontFamily: "mono", marginBottom: 6 }}>FIELDS</div>
                    <div style={{ display: "flex", flexWrap: "wrap", gap: 4, marginBottom: 8 }}>
                      {layer.fields.map((f, fi) => (
                        <div key={fi} style={{
                          fontSize: 10, padding: "3px 8px", borderRadius: 4,
                          background: "rgba(88,166,255,0.04)", border: "1px solid rgba(88,166,255,0.08)",
                          color: "#8b949e"
                        }}>
                          <span style={{ color: "#58a6ff" }}>{f.name}</span> — {f.desc}
                        </div>
                      ))}
                    </div>
                    <div style={{ display: "grid", gap: 2, fontSize: 11 }}>
                      {[
                        ["Resolution", layer.resolution],
                        ["Gap rule", layer.gap],
                        ["Source", layer.source],
                        ["Refresh", layer.refresh]
                      ].map(([k, v], ri) => (
                        <div key={ri} style={{ display: "flex", gap: 8 }}>
                          <span style={{ color: "#6e7681", minWidth: 80, fontFamily: "mono", fontSize: 10 }}>{k}</span>
                          <span style={{ color: k === "Gap rule" && layer.status === "blocked" ? "#ffa657" : "#c9d1d9" }}>{v}</span>
                        </div>
                      ))}
                    </div>
                    {layer.gapExplain && (
                      <div style={{ fontSize: 10.5, color: "#6e7681", marginTop: 6, fontStyle: "italic", lineHeight: 1.5 }}>{layer.gapExplain}</div>
                    )}
                  </div>
                ))}
              </div>
            </>
          )}

          {/* Availability types (PIT) */}
          {concept.availabilityTypes && (
            <>
              <SectionLabel>Availability rule types</SectionLabel>
              <div style={{ display: "grid", gap: 4 }}>
                {concept.availabilityTypes.map((at, i) => (
                  <div key={i} style={{
                    display: "flex", alignItems: "center", gap: 10, padding: "6px 10px",
                    background: at.yours ? "rgba(240,107,99,0.03)" : "transparent",
                    border: `1px solid ${at.yours ? "rgba(240,107,99,0.1)" : "#1a1f2b"}`,
                    borderRadius: 5, fontSize: 11
                  }}>
                    <span style={{ fontFamily: "mono", fontSize: 10.5, color: at.yours ? "#f06b63" : "#6e7681", minWidth: 110, fontWeight: at.yours ? 600 : 400 }}>{at.name}</span>
                    <span style={{ color: "#8b949e", flex: 1 }}>{at.desc}</span>
                    <span style={{ color: "#484f58", fontSize: 10, fontFamily: "mono" }}>{at.use}</span>
                    {at.yours && <Badge status="defined" label="YOUR RULE" />}
                  </div>
                ))}
              </div>
            </>
          )}

          {/* Quality constraints */}
          {concept.constraints && (
            <>
              <SectionLabel>Constraints</SectionLabel>
              <div style={{ display: "grid", gap: 4 }}>
                {concept.constraints.map((c, i) => (
                  <div key={i} style={{
                    display: "flex", alignItems: "center", gap: 6, padding: "5px 10px",
                    background: "#080b12", border: "1px solid #1a1f2b", borderRadius: 5, fontSize: 11
                  }}>
                    <span style={{ fontFamily: "mono", fontSize: 9.5, color: "#484f58", minWidth: 46 }}>{c.id}</span>
                    <span style={{ fontFamily: "mono", fontSize: 9, color: "#6e7681", minWidth: 46 }}>{c.scope}</span>
                    <span style={{ color: "#c9d1d9", flex: 1 }}>{c.rule}</span>
                    <Badge status="reject" label="HARD REJECT" />
                  </div>
                ))}
              </div>
            </>
          )}

          {/* Simulators */}
          {concept.sim === "staleness" && <StalenessSimulator />}
          {concept.sim === "pit" && <PITSimulator />}
          {concept.sim === "warmup" && <WarmUpSimulator />}
          {concept.sim === "quality" && <QualitySimulator />}

          {/* Decisions */}
          {concept.decisions && concept.decisions.length > 0 && (
            <>
              <SectionLabel color={concept.color}>Key decisions</SectionLabel>
              <div style={{ display: "grid", gap: 4 }}>
                {concept.decisions.map((d, i) => (
                  <div key={i} style={{
                    padding: "7px 10px", borderRadius: 5,
                    background: `${concept.color}05`, border: `1px solid ${concept.color}12`
                  }}>
                    <span style={{ fontSize: 11, fontWeight: 600, color: "#c9d1d9" }}>{d.title}: </span>
                    <span style={{ fontSize: 11, color: "#8b949e", lineHeight: 1.5 }}>{d.text}</span>
                  </div>
                ))}
              </div>
            </>
          )}

          {/* Edge cases */}
          {concept.edgeCases && concept.edgeCases.length > 0 && (
            <>
              <SectionLabel color="#ffa657">Edge cases</SectionLabel>
              <div style={{ display: "grid", gap: 4 }}>
                {concept.edgeCases.map((ec, i) => (
                  <div key={i} style={{
                    padding: "6px 10px", borderRadius: 5, fontSize: 11,
                    borderLeft: `2px solid ${ec.resolved ? "rgba(61,186,82,0.3)" : "rgba(255,166,87,0.3)"}`,
                    background: ec.resolved ? "rgba(61,186,82,0.02)" : "rgba(255,166,87,0.02)"
                  }}>
                    <div style={{ color: "#8b949e" }}>{ec.text}</div>
                    <div style={{ color: ec.resolved ? "#3dba52" : "#ffa657", fontSize: 10, marginTop: 2, fontFamily: "mono" }}>
                      {ec.resolved ? "✓ " : "⏳ "}{ec.resolution}
                    </div>
                  </div>
                ))}
              </div>
            </>
          )}

          {/* Connections */}
          {concept.connections && (
            <div style={{ display: "flex", gap: 4, flexWrap: "wrap", marginTop: 12 }}>
              <span style={{ fontSize: 9, color: "#484f58", fontFamily: "mono", alignSelf: "center" }}>CONNECTS TO</span>
              {concept.connections.map((c, i) => (
                <span key={i} style={{
                  fontSize: 9.5, fontFamily: "mono", padding: "2px 7px", borderRadius: 3,
                  background: "rgba(88,166,255,0.04)", border: "1px solid rgba(88,166,255,0.1)", color: "#58a6ff"
                }}>{c}</span>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

// ─── App ─────────────────────────────────────────────────────────────────────

export default function DataSpecExplorer() {
  const [openId, setOpenId] = useState(null);
  const [allOpen, setAllOpen] = useState(false);
  const [search, setSearch] = useState("");

  const filtered = search
    ? CONCEPTS.filter(c =>
        c.name.toLowerCase().includes(search.toLowerCase()) ||
        c.question.toLowerCase().includes(search.toLowerCase()) ||
        c.explain.toLowerCase().includes(search.toLowerCase())
      )
    : CONCEPTS;

  const stats = { defined: 0, partial: 0, tbd: 0 };
  CONCEPTS.forEach(c => {
    if (c.status === "defined") stats.defined++;
    else if (c.status === "tbd") stats.tbd++;
    else stats.partial++;
  });

  return (
    <div style={{ minHeight: "100vh", background: "#070a10", fontFamily: "'DM Sans', -apple-system, sans-serif", color: "#c9d1d9" }}>
      <style>{`
        @import url('https://fonts.googleapis.com/css2?family=DM+Sans:ital,wght@0,400;0,500;0,600;0,700;1,400&family=JetBrains+Mono:wght@400;500;600&display=swap');
        @keyframes fadeSlide { from { opacity: 0; transform: translateY(-6px); } to { opacity: 1; transform: translateY(0); } }
        * { box-sizing: border-box; margin: 0; }
        ::-webkit-scrollbar { width: 3px; height: 3px; }
        ::-webkit-scrollbar-track { background: transparent; }
        ::-webkit-scrollbar-thumb { background: #21262d; border-radius: 3px; }
        input[type=range] { -webkit-appearance: none; background: #1a1f2b; border-radius: 4px; outline: none; }
        input[type=range]::-webkit-slider-thumb { -webkit-appearance: none; width: 14px; height: 14px; border-radius: 50%; cursor: pointer; }
      `}</style>

      <div style={{ maxWidth: 720, margin: "0 auto", padding: "32px 16px 60px" }}>

        {/* Header */}
        <div style={{ textAlign: "center", marginBottom: 28 }}>
          <div style={{ fontSize: 10, fontFamily: "mono", color: "#353b48", letterSpacing: "0.15em", marginBottom: 6 }}>QUANTITATIVE TRADING PARADIGM</div>
          <h1 style={{ fontSize: 22, fontWeight: 700, color: "#e2e8f0", letterSpacing: "-0.02em" }}>Data Specification Explorer</h1>
          <p style={{ fontSize: 12.5, color: "#6e7681", marginTop: 8, maxWidth: 520, marginLeft: "auto", marginRight: "auto", lineHeight: 1.6 }}>
            7 concepts that fully describe a dataset for backtesting. Each one answers a different question.
            Expand any concept to learn what it is, why it matters, and see interactive examples.
          </p>
        </div>

        {/* Stats */}
        <div style={{ display: "flex", gap: 8, justifyContent: "center", marginBottom: 20 }}>
          {[
            { n: stats.defined, label: "Defined", color: "#3dba52" },
            { n: stats.partial, label: "Partial / Planned", color: "#c9a0f5" },
            { n: stats.tbd, label: "TBD", color: "#828c9b" },
          ].map((s, i) => (
            <div key={i} style={{
              padding: "10px 20px", borderRadius: 8, background: "#0c1019",
              border: "1px solid #151a24", textAlign: "center", minWidth: 80
            }}>
              <div style={{ fontSize: 22, fontWeight: 700, color: s.color, fontFamily: "mono" }}>{s.n}</div>
              <div style={{ fontSize: 10, color: "#6e7681", marginTop: 2 }}>{s.label}</div>
            </div>
          ))}
        </div>

        {/* Controls */}
        <div style={{ display: "flex", gap: 6, justifyContent: "center", marginBottom: 18, flexWrap: "wrap" }}>
          <input
            type="text" placeholder="Search concepts..." value={search}
            onChange={e => setSearch(e.target.value)}
            style={{
              fontSize: 11, fontFamily: "mono", padding: "5px 12px", borderRadius: 5, width: 180,
              background: "#0c1019", border: "1px solid #151a24", color: "#c9d1d9", outline: "none"
            }}
          />
          <button onClick={() => { setAllOpen(!allOpen); setOpenId(null); }} style={{
            fontSize: 10, fontFamily: "mono", padding: "5px 14px", borderRadius: 5, cursor: "pointer",
            background: allOpen ? "rgba(88,166,255,0.06)" : "transparent",
            border: `1px solid ${allOpen ? "rgba(88,166,255,0.2)" : "#151a24"}`,
            color: allOpen ? "#58a6ff" : "#6e7681", transition: "all 0.2s"
          }}>{allOpen ? "Collapse all" : "Expand all"}</button>
        </div>

        {/* Concept cards */}
        <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
          {filtered.map((concept, i) => (
            <div key={concept.id}>
              <Card
                concept={concept}
                isOpen={allOpen || openId === concept.id}
                onToggle={() => { if (!allOpen) setOpenId(openId === concept.id ? null : concept.id); }}
              />
              {i < filtered.length - 1 && <FlowArrow label={FLOW_LABELS[i] || ""} />}
            </div>
          ))}
        </div>

        {/* Connections map */}
        <div style={{
          marginTop: 28, padding: 18, background: "#0c1019",
          border: "1px solid #151a24", borderRadius: 10
        }}>
          <div style={{ fontSize: 13, fontWeight: 600, color: "#e2e8f0", marginBottom: 12 }}>How concepts connect</div>
          {[
            { from: "Universe", to: "Feature Layer", why: "Layers measure things within a universe's scope" },
            { from: "Feature Layer", to: "Join Key", why: "Multiple layers need alignment rules to combine" },
            { from: "Feature Layer", to: "Point-in-Time", why: "Each layer needs a rule for when data becomes 'known'" },
            { from: "Feature Layer", to: "Derived Feature", why: "Derived features are computed from raw layer data" },
            { from: "Point-in-Time", to: "Derived Feature", why: "Derived features inherit PIT rules from their source" },
            { from: "All concepts", to: "Data Quality", why: "Quality constraints can apply to any concept" },
            { from: "Universe", to: "Reference Dataset", why: "Reference data covers same coins and time window" },
          ].map((row, i) => (
            <div key={i} style={{ display: "flex", alignItems: "center", gap: 6, padding: "4px 0", fontSize: 11 }}>
              <span style={{ fontFamily: "mono", fontSize: 10, color: "#58a6ff", minWidth: 105, textAlign: "right" }}>{row.from}</span>
              <span style={{ color: "#2a2f3a", fontSize: 11 }}>→</span>
              <span style={{ fontFamily: "mono", fontSize: 10, color: "#58a6ff", minWidth: 105 }}>{row.to}</span>
              <span style={{ color: "#484f58", fontSize: 10, flex: 1 }}>{row.why}</span>
            </div>
          ))}
        </div>

        {/* Blocked */}
        <div style={{
          marginTop: 14, padding: "12px 16px", background: "#0c1019",
          border: "1px solid rgba(240,107,99,0.1)", borderRadius: 8
        }}>
          <div style={{ display: "flex", alignItems: "center", gap: 6, marginBottom: 4 }}>
            <div style={{ width: 6, height: 6, borderRadius: "50%", background: "#f06b63" }} />
            <span style={{ fontSize: 11, fontWeight: 600, color: "#f06b63" }}>Blocked item</span>
          </div>
          <div style={{ fontSize: 11.5, color: "#8b949e", lineHeight: 1.5 }}>
            <strong style={{ color: "#c9d1d9" }}>FL-002 gap handling</strong> — need to check what Moralis API actually returns (every interval, or only on holder state change). This determines whether FL-002 produces rows at every 5-min mark or only when something changes.
          </div>
        </div>

        {/* Footer */}
        <div style={{ textAlign: "center", marginTop: 32, fontSize: 10, color: "#2a2f3a", fontFamily: "mono" }}>
          Dataset Specification v1.0 · {CONCEPTS.length} concepts · {CONCEPTS.reduce((a, c) => a + (c.edgeCases?.length || 0), 0)} edge cases tracked
        </div>
      </div>
    </div>
  );
}
