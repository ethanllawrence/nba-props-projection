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
/* Color = where the projection sits vs. the book line (Robin, Oct 22): a
   gradient from red (well below the line) through gold (about even) to green
   (well above), full color at 25%+ off the line. It's a direction-at-a-glance
   guide (e.g. how safe a lower alt line looks), not a bet signal. A real edge
   call (the model's chance beats the price; see Edge coloring on the Method
   page) gets a box around the number. Far-from-line cells (1.5x+, "no call")
   stay grey: there the book likely knows something the model doesn't. */
const GRAD_DEAD = 0.03, GRAD_FULL = 0.25;
function lineTint(stat) {
  if (!stat || stat.line == null || !(stat.line > 0)) return null;
  if (stat.flag === "far") return "var(--dim)";
  const d = (stat.proj - stat.line) / stat.line;
  const t = Math.max(0, Math.min(1, (Math.abs(d) - GRAD_DEAD) / (GRAD_FULL - GRAD_DEAD)));
  if (t === 0) return "var(--gold)";
  return `color-mix(in srgb, ${d > 0 ? "var(--green)" : "var(--red)"} ${Math.round(35 + 65 * t)}%, var(--gold))`;
}

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
let earlyView = false;   // early look: no lines exist yet, so cells show the projection only
let linedStats = new Set();   // stats with at least one line tonight (big slates: often PRA only)

/* Injury check (nproj/injury_check.py): later runs compare against the
   morning board and list who was ruled out/cleared and whose projection
   moved. Moved cells get a small arrow with the change. */
const MOVE_MIN = { points: 1.5, rebounds: 1.0, assists: 1.0, pra: 2.0 };
const clock = (iso) => new Date(iso).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
const STAT_ABBR = { points: "pts", rebounds: "reb", assists: "ast", pra: "PRA" };

function checkBanner(ic) {
  if (!ic) {
    return `<div class="check-box calm">Injury checks run about an hour before each tip-off.
      Anything that changes after this board (a player ruled out, projections moving because of it)
      will show up here, so check back before games start.</div>`;
  }
  const head = `Injury check ${clock(ic.checked_at)} · changes since ${esc(ic.since_label || "this morning's board")}
    (${clock(ic.since)})`;
  const changes = ic.status_changes || [];
  const moves = ic.moves || [];
  if (!changes.length && !moves.length) {
    return `<div class="check-box calm"><b>${head}</b>
      <div>No status changes or projection moves. The morning numbers still stand.</div></div>`;
  }
  const st = changes.slice(0, 12).map((c) =>
    `<li><b>${esc(c.player)}</b> (${esc(c.team)}): ${esc(c.from)} → <b class="${c.to === "out" ? "neg" : c.to === "active" ? "pos" : ""}">${esc(c.to)}</b></li>`).join("");
  const mv = moves.slice(0, 8).map((m) => {
    const parts = Object.entries(m.delta || {}).filter(([k, d]) => Math.abs(d) >= MOVE_MIN[k])
      .map(([k, d]) => `${STAT_ABBR[k]} ${m.from[k]} → ${m.to[k]} <span class="mv ${d > 0 ? "up" : "down"}">${d > 0 ? "▲" : "▼"}${Math.abs(d).toFixed(1)}</span>`);
    const flips = Object.entries(m.call_flips || {}).map(([k, f]) =>
      `${STAT_ABBR[k]} edge ${f.from || "none"} → ${f.to || "none"}`);
    return `<li><b>${esc(m.player)}</b> (${esc(m.team)}): ${parts.concat(flips).join(" · ")}</li>`;
  }).join("");
  return `<div class="check-box"><b>${head}</b>
    ${st ? `<div class="ck-sub">Status changes</div><ul>${st}</ul>` : ""}
    ${mv ? `<div class="ck-sub">Projections that moved</div><ul>${mv}</ul>` : ""}
  </div>`;
}

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

