// "Host your own Branch Chat" pop-up under the app downloads (public server only). Without JS the link opens /host.
// Closes on tap outside, Escape or X; the page behind does not scroll; Tab stays inside the pop-up.
const dlg = document.getElementById("bc-host-dialog");
const opener = document.getElementById("bc-host-open");
let lastFocus = null;
const FOCUSABLE = 'a[href], button:not([disabled]), input:not([disabled]), [tabindex]:not([tabindex="-1"])';
// Visible focusable elements in the Host your own dialog.
function focusables() { return [...dlg.querySelectorAll(FOCUSABLE)].filter((el) => el.offsetParent !== null || el === document.activeElement); }
// Stop the page behind the dialog from scrolling while it is open.
function lock(on) {
  document.documentElement.classList.toggle("bc-host-lock", on);
}
// Open the Host your own dialog instead of following the link (old browsers just follow it).
function open(ev) {
  if (!dlg || typeof dlg.showModal !== "function") return; // old browser: follow the link to /host
  if (ev) ev.preventDefault();
  lastFocus = document.activeElement;
  dlg.showModal();
  lock(true);
  const first = document.getElementById("bc-host-close");
  if (first) first.focus();
}
// Close the Host your own dialog.
function close() { if (dlg && dlg.open) dlg.close(); }
if (dlg && opener) {
  opener.addEventListener("click", open);
  document.getElementById("bc-host-close").addEventListener("click", close);
  dlg.addEventListener("close", () => { lock(false); if (lastFocus && lastFocus.focus) lastFocus.focus(); });
  dlg.addEventListener("cancel", () => lock(false)); // Escape
  // Tap outside: the click lands on the <dialog> itself (its backdrop), not on anything inside it.
  dlg.addEventListener("click", (ev) => {
    if (ev.target !== dlg) return;
    const r = dlg.getBoundingClientRect();
    const inside = ev.clientX >= r.left && ev.clientX <= r.right && ev.clientY >= r.top && ev.clientY <= r.bottom;
    if (!inside) close();
  });
  dlg.addEventListener("keydown", (ev) => {
    if (ev.key !== "Tab") return;
    const items = focusables();
    if (!items.length) return;
    const first = items[0], last = items[items.length - 1];
    if (ev.shiftKey && (document.activeElement === first || !dlg.contains(document.activeElement))) { ev.preventDefault(); last.focus(); }
    else if (!ev.shiftKey && (document.activeElement === last || !dlg.contains(document.activeElement))) { ev.preventDefault(); first.focus(); }
  });
  // Scroll chaining: wheel/touch on the backdrop must not move the page.
  for (const type of ["wheel", "touchmove"]) {
    dlg.addEventListener(type, (ev) => {
      const body = dlg.querySelector(".bc-host-dbody");
      if (!body || !body.contains(ev.target)) { ev.preventDefault(); return; }
      if (type === "wheel") {
        const atTop = body.scrollTop <= 0 && ev.deltaY < 0;
        const atEnd = body.scrollTop + body.clientHeight >= body.scrollHeight - 1 && ev.deltaY > 0;
        if (atTop || atEnd) ev.preventDefault();
      }
    }, {passive: false});
  }
  if (location.hash === "#host") open();
}
