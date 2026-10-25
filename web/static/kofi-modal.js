/* Ko-fi tip panel in a BranchChat dialog (kofi-modal, Oct 21 2026).
   Every "Support BranchChat" link keeps its real href (middle-click, Ctrl/Cmd-click and no-JS still open Ko-fi);
   only plain left clicks are caught here. The iframe is created only when the dialog first opens.
   26.10.13.11: also in the Android / desktop apps (they used to skip it, so taps there opened ko-fi.com in the
   browser). In the apps, "Open on Ko-fi" and any pop-up Ko-fi's payment opens still go to the system browser. */
(function () {
  "use strict";
  var KOFI = "https://ko-fi.com/branchchat";
  var EMBED = "https://ko-fi.com/branchchat/?hidefeed=true&widget=true&embed=true&preview=true";
  var ua = navigator.userAgent || "";
  var r = document.documentElement;
  if (typeof HTMLDialogElement !== "function") return;
  var dlg = null;
  function build() {
    dlg = document.createElement("dialog");
    dlg.id = "kofi-dialog";
    dlg.setAttribute("aria-labelledby", "kofi-title");
    dlg.innerHTML =
      '<button type="button" class="x-close kofi-x" aria-label="Close"><svg viewBox="0 0 24 24" width="16" height="16" aria-hidden="true"><path d="M6 6l12 12M18 6L6 18" stroke="currentColor" stroke-width="2.2" stroke-linecap="round"/></svg></button>' +
      '<h2 id="kofi-title">Support <span class="kofi-nw">BranchChat.app <span aria-hidden="true">\u2764\uFE0F</span></span></h2>' +
      '<p class="kofi-sub">Optional tips help cover hosting. BranchChat stays free, with no ads.</p>' +
      '<div class="kofi-frame-wrap"><p class="kofi-loading">Loading Ko-fi\u2026</p></div>' +
      '<p class="kofi-foot">Payment not working here? <a href="' + KOFI + '" target="_blank" rel="noopener noreferrer" id="kofi-open">Open on Ko-fi</a></p>';
    document.body.appendChild(dlg);
    dlg.querySelector(".kofi-x").addEventListener("click", function () { dlg.close(); });
    dlg.addEventListener("click", function (e) {   // backdrop only
      var b = dlg.getBoundingClientRect();
      if (e.target === dlg && (e.clientX < b.left || e.clientX > b.right || e.clientY < b.top || e.clientY > b.bottom)) dlg.close();
    });
    dlg.addEventListener("keydown", function (e) {
      if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); dlg.close(); }
    });
  }
  function open() {
    if (!dlg) build();
    var wrap = dlg.querySelector(".kofi-frame-wrap");
    if (!wrap.querySelector("iframe")) {
      var f = document.createElement("iframe");
      f.id = "kofiframe";
      f.title = "Support BranchChat on Ko-fi";
      f.src = EMBED;
      f.height = "584";
      f.setAttribute("allow", "payment");
      f.addEventListener("load", function () { wrap.classList.add("loaded"); });
      wrap.appendChild(f);
    }
    if (!dlg.open) dlg.showModal();
    setTimeout(function () { var x = dlg.querySelector(".kofi-x"); if (x) x.focus(); }, 0);
  }
  document.addEventListener("click", function (e) {
    if (e.defaultPrevented || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
    var a = e.target.closest && e.target.closest("a[href]");
    if (!a || a.id === "kofi-open") return;
    if (!/^https:\/\/ko-fi\.com\/branchchat\/?$/.test(a.getAttribute("href"))) return;
    e.preventDefault();
    if (typeof a.blur === "function") a.blur();
    open();
  }, true);
  window.bcOpenKofi = open;
})();
