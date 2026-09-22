/* Today board renderer — NBA points/rebounds/assists props. Condensed
   table: one row per player, one colored cell per stat (proj vs. book line),
   sortable by clicking any stat column to surface the highest projections. */
const $ = (s, el = document) => el.querySelector(s);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

/* Edge = (proj - line) / line, as a fraction. Green when the model likes the
   over meaningfully, red when it likes the under meaningfully, gold/neutral
   when it's close to the book. One relative threshold works across points,
   rebounds and assists despite their very different natural scales. */
const EDGE_THRESHOLD = 0.06;

function edgeOf(stat) {
  if (!stat || stat.line == null || !stat.line) return null;
  return (stat.proj - stat.line) / stat.line;
}

function edgeClass(edge) {
  if (edge == null) return "dim";
  if (edge >= EDGE_THRESHOLD) return "pos";
  if (edge <= -EDGE_THRESHOLD) return "neg";
  return "neu";
}

const STAT_COLS = [
  { key: "points", label: "Points" },
  { key: "rebounds", label: "Reb" },
  { key: "assists", label: "Ast" },
  { key: "pra", label: "PRA", cls: "pra-col" },
];

let sortState = { key: "points", dir: "desc" };

/* PRA (points + rebounds + assists): the projection is the sum of the three.
   The line is the book's real PRA line (stats.pra) when one was pulled,
   otherwise the sum of the three individual lines. */
function praOf(player) {
  const s = player.stats;
  if (!s || !s.points || !s.rebounds || !s.assists) return null;
  const lines = [s.points.line, s.rebounds.line, s.assists.line];
  const summed = lines.every((l) => l != null) ? lines.reduce((a, b) => a + b, 0) : null;
  return {
    proj: s.points.proj + s.rebounds.proj + s.assists.proj,
    // A real PRA line from the book wins; otherwise the sum of the three lines, if all exist.
    line: s.pra && s.pra.line != null ? s.pra.line : summed,
  };
}

function statOf(player, key) {
  return key === "pra" ? praOf(player) : player.stats && player.stats[key];
}

function statValue(player, key) {
  const s = statOf(player, key);
  return s ? s.proj : -1;
}

function sortPlayers(list) {
  const dir = sortState.dir === "asc" ? 1 : -1;
  return list.slice().sort((a, b) => {
    if (sortState.key === "player") {
      return dir * String(a.player).localeCompare(b.player);
    }
    return dir * (statValue(a, sortState.key) - statValue(b, sortState.key));
  });
}

function statCell(stat, extraCls) {
  const cls = extraCls ? ` ${extraCls}` : "";
  if (!stat) return `<td class="num${cls}"><span class="dim">—</span></td>`;
  const hasLine = stat.line != null;
  // No book line yet (odds not wired in): show the projection uncolored.
  const edgeCls = hasLine ? edgeClass(edgeOf(stat)) : "";
  return `<td class="num${cls}">
    <div class="stat-cell">
      <div class="num ${edgeCls}">${stat.proj.toFixed(1)}</div>
      <div class="line">${hasLine ? "L " + stat.line.toFixed(1) : "no line"}</div>
    </div>
  </td>`;
}

function tableRow(p) {
  const tag = p.status ? ` <span class="dim">(${esc(p.status)})</span>` : "";
  const nameCell = `<td><div class="pn">${esc(p.player)}${tag}</div>
    <div class="pm">${esc(p.team)} ${p.home ? "vs" : "@"} ${esc(p.opp)} · ${esc(p.time_et)}</div></td>`;
  const cells = STAT_COLS.map((c) => statCell(statOf(p, c.key), c.key === "pra" ? "pra-cell" : "")).join("");
  return `<tr>${nameCell}${cells}</tr>`;
}

function tableView(players) {
  const rows = sortPlayers(players).map(tableRow).join("");
  const playerActive = sortState.key === "player";
  const playerArrow = playerActive ? (sortState.dir === "asc" ? " ↑" : " ↓") : "";
  const head = `<th data-key="player" class="${playerActive ? "sort-on" : ""}">Player${playerArrow}</th>` +
    STAT_COLS.map((c) => {
      const active = c.key === sortState.key;
      const arrow = active ? (sortState.dir === "asc" ? " ↑" : " ↓") : "";
      const cls = ["num", c.cls, active ? "sort-on" : ""].filter(Boolean).join(" ");
      return `<th data-key="${c.key}" class="${cls}">${esc(c.label)}${arrow}</th>`;
    }).join("");
  const anyLines = players.some((p) => Object.values(p.stats || {}).some((s) => s && s.line != null));
  const legend = anyLines
    ? `<div class="legend">
      <span><span class="sw g"></span>edge (over)</span>
      <span><span class="sw o"></span>near the book</span>
      <span><span class="sw r"></span>edge (under)</span>
    </div>`
    : `<div class="legend"><span>Projections only. Book lines and edge colors arrive once odds are wired in.</span></div>`;
  return `${legend}
    <div class="board-tbl-wrap today-tbl-wrap"><div class="board-tbl today-tbl">
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
  if (data.note) $("#note").textContent = data.note + " Tap a column header to sort.";
  const players = data.players || [];

  const render = () => {
    if (!players.length) {
      $("#board").innerHTML = '<div class="notice">No players on the slate.</div>';
      return;
    }
    $("#board").innerHTML = tableView(players);
    $("#board").querySelectorAll("th[data-key]").forEach((th) => th.addEventListener("click", () => {
      const key = th.dataset.key;
      sortState = key === sortState.key
        ? { key, dir: sortState.dir === "asc" ? "desc" : "asc" }
        : { key, dir: key === "player" ? "asc" : "desc" };
      render();
    }));
  };

  render();
}
main();
