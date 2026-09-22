/* Today board renderer — NBA points props (mockup). Table-only: with a full
   NBA slate (20+ meaningfully-projected players/night) a scannable sortable
   table beats a scrolling list of cards — see the planning doc. */
const $ = (s, el = document) => el.querySelector(s);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

/* Probability edge: model win % minus vig-free market win %. Primary ranking. */
const probEdge = (e) => (e.prob_edge != null ? e.prob_edge : (e.model_prob - e.vigfree_prob));

/* One row per pick: the same side+line offered at several books collapses
   to the single best price (highest EV). */
function dedupeEdges(edges) {
  const best = new Map();
  (edges || []).forEach((e) => {
    const k = `${e.side}|${e.line}`;
    if (!best.has(k) || e.ev_per_unit > best.get(k).ev_per_unit) best.set(k, e);
  });
  return [...best.values()];
}

function bestPick(s) {
  const pos = dedupeEdges(s.edges).filter((e) => e.ev_per_unit > 0).sort((a, b) => probEdge(b) - probEdge(a));
  return pos[0] || null;
}

function bestScore(s) {
  const best = bestPick(s);
  return best ? probEdge(best) : -1;
}

/* Slate-wide summary: best +EV edge per player, ranked by probability edge. */
function topEdges(players, n = 8) {
  const rows = [];
  (players || []).forEach((s) => {
    const e = bestPick(s);
    if (e) rows.push({ s, e });
  });
  return rows.sort((a, b) => probEdge(b.e) - probEdge(a.e)).slice(0, n);
}

function summaryTable(players) {
  const rows = topEdges(players, 8);
  if (!rows.length) {
    return `<h2 class="sec">Top edges</h2>
      <div class="notice" style="margin:0 0 16px">No positive-EV edges vs the current points lines.</div>`;
  }
  const body = rows.map(({ s, e }) => {
    const odds = e.odds > 0 ? "+" + e.odds : e.odds;
    const pick = `${e.side === "over" ? "▲ O" : "▼ U"} ${e.line}`;
    return `<tr>
      <td><div class="pn">${esc(s.player)}</div>
        <div class="pm">${s.home ? "vs" : "@"} ${esc(s.opp)} · ${esc(s.time_et)}</div></td>
      <td class="pk">${pick}</td>
      <td class="dim">${esc(e.book)} ${odds}</td>
      <td>${(e.model_prob * 100).toFixed(0)}%</td>
      <td class="pos">+${(probEdge(e) * 100).toFixed(1)}</td>
      <td class="pos">+${(e.ev_per_unit * 100).toFixed(1)}%</td>
    </tr>`;
  }).join("");
  return `<h2 class="sec">Top edges · biggest model vs. book gaps</h2>
    <div class="summary">
      <table class="t summary-t">
        <thead><tr>
          <th>Player</th><th>Pick</th><th>Book</th><th>Model</th><th>Edge</th><th>EV</th>
        </tr></thead>
        <tbody>${body}</tbody>
      </table>
    </div>`;
}

function confBadgeCompact(conf, tier) {
  const cls = conf >= 0.95 ? "conf-high" : conf >= 0.5 ? "conf-mid" : "conf-low";
  const label = { starter_full: "starter, full minutes", starter_uncertain: "starter, minutes uncertain",
    bench: "bench role", questionable: "questionable" }[tier] || "role unknown";
  return `<span class="badge ${cls}" title="${esc(label)}">${Math.round(conf * 100)}%</span>`;
}

/* ---------------- Full-slate table: one sortable row per player ---------------- */

const TABLE_COLS = [
  { key: "player", label: "Player", num: false, get: (s) => s.player },
  { key: "proj", label: "Proj pts", num: true, get: (s) => (s.proj ? s.proj.point : -1) },
  { key: "range", label: "p10–p90", num: true, get: (s) => (s.proj ? s.proj.p50 : -1) },
  { key: "conf", label: "Minutes", num: true, get: (s) => (s.proj ? s.proj.minutes_confidence : -1) },
  { key: "line", label: "Pts line", num: true, get: (s) => (s.pts_line ? s.pts_line.line : -1) },
  { key: "pick", label: "Best pick", num: true, get: bestScore },
];

let sortState = { key: "proj", dir: "desc" };

