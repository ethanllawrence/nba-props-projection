/* Jokic triple-double tracker — season counter + recent games + tonight's projection.
   Season summary and recent games are Jokic's real 2025-26 numbers (see docs/data/jokic.json's
   note). "Tonight" is a placeholder until a real TD model exists. */
const $ = (s, el = document) => el.querySelector(s);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

function currentStreak(games) {
  // recent_games is newest-first; a "streak" counts consecutive TDs from the most recent game.
  let n = 0;
  for (const g of games) {
    if (g.triple_double) n++;
    else break;
  }
  return n;
}

function summaryTiles(sum, streak) {
  return `
    <div class="tiles">
      <div class="tile"><div class="v">${sum.triple_doubles}</div><div class="k">Triple-doubles</div>
        <div class="s">${sum.games_played} games played</div></div>
      <div class="tile"><div class="v">${(sum.hit_rate * 100).toFixed(0)}%</div><div class="k">Hit rate</div>
        <div class="s">${sum.season_label}</div></div>
      <div class="tile"><div class="v">${sum.ppg}/${sum.rpg}/${sum.apg}</div><div class="k">PPG / RPG / APG</div>
        <div class="s">season averages</div></div>
      <div class="tile"><div class="v">${streak}</div><div class="k">Current streak</div>
        <div class="s">${streak > 0 ? "games in a row" : "snapped last game"}</div></div>
    </div>`;
}

function tonightCard(t) {
  if (!t) return "";
  const p = t.td_projection;
  const bk = t.book_line;
  return `
    <h2 class="sec">Tonight</h2>
    <div class="card">
      <div class="top">
        <div class="who">
          <div class="name">Nikola Jokic</div>
          <div class="meta">DEN ${esc(t.opp)} · ${esc(t.time_et)}</div>
        </div>
        <div class="pt"><div class="num">${Math.round(p.model_prob * 100)}%</div><div class="lbl">TD prob</div></div>
      </div>
      <div class="mkt">
        <span class="mkt-item">Triple-double <b>${bk.side === "yes" ? "Yes" : "No"}</b>
          <span class="dim">${bk.odds > 0 ? "+" + bk.odds : bk.odds} · ${esc(bk.book)}</span></span>
        <span class="mkt-src">${bk.books} book${bk.books === 1 ? "" : "s"}</span>
      </div>
      <div class="noedge">${esc(p.note)}</div>
    </div>`;
}

function gameRow(g) {
  const badge = g.triple_double
    ? '<span class="win win-go">TD</span>'
    : '<span class="win win-wait">—</span>';
  return `<tr>
    <td><div class="pn">${esc(g.date)}</div><div class="pm">${esc(g.opp)}</div></td>
    <td class="num dim">${g.pts}p · ${g.reb}r · ${g.ast}a</td>
    <td>${badge}</td>
  </tr>`;
}

function recentGamesTable(games) {
  const rows = games.map(gameRow).join("");
  return `
    <h2 class="sec">Recent games</h2>
    <div class="board-tbl-wrap"><div class="board-tbl compact-tbl">
      <table class="t">
        <thead><tr><th>Date</th><th class="num">Stat line</th><th>TD?</th></tr></thead>
        <tbody>${rows}</tbody>
      </table>
    </div></div>`;
}

async function main() {
  let data;
  try {
    data = await (await fetch("data/jokic.json?_=" + Date.now())).json();
  } catch {
    $("#jokic-body").innerHTML = '<div class="notice">No data yet.</div>';
    return;
  }
  const upd = new Date(data.generated_at);
  $("#subtitle").textContent = `updated ${upd.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" })}`;
  const streak = currentStreak(data.recent_games || []);
  $("#jokic-body").innerHTML =
    summaryTiles(data.season_summary, streak) +
    `<div class="notice" style="margin:12px 0">${esc(data.season_summary.fun_fact)}</div>` +
    tonightCard(data.tonight) +
    recentGamesTable(data.recent_games || []);
}
main();
