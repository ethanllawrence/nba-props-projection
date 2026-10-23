/* Jokic triple-double tracker — season counter + recent games + tonight's projection.
   Season summary and recent games are Jokic's real 2025-26 numbers (see docs/data/jokic.json's
   note). The call is his long-run triple-double rate against the Yes price;
   see nproj/model/predict.project_triple_double_prob for why. */
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

/* The headline: do we expect a triple-double tonight, yes or no. The model
   probability and market price are supporting detail underneath, not the
   lead — the call itself is what matters most here, not the edge math. */
const priceTxt = (q) => {
  const at = q.at ? new Date(q.at).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" }) : "";
  return `${q.odds > 0 ? "+" + q.odds : q.odds} ${esc(q.book)}${at ? ` <span class="dim">(as of ${esc(at)})</span>` : ""}`;
};

function tonightCall(t) {
  if (!t) return "";
  const p = t.td_projection;
  const mkt = t.market;
  // ?td=yes previews the YES celebration whatever tonight's call is
  const isYes = t.call === "yes" || new URLSearchParams(location.search).get("td") === "yes";
  const pct = Math.round(p.model_prob * 100);
  return `
    <div class="call-hero">
      ${isYes ? `<img class="td-yes-art" src="assets/jokic-td-yes.jpg?v=2"
        alt="Cartoon of Jokic dunking a flaming ball through a shattered backboard while the crowd goes wild">` : ""}
      <div class="call-meta">${t.opp
        ? `Nikola Jokic · DEN ${esc(t.opp)} · ${esc(t.time_et)}`
        : "Nikola Jokic · no game today · call for his next game"}</div>
      <div class="call-badge ${isYes ? "call-yes" : "call-no"}">Bet TD: ${isYes ? "YES" : "NO"}</div>
      <div class="call-prob">His chance: about <b>${pct}%</b>${p.break_even != null
        ? ` · price needs ${Math.round(p.break_even * 100)}%` : ""}</div>
      ${mkt ? `<div class="call-market">Best Yes price: ${priceTxt(mkt)}${(mkt.others || []).length
        ? ` · also ${mkt.others.map(priceTxt).join(" · ")}` : ""}</div>` : ""}
    </div>
    <div class="noedge" style="margin:-8px 0 16px">${esc(p.note)}</div>`;
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
  if (data.note) $("#note").textContent = data.note;
  const streak = currentStreak(data.recent_games || []);
  $("#jokic-body").innerHTML =
    tonightCall(data.tonight) +
    summaryTiles(data.season_summary, streak) +
    (data.season_summary.fun_fact
      ? `<div class="notice" style="margin:12px 0">${esc(data.season_summary.fun_fact)}</div>` : "") +
    recentGamesTable(data.recent_games || []);
}
main();
