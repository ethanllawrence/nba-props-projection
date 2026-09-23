/* Results — how the Today board's projections held up. Reads data/results.json,
   written by nproj/results.py after each night's games are final. */
const $ = (s, el = document) => el.querySelector(s);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

const STATS = [["points", "Points"], ["rebounds", "Rebounds"], ["assists", "Assists"], ["pra", "PRA"]];
const pct = (x) => (x == null ? "—" : Math.round(x * 100) + "%");
const rec = (r) => `${r.wins}-${r.losses}${r.pushes ? "-" + r.pushes : ""}`;
const rate = (r) => {
  const d = r.wins + r.losses;
  return d ? r.wins / d : null;
};

function headline(sum) {
  const hr = sum.hit_rate;
  const cls = hr == null ? "" : hr >= sum.breakeven ? "pos" : "neg";
  return `
    <div class="tiles">
      <div class="tile"><div class="v ${cls}">${pct(hr)}</div><div class="k">Edge-call hit rate</div>
        <div class="s">${pct(sum.breakeven)} breaks even at -110</div></div>
      <div class="tile"><div class="v">${rec(sum.record)}</div><div class="k">Edge calls</div>
        <div class="s">green/red cells, all season</div></div>
      <div class="tile"><div class="v">${sum.mae.pra ?? "—"}</div><div class="k">PRA miss (avg)</div>
        <div class="s">points off, per player</div></div>
      <div class="tile"><div class="v">${sum.nights}</div><div class="k">Nights graded</div>
        <div class="s">since opening night</div></div>
    </div>`;
}

function byStatTable(sum) {
  const rows = STATS.map(([k, label]) => {
    const r = sum.by_stat_recent[k];
    const hr = rate(r);
    const cls = hr == null ? "dim" : hr >= sum.breakeven ? "pos" : "neg";
    return `<tr><td><div class="pn">${label}</div></td>
      <td class="num">${rec(r)}</td><td class="num ${cls}">${pct(hr)}</td>
      <td class="num dim">${sum.mae[k] ?? "—"}</td></tr>`;
  }).join("");
  const sides = ["over", "under"].map((s) => {
    const r = sum.by_side_recent[s];
    return `${s === "over" ? "Overs" : "Unders"} ${rec(r)} (${pct(rate(r))})`;
  }).join(" · ");
  return `
    <h2 class="sec">By stat</h2>
    <div class="board-tbl-wrap"><div class="board-tbl compact-tbl">
      <table class="t">
        <thead><tr><th>Stat</th><th class="num">Calls</th><th class="num">Hit</th>
          <th class="num">Miss</th></tr></thead>
        <tbody>${rows}</tbody>
      </table>
    </div></div>
    <div class="noedge" style="margin:6px 0 14px">${sides}. Calls: last
      ${sum.recent_calls_window} edge calls. Avg miss: all projections, all season.</div>`;
}

function nightsTable(nights) {
  const rows = nights.slice(-30).reverse().map((n) => {
    const hr = rate(n.calls);
    const cls = hr == null ? "dim" : hr >= 0.524 ? "pos" : "neg";
    return `<tr><td><div class="pn">${esc(n.date)}</div><div class="pm">${n.players_graded} players</div></td>
      <td class="num">${rec(n.calls)}</td><td class="num ${cls}">${pct(hr)}</td>
      <td class="num dim">${n.mae.pra ?? "—"}</td></tr>`;
  }).join("");
  return `
    <h2 class="sec">Last 30 nights</h2>
    <div class="board-tbl-wrap"><div class="board-tbl compact-tbl">
      <table class="t">
        <thead><tr><th>Night</th><th class="num">Calls</th><th class="num">Hit</th>
          <th class="num">PRA ±</th></tr></thead>
        <tbody>${rows}</tbody>
      </table>
    </div></div>`;
}

function callsTable(calls) {
  const abbr = { points: "Pts", rebounds: "Reb", assists: "Ast", pra: "PRA" };
  const rows = calls.slice(-25).reverse().map((c) => {
    const cls = c.result === "win" ? "win-go" : c.result === "loss" ? "win-off" : "win-wait";
    const lab = { win: "HIT", loss: "MISS", push: "PUSH" }[c.result];
    return `<tr><td><div class="pn">${esc(c.player)}</div>
        <div class="pm">${esc(c.date)} · ${c.side === "over" ? "O" : "U"} ${c.line} ${abbr[c.stat]}
        · proj ${c.proj}</div></td>
      <td class="num">${c.actual}</td><td><span class="win ${cls}">${lab}</span></td></tr>`;
  }).join("");
  return `
    <h2 class="sec">Latest edge calls</h2>
    <div class="board-tbl-wrap"><div class="board-tbl compact-tbl">
      <table class="t">
        <thead><tr><th>Call</th><th class="num">Actual</th><th>Result</th></tr></thead>
        <tbody>${rows}</tbody>
      </table>
    </div></div>`;
}

async function main() {
  let data;
  try {
    data = await (await fetch("data/results.json?_=" + Date.now())).json();
  } catch {
    data = null;
  }
  if (!data || !data.nights || !data.nights.length || !data.summary) {
    $("#subtitle").textContent = "no nights graded yet";
    $("#results-body").innerHTML = `<div class="notice">Nothing graded yet. The first night is
      graded the morning after opening night${data && data.pending
        ? ` (waiting on ${esc(data.pending.date)}: ${data.pending.players.length} players)` : ""}.</div>`;
    return;
  }
  const upd = new Date(data.generated_at);
  $("#subtitle").textContent = `updated ${upd.toLocaleDateString([], { month: "short", day: "numeric" })}`;
  const sum = data.summary;
  $("#results-body").innerHTML = headline(sum) + byStatTable(sum) + nightsTable(data.nights) +
    (data.calls && data.calls.length ? callsTable(data.calls) : "");
}
main();
