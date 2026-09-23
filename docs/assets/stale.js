/* Warns when the daily job has stopped updating the site. data/status.json
   is rewritten at least every 12 hours by every successful run, so anything
   older than 30 hours means runs are failing or GitHub Actions is paused. */
(async () => {
  try {
    const s = await (await fetch("data/status.json?_=" + Date.now())).json();
    const last = new Date(s.last_run);
    const hours = (Date.now() - last.getTime()) / 36e5;
    if (!(hours > 30)) return;
    const el = document.createElement("div");
    el.className = "stale-banner";
    el.textContent = `Heads up: this data hasn't refreshed since ${last.toLocaleString([], {
      month: "short", day: "numeric", hour: "numeric", minute: "2-digit" })}. The daily update ` +
      "job may be failing; check the Actions tab on GitHub.";
    const nav = document.querySelector("nav.tabs");
    nav.parentNode.insertBefore(el, nav.nextSibling);
  } catch { /* no status.json yet: say nothing */ }
})();
