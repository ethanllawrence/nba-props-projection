/* Parlay of the Day — 2-4 high-conviction legs (alt lines when the budget
   allowed buying them, main lines otherwise), picked automatically for model
   confidence with low blowout/minutes risk, not for the best expected value.
   Settled the next morning. Selection logic: nproj/model/parlay.py. */
const $ = (s, el = document) => el.querySelector(s);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

const fmtOdds = (o) => (o > 0 ? "+" + o : String(o));
const statLabel = { points: "Points", rebounds: "Rebounds", assists: "Assists", pra: "Pts+Reb+Ast" };
const statAbbr = { points: "Pts", rebounds: "Reb", assists: "Ast", pra: "PRA" };
const lineOf = (leg) => leg.line ?? leg.alt_line;

function parlayHero(t) {
  const statusCls = { pending: "win-wait", win: "win-go", loss: "win-off" }[t.status] || "win-wait";
  const statusLabel = { pending: "PENDING", win: "HIT", loss: "MISS" }[t.status] || "PENDING";
  return `
    <div class="call-hero">
      <div class="call-meta">Parlay of the Day · ${esc(t.date)} · ${t.legs.length} legs</div>
      <div class="call-badge" style="background:transparent;border:2px solid var(--line);color:var(--text);
        font-size:26px;padding-top:6px;padding-bottom:6px">${fmtOdds(t.combined_odds)}</div>
      <div class="call-prob"><span class="win ${statusCls}">${statusLabel}</span></div>
    </div>`;
}

function trackRecord(history) {
  if (!history.length) return "";
  const graded = history.filter((h) => h.result === "win" || h.result === "loss");
  if (!graded.length) return "";
  const wins = graded.filter((h) => h.result === "win").length;
  const losses = graded.length - wins;
  const hitRate = Math.round((wins / graded.length) * 100);
  let streak = 0;
  for (const h of graded.slice().reverse()) {
    if (h.result === "win") streak++;
    else break;
  }
  return `
    <div class="tiles">
      <div class="tile"><div class="v">${wins}-${losses}</div><div class="k">Record</div>
        <div class="s">last ${graded.length} parlays</div></div>
      <div class="tile"><div class="v">${hitRate}%</div><div class="k">Hit rate</div>
        <div class="s">all legs must hit</div></div>
      <div class="tile"><div class="v">${streak}</div><div class="k">Current streak</div>
        <div class="s">${streak > 0 ? "wins in a row" : "snapped last parlay"}</div></div>
    </div>`;
}

function riskBadge(level) {
  if (level === "unknown") return `<span class="badge conf-mid">no spread today</span>`;
  const cls = { low: "conf-high", medium: "conf-mid", high: "conf-low" }[level] || "conf-mid";
  return `<span class="badge ${cls}">${esc(level)} blowout risk</span>`;
}

function legCard(leg) {
  const sideArrow = leg.side === "over" ? "▲ O" : "▼ U";
  const line = lineOf(leg);
  const isAlt = (leg.line_type || "alt") === "alt";
  const where = leg.home === undefined ? "vs" : leg.home ? "vs" : "@";
  const lineInfo = isAlt ? `vs. full line ${leg.book_line} · alt ${line}` : `vs. line ${line}`;
  const probTxt = leg.prob != null ? ` · model ${Math.round(leg.prob * 100)}%` : "";
  return `
    <div class="card">
      <div class="top">
        <div class="who">
          <div class="name">${esc(leg.player)}</div>
          <div class="meta">${esc(leg.team)} ${where} ${esc(leg.opp)} · ${esc(leg.time_et)}</div>
        </div>
        <div class="pt">
          <div class="num pos">${sideArrow} ${line}</div>
          <div class="lbl">${esc(statLabel[leg.stat] || leg.stat)}</div>
        </div>
      </div>
      <div class="mkt">
        <span class="mkt-item">Model proj <b>${leg.proj}</b>
          <span class="dim">${lineInfo}${probTxt}</span></span>
        <span class="mkt-src">${fmtOdds(leg.odds)} · ${esc(leg.book)}</span>
      </div>
      <div class="badges">
        ${riskBadge(leg.blowout_risk)}
        <span class="badge conf-high">${esc(leg.minutes_confidence)} minutes confidence</span>
      </div>
      <div class="noedge" style="margin-top:9px">${esc(leg.reasoning)}</div>
    </div>`;
}

function historyRow(h) {
  const hits = h.legs.filter((l) => l.hit).length;
  const cls = { win: "win-go", loss: "win-off" }[h.result] || "win-wait";
  const label = { win: "HIT", loss: "MISS", void: "VOID" }[h.result] || "—";
  const legSummary = h.legs.map((l) =>
    `${esc(l.player.split(" ").slice(-1)[0])} ${l.side === "over" ? "O" : "U"}${lineOf(l)}
     ${statAbbr[l.stat] || l.stat} ${l.void ? "void" : l.hit ? "✓" : "✗"}`
  ).join(" · ");
  return `<tr>
    <td><div class="pn">${esc(h.date)}</div><div class="pm">${esc(legSummary)}</div></td>
    <td class="num">${fmtOdds(h.combined_odds)}</td>
    <td class="num dim">${hits}/${h.legs.length}</td>
    <td><span class="win ${cls}">${label}</span></td>
  </tr>`;
}

function historyTable(history) {
  if (!history.length) return "";
  const rows = history.slice().reverse().map(historyRow).join("");
  return `
    <h2 class="sec">History</h2>
    <div class="board-tbl-wrap"><div class="board-tbl compact-tbl parlay-hist">
      <table class="t">
        <thead><tr><th>Date</th><th class="num">Odds</th><th class="num">Legs</th><th>Result</th></tr></thead>
        <tbody>${rows}</tbody>
      </table>
    </div></div>`;
}

async function main() {
  let data;
  try {
    data = await (await fetch("data/parlay.json?_=" + Date.now())).json();
  } catch {
    $("#parlay-body").innerHTML = '<div class="notice">No data yet.</div>';
    return;
  }
  const upd = new Date(data.generated_at);
  $("#subtitle").textContent = `updated ${upd.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" })}`;
  const history = data.history || [];
  if (data.note && $("#note")) $("#note").textContent = data.note;
  const todayHtml = data.today && data.today.legs && data.today.legs.length
    ? parlayHero(data.today) + data.today.legs.map(legCard).join("")
    : `<div class="notice" style="margin:12px 0">${esc(data.no_parlay_reason ||
        "No parlay yet today. It's picked each morning once the day's lines are in.")}</div>`;
  $("#parlay-body").innerHTML = todayHtml + trackRecord(history) + historyTable(history);
}
main();