function moveTag(delta, key) {
  if (delta == null || Math.abs(delta) < MOVE_MIN[key]) return "";
  return ` <span class="mv ${delta > 0 ? "up" : "down"}" title="since the morning board">${delta > 0 ? "▲" : "▼"}${Math.abs(delta).toFixed(1)}</span>`;
}

function statCell(stat, extraCls, delta, key) {
  const cls = extraCls ? ` ${extraCls}` : "";
  if (!stat) return `<td class="num${cls}"><span class="dim">—</span></td>`;
  const hasLine = stat.line != null;
  const edgeCls = edgeClass(stat);
  // a whole column without lines (budget bought PRA only) stays blank; a single
  // missing line among lined players gets a quiet dash
  let sub = hasLine ? "L " + stat.line.toFixed(1)
    : earlyView || !linedStats.has(key) ? "&nbsp;" : '<span class="dim" title="no line for this player">–</span>';
  if (hasLine && stat.flag === "far") {
    // projection 1.5x+ the line: the book likely knows about a role or minutes
    // change the model can't see; historically no edge there, so no call
    sub += ` · <span class="dim" title="Model is ${(stat.proj / stat.line).toFixed(1)}x the line. The book likely knows about a role or minutes change the model can't see; overs this far above the line haven't beaten the book, so no call.">no call ⓘ</span>`;
  } else if (hasLine && stat.p_over != null) {
    const pick = stat.call === "under" || (!stat.call && stat.p_over < 0.5)
      ? `U ${Math.round((1 - stat.p_over) * 100)}%` : `O ${Math.round(stat.p_over * 100)}%`;
    sub += ` · ${pick}`;
  }
  const tint = earlyView ? null : lineTint(stat);
  const callCls = stat.call ? ` edge-call ${edgeCls}` : "";
  const pctTxt = hasLine && stat.line > 0 ? `${stat.proj > stat.line ? "+" : ""}${Math.round((stat.proj - stat.line) / stat.line * 100)}% vs the line` : "";
  const tip = stat.call ? `Model edge: ${stat.call} (the model's chance beats the price). ${pctTxt}` : pctTxt;
  return `<td class="num${cls}">
    <div class="stat-cell">
      <div class="num${callCls}"${tint && !stat.call ? ` style="color:${tint}"` : ""}${tip ? ` title="${esc(tip)}"` : ""}>${stat.proj.toFixed(1)}${moveTag(delta, key)}</div>
      <div class="line">${sub}</div>
    </div>
  </td>`;
}

/* Last 5 PRA under the player's name, oldest to newest (same idea as the
   K Board's last-5 strikeouts). With a PRA line, each game is green if it
   would have cleared tonight's line, red if not. Hover (or tap, on a
   phone) a number for that game's date and points/rebounds/assists. */
const shortDate = (d) => {
  const [y, m, day] = String(d).split("-").map(Number);
  return new Date(y, m - 1, day).toLocaleDateString([], { month: "short", day: "numeric" });
};

function last5(p) {
  const g = p.last5 || [];
  if (!g.length) return "";
  const pra = praOf(p);
  const line = pra && pra.line;
  const chips = g.map((x) => {
    const cls = line == null ? "" : x.pra > line ? " hit" : " miss";
    const tip = `${shortDate(x.date)} · ${x.pts} pts, ${x.reb} reb, ${x.ast} ast`;
    return `<span class="l5c${cls}" tabindex="0" data-tip="${esc(tip)}" aria-label="PRA ${x.pra}, ${esc(tip)}">${x.pra}</span>`;
  }).join("");
  return `<div class="l5"><span class="l5k">L5 PRA</span>${chips}</div>`;
}

