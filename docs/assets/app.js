/* Today board renderer — NBA points/rebounds/assists props. Condensed
   table: one row per player, one colored cell per stat (proj vs. book line),
   sortable by clicking any stat column to surface the highest projections. */
const $ = (s, el = document) => el.querySelector(s);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

/* Coloring comes from the model, not a flat rule: each stat with a book
   line carries p_over (the model's chance of the over, blended with the
   market price, see nproj/model/live.py) and call ("over"/"under"/null).
   Green = the model likes the over enough to beat the price, red = the
   under, gold = there's a line but no edge. Older data without p_over
   (before the model) shows uncolored. */
function edgeClass(stat) {
  if (!stat || stat.line == null || stat.p_over == null) return "";
  if (stat.call === "over") return "pos";
  if (stat.call === "under") return "neg";
  return "neu";
}

const STAT_COLS = [
  { key: "points", label: "Points" },
  { key: "rebounds", label: "Reb" },
  { key: "assists", label: "Ast" },
  { key: "pra", label: "PRA", cls: "pra-col" },
];

let sortState = { key: "points", dir: "desc" };

/* PRA: the model's own PRA projection and the book's real PRA line when
   they exist; otherwise the sums of the three stats and three lines. */
function praOf(player) {
  const s = player.stats;
  if (!s || !s.points || !s.rebounds || !s.assists) return null;
  if (s.pra && s.pra.proj != null) {
    if (s.pra.line != null) return s.pra;
    const lines = [s.points.line, s.rebounds.line, s.assists.line];
    return { ...s.pra, line: lines.every((l) => l != null) ? lines.reduce((a, b) => a + b, 0) : null };
  }
  const lines = [s.points.line, s.rebounds.line, s.assists.line];
  return {
    proj: s.points.proj + s.rebounds.proj + s.assists.proj,
    line: lines.every((l) => l != null) ? lines.reduce((a, b) => a + b, 0) : null,
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
  const edgeCls = edgeClass(stat);
  let sub = hasLine ? "L " + stat.line.toFixed(1) : "no line";
  if (hasLine && stat.p_over != null) {
    const pick = stat.call === "under" || (!stat.call && stat.p_over < 0.5)
      ? `U ${Math.round((1 - stat.p_over) * 100)}%` : `O ${Math.round(stat.p_over * 100)}%`;
    sub += ` · ${pick}`;
  }
  return `<td class="num${cls}">
    <div class="stat-cell">
      <div class="num ${edgeCls}">${stat.proj.toFixed(1)}</div>
      <div class="line">${sub}</div>
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
  const anyLines = players.some((p) => Object.values(p.stats || {}).some((s) => s && s.p_over != null));
  const legend = anyLines
    ? `<div class="legend">
      <span><span class="sw g"></span>model edge: over</span>
      <span><span class="sw o"></span>no edge vs. price</span>
      <span><span class="sw r"></span>model edge: under</span>
    </div>`
    : `<div class="legend"><span>Projections only: no book lines for this slate yet.</span></div>`;
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
