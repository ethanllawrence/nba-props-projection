/* Today board renderer — NBA points/rebounds/assists props (mockup). Condensed
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
];

let sortState = { key: "points", dir: "desc" };

function statValue(player, key) {
  const s = player.stats && player.stats[key];
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

function statCell(stat) {
  if (!stat) return `<td class="num"><span class="dim">—</span></td>`;
  const edge = edgeOf(stat);
  const cls = edgeClass(edge);
  return `<td class="num">
    <div class="stat-cell">
      <div class="num ${cls}">${stat.proj.toFixed(1)}</div>
      <div class="line">L ${stat.line.toFixed(1)} <span class="dim">· ${stat.books}bk</span></div>
    </div>
  </td>`;
}

function tableRow(p) {
  const nameCell = `<td><div class="pn">${esc(p.player)}</div>
    <div class="pm">${esc(p.team)} ${p.home ? "vs" : "@"} ${esc(p.opp)} · ${esc(p.time_et)}</div></td>`;
  const cells = STAT_COLS.map((c) => statCell(p.stats && p.stats[c.key])).join("");
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
      return `<th data-key="${c.key}" class="num${active ? " sort-on" : ""}">${esc(c.label)}${arrow}</th>`;
    }).join("");
  return `<div class="legend">
      <span><span class="sw g"></span>edge (over)</span>
      <span><span class="sw o"></span>near the book</span>
      <span><span class="sw r"></span>edge (under)</span>
    </div>
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
