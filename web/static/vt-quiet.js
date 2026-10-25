/* Cross-page view transitions (gate.css / dir.css opt in) can be skipped by Chromium when a page changes itself
   while it is being revealed; it then reports "Transition was aborted" as an unhandled error. The page is fine and
   the transition is only skipped: mark those promises as handled. */
addEventListener("pagereveal", (e) => {
  const t = e.viewTransition;
  if (!t) return;
  for (const p of [t.ready, t.finished, t.updateCallbackDone]) if (p && p.catch) p.catch(() => {});
});
