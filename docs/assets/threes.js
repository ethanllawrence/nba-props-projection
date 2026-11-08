/* 3PM tab (beta): docs/data/threes.json from nproj/export/threes_export.py.
   One row per player: projected made threes, then DraftKings' main line and
   the two ladder rungs above it (free via ESPN), each price colored by our
   chance against the book's. No calls yet:
   the model isn't blended with the market until enough lines are saved. */
const $ = (s, el = document) => el.querySelector(s);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const fmtOdds = (o) => (o > 0 ? "+" + o : String(o));
const pct = (p) => (p == null ? "–" : Math.round(p * 100) + "%");
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

/* Table (Nov 8, Robin): projection, the book's line, and the next two rungs
   above it (line +1, +2), each shown as the book's price colored by how our
   chance compares with the book's. Hover/tap a price for the two chances. */
const ladderAt = (p, n) => (p.ladder || []).find((s) => s.n === n);

function priceCell(label, price, edge, ours, book) {
  if (price == null) return '<td class="num dim">–</td>';
  const tip = ours != null && book != null ? ` title="our ${pct(ours)} vs book ${pct(book)}"` : "";
  return `<td class="num"${tip}><div class="pm">${label}</div><div>${styled(fmtOdds(price), edge, true)}</div></td>`;
}

/* Alt below the line: the deepest rung under the line still priced -400 or
   longer (Robin: a -1000 leg is never worth it, but -350 to -400 cushions are). */
const ALT_FLOOR = -400;
function altBelow(p, n) {
  const ok = (p.ladder || []).filter((s) => s.n < n && s.price >= ALT_FLOOR);
  return ok.length ? ok.reduce((a, b) => (b.n < a.n ? b : a)) : null;
}

function cells(p) {
  const l = p.line;
  if (!l) return '<td class="num dim" colspan="4">no line posted</td>';
  const n = Math.floor(l.line) + 1;                  // 4.5 -> 5+
  const alt = altBelow(p, n);
  const out = [alt ? priceCell(`${alt.n}+`, alt.price, alt.edge, alt.model, alt.book) : '<td class="num dim">–</td>',
               priceCell(`O ${l.line}`, l.over, l.edge, l.model_over, l.book_over)];
  for (const k of [n + 1, n + 2]) {
    const b = ladderAt(p, k);
    out.push(b ? priceCell(`${k}+`, b.price, b.edge, b.model, b.book) : '<td class="num dim">–</td>');
  }
  return out.join("");
}

const shortDate = (d) => {
  const [y, m, day] = String(d).split("-").map(Number);
  return new Date(y, m - 1, day).toLocaleDateString([], { month: "short", day: "numeric" });
};

/* Last 5 games' threes, green when he'd have cleared tonight's line, red when
   not (like the Today board's L5 PRA). */
function last5(p) {
  const g = p.last5 || [];
  if (!g.length) return "";
  const line = p.line ? p.line.line : null;
  const chips = g.map((x) => {
    const cls = line == null ? "" : x.made > line ? " hit" : " miss";
    const tip = `${shortDate(x.date)} · ${x.made} threes`;
    return `<span class="l5c${cls}" tabindex="0" data-tip="${esc(tip)}" aria-label="${esc(tip)}">${x.made}</span>`;
  }).join("");
  return `<div class="l5"><span class="l5k">L5</span>${chips}</div>`;
}

function row(p) {
  const where = p.home ? "vs" : "@";
  const flag = p.status && p.status !== "active" ? ` · <span class="neg">${esc(p.status)}</span>` : "";
  return `<tr>
    <td><div class="pn">${esc(p.player)}</div>
      <div class="pm">${esc(p.team)} ${where} ${esc(p.opp)} · ${esc(p.time_et)}${flag}</div>${last5(p)}</td>
    <td class="num"><div class="proj3">${styled(p.made.toFixed(1), p.line ? p.line.edge : null, true)}</div></td>
    ${cells(p)}
  </tr>`;
}

function maxGap(p) {
  if (!p.line) return 0;
  const n = Math.floor(p.line.line) + 1;
  const e = [p.line.edge, (altBelow(p, n) || {}).edge, ...[n + 1, n + 2].map((k) => (ladderAt(p, k) || {}).edge)];
  return Math.max(...e.filter((x) => x != null).map(Math.abs), 0);
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
    players.sort((a, b) => maxGap(b) - maxGap(a));
  } else if (sortBy === "lined") {
    players.sort((a, b) => (b.line ? 1 : 0) - (a.line ? 1 : 0) || b.made - a.made);
  }
  const chips = ["made", "lined", "gap"].map((k) => `<button class="chip ${sortBy === k ? "on" : ""}" data-sort="${k}">${
    { made: "Most threes", lined: "With a line", gap: "Biggest gaps" }[k]}</button>`).join("");
  $("#threes-body").innerHTML = `
    <div class="chips" style="margin-bottom:10px">${chips}</div>
    <div class="board-tbl threes-tbl"><table class="t">
      <thead><tr><th>Player</th><th class="num">Proj</th><th class="num">Alt</th><th class="num">Line</th>
        <th class="num">+1</th><th class="num">+2</th></tr></thead>
      <tbody>${players.map(row).join("") || '<tr><td colspan="6" class="dim">No 3PM projections for this slate yet.</td></tr>'}</tbody>
    </table></div>
    <div class="legend" style="margin-top:8px">
      <span><span class="sw grad"></span>our chance vs the book's: red lower · gold even · green higher</span>
      <span>Proj: projected threes, colored by the main line</span>
      <span>Alt: the safest rung under the line still priced -400 or longer</span>
      <span>Line / +1 / +2: DraftKings' over price at the line and one and two threes above it</span>
      <span>L5: threes in his last 5 games (green = over tonight's line)</span>
      <span>tap a price for both chances</span></div>
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
  // lined players first when the books have posted (the default view)
  if ((data.players || []).some((p) => p.line)) sortBy = "lined";
  render(data);
}
main();
