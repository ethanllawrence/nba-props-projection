/* 3PM tab (beta): docs/data/threes.json from nproj/export/threes_export.py.
   One row per player: projected attempts x make rate = projected made
   threes, the chance of 2+/3+/4+..., and DraftKings' main line and ladder
   (free via ESPN) with the model's chance next to the book's. No calls yet:
   the model isn't blended with the market until enough lines are saved. */
const $ = (s, el = document) => el.querySelector(s);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const fmtOdds = (o) => (o > 0 ? "+" + o : String(o));
const pct = (p) => (p == null ? "–" : Math.round(p * 100) + "%");
const fair = (p) => {
  if (!p || p <= 0 || p >= 1) return "–";
  return p >= 0.5 ? String(-Math.round((100 * p) / (1 - p))) : "+" + Math.round((100 * (1 - p)) / p);
};
const gap = (e) => {
  if (e == null) return "";
  const g = Math.round(e * 100);
  return `<span class="${g >= 0 ? "pos" : "neg"}">(${g >= 0 ? "+" : ""}${g})</span>`;
};
let sortBy = "made";

/* Board-style color (Nov 8, Robin): gold when we agree with the book, then
   green (we like the over more) or red (less), full color at a 15-point gap.
   3PM lines are small numbers, so the gap is measured in chance points
   (model % minus the book's % with the vig removed), not projection vs line:
   0.4 threes over a 0.5 line and over a 4.5 line mean very different things. */
const GAP_DEAD = 0.03, GAP_FULL = 0.15;
function tint(edge) {
  if (edge == null) return null;
  const t = Math.max(0, Math.min(1, (Math.abs(edge) - GAP_DEAD) / (GAP_FULL - GAP_DEAD)));
  if (t === 0) return "var(--gold)";
  return `color-mix(in srgb, ${edge > 0 ? "var(--green)" : "var(--red)"} ${Math.round(35 + 65 * t)}%, var(--gold))`;
}
const styled = (txt, edge, bold) => {
  const c = tint(edge);
  return `<span style="${c ? `color:${c};` : ""}${bold ? "font-weight:700;" : ""}">${txt}</span>`;
};

function projCell(p) {
  const l = p.line;
  const sub = l
    ? `<div class="pm">O ${l.line} ${l.over != null ? fmtOdds(l.over) : ""}</div><div class="pm">${pct(l.model_over)} / ${pct(l.book_over)}</div>`
    : '<div class="pm dim">no line</div>';
  return `<td class="num"><div style="font-size:16px">${styled(p.made.toFixed(1), l ? l.edge : null, true)}</div>${sub}</td>`;
}

function rungCell(p, n) {
  const m = p.rungs[n - 1];
  const b = (p.ladder || []).find((s) => s.n === n);
  if (m == null) return '<td class="num dim">–</td>';
  return `<td class="num" title="model fair price ${fair(m)}"><div>${styled(pct(m), b ? b.edge : null, !!b)}</div>
    <div class="pm">${b ? fmtOdds(b.price) : `<span class="dim">${fair(m)}</span>`}</div></td>`;
}

function row(p) {
  const where = p.home ? "vs" : "@";
  const flag = p.status && p.status !== "active" ? ` · <span class="neg">${esc(p.status)}</span>` : "";
  return `<tr>
    <td><div class="pn">${esc(p.player)}</div>
      <div class="pm">${esc(p.team)} ${where} ${esc(p.opp)} · ${esc(p.time_et)}${flag}</div>
      <div class="pm">${p.attempts.toFixed(1)} att × ${(p.p3 * 100).toFixed(0)}%</div></td>
    ${projCell(p)}${rungCell(p, 2)}${rungCell(p, 3)}${rungCell(p, 4)}
  </tr>`;
}

function trackTiles(t) {
  if (!t || !t.days) {
    return `<div class="notice" style="margin:8px 0">Track record starts once the first night is graded
      (the morning after its games).</div>`;
  }
  const r2 = (t.rungs || {})["2+"] || {};
  const tile = (v, k, s) => `<div class="tile"><div class="v">${v}</div><div class="k">${k}</div>
    <div class="s">${s}</div></div>`;
  const lines = t.lines
    ? tile(pct(t.lines.over_hit), "Overs hit (lined)", `model said ${pct(t.lines.model_said_over)}, book ${pct(t.lines.book_said_over)} · ${t.lines.n} lines`)
    : tile("–", "Lined players", "no graded lines yet");
  return `<div class="tiles">
    ${tile(t.days, "Nights graded", `${t.player_games} player-games`)}
    ${tile(t.avg_miss_made.toFixed(2), "Avg miss, 3PM", `bias ${t.bias_made >= 0 ? "+" : ""}${t.bias_made.toFixed(2)}`)}
    ${tile(pct(r2.hit), "2+ threes hit", `model said ${pct(r2.said)}`)}
    ${lines}</div>`;
}

function render(data) {
  const players = (data.players || []).slice();
  if (sortBy === "gap") {
    const g = (p) => Math.max(Math.abs((p.line || {}).edge || 0), ...((p.ladder || []).map((x) => Math.abs(x.edge))));
    players.sort((a, b) => g(b) - g(a));
  } else if (sortBy === "lined") {
    players.sort((a, b) => (b.line ? 1 : 0) - (a.line ? 1 : 0) || b.made - a.made);
  }
  const chips = ["made", "lined", "gap"].map((k) => `<button class="chip ${sortBy === k ? "on" : ""}" data-sort="${k}">${
    { made: "Most threes", lined: "With a line", gap: "Biggest gaps" }[k]}</button>`).join("");
  $("#threes-body").innerHTML = `
    <div class="chips" style="margin-bottom:10px">${chips}</div>
    <div class="board-tbl threes-tbl"><table class="t">
      <thead><tr><th>Player</th><th class="num">3PM</th>
        <th class="num">2+</th><th class="num">3+</th><th class="num">4+</th></tr></thead>
      <tbody>${players.map(row).join("") || '<tr><td colspan="5" class="dim">No 3PM projections for this slate yet.</td></tr>'}</tbody>
    </table></div>
    <div class="legend" style="margin-top:8px">
      <span><span class="sw grad"></span>our chance vs the book's: red below · gold even · green above</span>
      <span>3PM: projected threes, the book's line, then our over % / the book's</span>
      <span>2+ 3+ 4+: our chance, the book's price under it (grey = our fair price, not posted)</span>
      <span>prices: DraftKings via ESPN</span></div>
    <h2 class="sec">Track record (beta)</h2>
    ${trackTiles(data.track)}`;
  document.querySelectorAll("[data-sort]").forEach((b) => b.addEventListener("click", () => {
    sortBy = b.dataset.sort;
    render(data);
  }));
}

async function main() {
  let data;
  try {
    data = await (await fetch("data/threes.json?_=" + Date.now())).json();
  } catch {
    $("#threes-body").innerHTML = '<div class="notice">No 3PM projections yet. They appear after the next board run.</div>';
    $("#subtitle").textContent = "beta";
    return;
  }
  const upd = new Date(data.generated_at);
  $("#subtitle").textContent = `${data.date} · updated ${upd.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" })}`;
  render(data);
}
main();