function sortPlayers(list) {
  const col = TABLE_COLS.find((c) => c.key === sortState.key) || TABLE_COLS[TABLE_COLS.length - 1];
  const dir = sortState.dir === "asc" ? 1 : -1;
  return list.slice().sort((a, b) => {
    const av = col.get(a), bv = col.get(b);
    if (col.num) return dir * ((av ?? -1) - (bv ?? -1));
    return dir * String(av ?? "").localeCompare(String(bv ?? ""));
  });
}

function tableRow(s) {
  const p = s.proj;
  const best = bestPick(s);
  const lineCell = s.pts_line
    ? `${s.pts_line.line} <span class="dim">${s.pts_line.books}bk</span>`
    : '<span class="dim">—</span>';
  const pickCell = best
    ? `<span class="pick">${best.side === "over" ? "▲O" : "▼U"} ${best.line}</span>
       <span class="dim"> ${esc(best.book)} ${best.odds > 0 ? "+" + best.odds : best.odds}</span>
       <div class="dim tsub">+${(probEdge(best) * 100).toFixed(1)} pts · +${(best.ev_per_unit * 100).toFixed(1)}% EV</div>`
    : '<span class="dim">—</span>';
  const nameCell = `<div class="pn">${esc(s.player)}</div>
    <div class="pm">${esc(s.team)} ${s.home ? "vs" : "@"} ${esc(s.opp)} · ${esc(s.time_et)}</div>`;
  if (!p) {
    return `<tr><td>${nameCell}</td><td class="dim" colspan="3">No projection yet</td>
      <td class="num">${lineCell}</td><td class="dim">—</td></tr>`;
  }
  return `<tr data-hasedge="${(s.edges || []).some((e) => e.ev_per_unit > 0)}">
    <td>${nameCell}</td>
    <td class="num proj">${p.point.toFixed(1)}</td>
    <td class="dim num">${p.p10}–${p.p90}</td>
    <td>${confBadgeCompact(p.minutes_confidence, p.minutes_tier)}</td>
    <td class="num">${lineCell}</td>
    <td>${pickCell}</td>
  </tr>`;
}

function tableView(players) {
  const rows = sortPlayers(players).map(tableRow).join("");
  const head = TABLE_COLS.map((c) => {
    const active = c.key === sortState.key;
    const arrow = active ? (sortState.dir === "asc" ? " ↑" : " ↓") : "";
    return `<th data-key="${c.key}" class="${active ? "sort-on" : ""}${c.num ? " num" : ""}">${esc(c.label)}${arrow}</th>`;
  }).join("");
  return `<div class="board-tbl-wrap today-tbl-wrap"><div class="board-tbl today-tbl">
    <table class="t"><thead><tr>${head}</tr></thead><tbody>${rows}</tbody></table>
  </div></div>`;
}

async function main() {
  let data;
  try {
    data = await (await fetch("data/today.json?_=" + Date.now())).json();
  } catch {
    $("#board").innerHTML = '<div class="notice">No data yet.</div>';
    return;
  }
  const upd = new Date(data.generated_at);
  $("#subtitle").textContent = `${data.date} · updated ${upd.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" })}`;
  const players = (data.players || []).slice()
    .sort((a, b) => bestScore(b) - bestScore(a) || String(a.time_et).localeCompare(b.time_et));

  let currentFilter = localStorage.getItem("nba_filter") || "all";

  const render = () => {
    const list = currentFilter === "edges" ? players.filter((s) => bestScore(s) > 0) : players;
    const summaryHtml = summaryTable(players);
    if (!list.length) {
      $("#board").innerHTML = summaryHtml + '<div class="notice">' + (currentFilter === "edges"
        ? "No positive-EV edges right now."
        : "No players on the slate.") + "</div>";
      return;
    }
    $("#board").innerHTML = summaryHtml + tableView(list);
    $("#board").querySelectorAll("th[data-key]").forEach((th) => th.addEventListener("click", () => {
      const key = th.dataset.key;
      sortState = key === sortState.key
        ? { key, dir: sortState.dir === "asc" ? "desc" : "asc" }
        : { key, dir: "desc" };
      render();
    }));
  };

  document.querySelectorAll(".chip[data-f]").forEach((ch) => ch.addEventListener("click", () => {
    document.querySelectorAll(".chip[data-f]").forEach((c) => c.classList.remove("on"));
    ch.classList.add("on");
    currentFilter = ch.dataset.f;
    localStorage.setItem("nba_filter", currentFilter);
    render();
  }));

  document.querySelectorAll(".chip[data-f]").forEach((c) => c.classList.toggle("on", c.dataset.f === currentFilter));
  render();
}
main();
