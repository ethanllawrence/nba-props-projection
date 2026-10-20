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

function riskBadge(level) {
  if (level === "unknown") return `<span class="badge conf-mid">no spread today</span>`;
  const cls = { low: "conf-high", medium: "conf-mid", high: "conf-low" }[level] || "conf-mid";
  return `<span class="badge ${cls}">${esc(level)} blowout risk</span>`;
}

/* Injury check flags (nproj/injury_check.py). Legs never change after the
   morning pick; a flag means something moved since then. */
function checkNote(c) {
  if (!c) return "";
  const bits = [];
  if (c.kind === "status" || c.to) {
    bits.push(c.to === "out"
      ? "Ruled <b>OUT</b> since this morning. Most books void a leg when the player doesn't play; check yours."
      : `Status changed since this morning: ${esc(c.from || "active")} → <b>${esc(c.to)}</b>. Check before tip-off.`);
  }
  if (c.proj_from != null) {
    bits.push(`Projection moved ${c.proj_from} → <b>${c.proj_to}</b> since this morning,
      ${c.against ? "<span class=\"neg\">against this leg</span>" : "<span class=\"pos\">in this leg's favor</span>"}.`);
  }
  return `<div class="check-box" style="margin:9px 0 0">${bits.join("<br>")}</div>`;
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
      ${checkNote(leg.check)}
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

/* Early-look parlay (nproj/model/early_parlay.py): milestone overs picked
   from the next game day's early-look projections, before lines exist. */
const niceDate = (d) => {
  const [y, m, day] = String(d).split("-").map(Number);
  return new Date(y, m - 1, day).toLocaleDateString([], { weekday: "short", month: "short", day: "numeric" });
};
const statShort = { pra: "PRA", points: "points", rebounds: "rebounds", assists: "assists" };

function earlyLegRow(l) {
  const ms = Math.floor(l.line) + 1;                  // 29.5 -> 30+
  const gap = Math.round(l.gap * 100);
  return `<tr>
    <td><div class="pn">${esc(l.player)}</div>
      <div class="pm">${esc(l.team)} ${l.home ? "vs" : "@"} ${esc(l.opp)} · proj ${l.proj} ${esc(statShort[l.stat])}</div></td>
    <td class="num"><div class="pos" style="font-weight:700">${ms}+ ${esc(statShort[l.stat])}</div>
      <div class="pm">${fmtOdds(l.odds)} ${esc(l.book || "")}</div>
      <div class="pm">model ${Math.round(l.model_prob * 100)}% · book ${Math.round(l.book_prob * 100)}%
        <span class="${gap >= 0 ? "pos" : "neg"}">(${gap >= 0 ? "+" : ""}${gap})</span></div></td>
  </tr>`;
}

function earlyCards(picks, emptyNote) {
  picks = (picks || []).slice().sort((a, b) => (a.date < b.date ? -1 : 1));
  if (!picks.length && emptyNote === "") return "";
  if (!picks.length) {
    return `<div class="notice" style="margin:8px 0">The early look parlay appears once the next game
      day's alt lines are bought, a day or two before the games.</div>`;
  }
  return picks.map((p) => {
    if (!p.legs || !p.legs.length) {
      return `<div class="notice" style="margin:8px 0">${esc(niceDate(p.date))}: ${esc(p.note || "No early look parlay.")}</div>`;
    }
    return `<div class="card">
      <div class="call-meta" style="margin-bottom:6px">${esc(niceDate(p.date))} · ${p.legs.length} legs ·
        <b>${fmtOdds(p.combined_odds)}</b> on the book · all legs hit: model ${Math.round(p.model_prob * 100)}%,
        book ${Math.round(p.book_prob * 100)}%</div>
      <div class="board-tbl-wrap"><div class="board-tbl compact-tbl"><table class="t">
        <thead><tr><th>Player</th><th class="num">Leg</th></tr></thead>
        <tbody>${p.legs.map(earlyLegRow).join("")}</tbody></table></div></div>
    </div>`;
  }).join("");
}

function record(hist) {
  const graded = (hist || []).filter((h) => h.result === "win" || h.result === "loss");
  const w = graded.filter((h) => h.result === "win").length;
  return { w, l: graded.length - w, n: graded.length };
}

function recordTiles(main, early) {
  const tile = (r, label) => `<div class="tile"><div class="v">${r.n ? `${r.w}-${r.l}` : "—"}</div>
    <div class="k">${label}</div>
    <div class="s">${r.n ? `${Math.round((r.w / r.n) * 100)}% hit · every leg must hit` : "no graded parlays yet"}</div></div>`;
  return `<div class="tiles">${tile(record(main), "Parlay of the Day")}${tile(record(early), "Early look parlay")}</div>`;
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
  // the hand-made preview from before opening night is never shown; it's
  // replaced in the data on the first real game day
  const mock = /MOCKUP/.test(data.note || "");
  const history = mock ? [] : (data.history || []);
  const t = !mock && data.today && data.today.legs && data.today.legs.length ? data.today : null;
  const tonight = t
    ? parlayHero(t) + t.legs.map(legCard).join("")
      + (data.note ? `<div class="noedge" style="margin:4px 0 12px">${esc(data.note)}</div>` : "")
    : `<div class="notice" style="margin:8px 0">${esc((!mock && data.no_parlay_reason) ||
        "No Parlay of the Day yet. It's picked each morning at 6 AM Arizona time from that day's lines; " +
        "the first one comes on opening day.")}</div>`;
  const earlyHist = data.early_history || [];
  // group early-look picks by the board date: tonight's (picked a day or
  // two ago, graded tomorrow morning) vs. the next game day's
  let boardDate = t ? t.date : null;
  try {
    boardDate = (await (await fetch("data/status.json?_=" + Date.now())).json()).board_date || boardDate;
  } catch { /* keep the parlay's date */ }
  const picks = Object.values(data.early || {});
  const earlyTonight = picks.filter((p) => boardDate && p.date <= boardDate && p.legs && p.legs.length);
  const earlyNext = picks.filter((p) => !boardDate || p.date > boardDate);
  const tonightEarly = earlyTonight.length
    ? `<div class="noedge" style="margin:14px 0 6px">Also tonight: the early look parlay, picked before
        today's lines from alt lines bought ahead of time. It's graded tomorrow morning with the Parlay of the Day.</div>
      ${earlyCards(earlyTonight, "")}`
    : "";
  $("#parlay-body").innerHTML = `
    <h2 class="sec">Tonight · Parlay of the Day</h2>
    ${tonight}
    ${tonightEarly}
    <h2 class="sec">Next game day · Early look parlay</h2>
    <div class="noedge" style="margin:-4px 0 10px">3-4 star overs at about +200 real odds from FanDuel/DraftKings
      alt lines. The model drops legs it rates well below the book and picks the combination it likes most
      relative to the book (the +/- number). Prices are from when the lines were bought, so check them
      before betting.</div>
    ${earlyCards(earlyNext)}
    <h2 class="sec">Track record</h2>
    ${recordTiles(history, earlyHist)}
    ${history.length ? historyTable(history).replace('<h2 class="sec">History</h2>', '<h2 class="sec">Parlay of the Day history</h2>') : ""}
    ${earlyHist.length ? historyTable(earlyHist).replace('<h2 class="sec">History</h2>', '<h2 class="sec">Early look history</h2>') : ""}`;
}
main();