function tableRow(p) {
  // injury status goes on the second line so long names don't widen the table
  const tag = p.status ? ` · <span class="status-tag">${esc(p.status)}</span>` : "";
  const nameCell = `<td><div class="pn">${esc(p.player)}</div>
    <div class="pm">${esc(p.team)} ${p.home ? "vs" : "@"} ${esc(p.opp)} · ${esc(p.time_et)}${tag}</div>${last5(p)}</td>`;
  const d = (p.since && p.since.delta) || {};
  const cells = STAT_COLS.map((c) => statCell(statOf(p, c.key), c.key === "pra" ? "pra-cell" : "",
    d[c.key], c.key)).join("");
  return `<tr${p.since ? ' class="moved"' : ""}>${nameCell}${cells}</tr>`;
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
      <span><span class="sw grad"></span>projection vs. line: below · even · above</span>
      <span><span class="sw box"></span>boxed = model edge vs. the price</span>
    </div>`
    : `<div class="legend"><span>${earlyView ? "Early look: model projections only. Lines arrive the morning of the games."
      : "Projections only: no book lines for this slate yet."}</span></div>`;
  return `${legend}
    <div class="board-tbl-wrap today-tbl-wrap"><div class="board-tbl today-tbl">
      <table class="t"><thead><tr>${head}</tr></thead><tbody>${rows}</tbody></table>
    </div></div>`;
}

/* Today vs. Early look (docs/data/tomorrow.json: the next game day's model
   projections, before lines exist). The Today board is the default; the
   early look opens first when today's board is stale or the offseason
   preview. */
const azToday = () => new Date().toLocaleDateString("en-CA", { timeZone: "America/Phoenix" });
const niceDate = (d) => {
  const [y, m, day] = String(d).split("-").map(Number);
  return new Date(y, m - 1, day).toLocaleDateString([], { weekday: "short", month: "short", day: "numeric" });
};

async function getJson(name) {
  try {
    return await (await fetch(`data/${name}?_=` + Date.now())).json();
  } catch {
    return null;
  }
}

async function main() {
  const [today, early] = await Promise.all([getJson("today.json"), getJson("tomorrow.json")]);
  if (!today && !early) {
    $("#board").innerHTML = '<div class="notice">No data yet.</div>';
    return;
  }
  const todayMock = !today || /MOCKUP/.test(today.note || "");   // pre-season preview board
  const earlyOk = early && early.players && early.players.length && early.date >= azToday()
    && (todayMock || early.date > today.date);
  const todayStale = todayMock || today.date < azToday();
  let view = earlyOk && todayStale ? "early" : "today";

  const render = () => {
    const data = view === "early" ? early : today;
    earlyView = view === "early";
    const todayChip = todayMock
      ? `<button class="chip" disabled title="Tonight's board starts on opening day">Today · starts opening day</button>`
      : `<button class="chip ${view === "today" ? "on" : ""}" data-view="today">Today · ${esc(niceDate(today.date))}</button>`;
    const chips = earlyOk
      ? `<div class="view-chips" style="margin-bottom:12px">
          ${todayChip}
          <button class="chip ${view === "early" ? "on" : ""}" data-view="early">Early look · ${esc(niceDate(early.date))}</button>
        </div>` : "";
    const upd = new Date(data.generated_at);
    $("#subtitle").textContent = `${data.date} · updated ${upd.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" })}`;
    const players = data.players || [];
    linedStats = new Set();
    players.forEach((p) => Object.entries(p.stats || {}).forEach(([k, v]) => { if (v && v.line != null) linedStats.add(k); }));
    const names = { points: "points", rebounds: "rebounds", assists: "assists", pra: "PRA" };
    const have = ["pra", "points", "rebounds", "assists"].filter((k) => linedStats.has(k));
    const lineNote = !earlyView && have.length && have.length < 4
      ? ` Lines tonight: ${have.map((k) => names[k]).join(" and ")} only. On big slates the odds budget covers fewer stats per game, so the other columns show projections without a line.`
      : "";
    $("#note").textContent = (data.note || "") + lineNote + " Tap a column header to sort.";
    const body = !players.length
      ? '<div class="notice">No players on the slate.</div>'
      : (view === "today" ? checkBanner(data.injury_check) : "") + tableView(players);
    $("#board").innerHTML = chips + body;
    $("#board").querySelectorAll("[data-view]").forEach((b) => b.addEventListener("click", () => {
      view = b.dataset.view;
      render();
    }));
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
