// Branch Chat download links on the sign-in page (BC_DOWNLOAD_LINKS=1 only).
// Progressive enhancement: the server already renders every platform link, and every
// "coming soon" link is a plain href="#signin". This file only (1) turns the generic
// "Choose your platform" button into the visitor's own platform and (2) makes #signin
// links scroll smoothly, focus Username and show a short note.

// Guess the visitor's platform (android, ios, windows, mac, linux) so the best download shows first.
export function detectOS(nav) {
  nav = nav || navigator;
  const ua = String(nav.userAgent || "");
  const uad = nav.userAgentData;
  const plat = String((uad && uad.platform) || "");
  if (/android/i.test(plat) || /Android/i.test(ua)) return "android";
  // iPhone/iPod/iPad, and iPadOS that asks for the desktop site (says "Macintosh" but has touch).
  if (/iPhone|iPad|iPod/.test(ua) || (/Macintosh/.test(ua) && (nav.maxTouchPoints || 0) > 1)) return "ios";
  if (/^windows/i.test(plat) || /Windows NT|Win64|Win32|WOW64/.test(ua)) return "windows";
  if (/^mac/i.test(plat) || /Macintosh|Mac OS X/.test(ua)) return "macos";
  if (/CrOS|chrome os/i.test(ua + " " + plat)) return "unknown";
  if (/^linux/i.test(plat) || /Linux|X11/.test(ua)) return "linux";
  return "unknown";
}

const reduced = () => { try { return matchMedia("(prefers-reduced-motion: reduce)").matches; } catch { return false; } };
let noteTimer = 0;

// "Use it in your browser": switch to Log in and scroll to it, with an optional note.
function goSignin(note) {
  if (document.documentElement.dataset.bcGet) { location.href = "/?login"; return; }   // /get: no sign-in column here
  const tab = document.getElementById("tab-login");
  const login = document.getElementById("login");
  if (login && login.hidden && tab) tab.click();  // public mode: switch to the Log in tab
  const fdLogin = document.getElementById("su-to-login");   // front door: "Already have an account? Log in"
  if (login && login.hidden && !tab && fdLogin) fdLogin.click();
  const wrap = document.getElementById("signin") || login;
  const user = document.getElementById("username");
  const box = document.getElementById("bc-dl-note");
  if (box) {
    box.textContent = note || ""; box.hidden = !note;
    clearTimeout(noteTimer); if (note) noteTimer = setTimeout(() => { box.hidden = true; }, 9000);
  }
  try { history.replaceState(null, "", "#signin"); } catch {}
  if (user) { try { user.focus({preventScroll: true}); } catch { user.focus(); } }
  if (wrap) wrap.scrollIntoView({behavior: reduced() ? "auto" : "smooth", block: "start"});
  if (login && !reduced()) { login.classList.remove("flash"); void login.offsetWidth; login.classList.add("flash"); }
}

// Fill the big main download button with the chosen platform's link, title and icon.
function fill(el, {href, title, sub, note, download, soon, icon, os}) {
  el.setAttribute("href", href);
  document.getElementById("dl-label").textContent = title;
  document.getElementById("dl-sub").textContent = sub;
  const use = el.querySelector("svg use"); if (use) use.setAttribute("href", "#" + icon);
  if (note) el.dataset.dlNote = note; else delete el.dataset.dlNote;
  if (download) el.setAttribute("download", download); else el.removeAttribute("download");
  if (/^https:/.test(href)) { el.rel = "noopener"; el.target = "_blank"; } else { el.removeAttribute("rel"); el.removeAttribute("target"); }
  el.classList.toggle("soon", !!soon);
  if (soon) el.setAttribute("aria-label", title + ". " + sub + ". Go to sign-in"); else el.removeAttribute("aria-label");
  el.dataset.os = os;
}

// Read one download tile's details from the page, and whether it actually works yet.
function item(id) {
  const a = document.querySelector('#bc-dl-list [data-dl="' + id + '"]');
  if (!a) return null;
  return {href: a.getAttribute("href"), live: a.getAttribute("href") !== "#signin" && !a.classList.contains("soon"), sub: a.querySelector("small").textContent,
          note: a.dataset.dlNote || "", download: a.getAttribute("download") || ""};
}

const NAMES = {windows: "Windows", macos: "Mac", linux: "Linux"};

// Pick the main download for this platform: Play or the APK on Android, the installer elsewhere, else the browser.
export function choosePrimary(os) {
  if (os === "android") {
    const play = item("play"), apk = item("apk");
    if (play && play.live) return {href: play.href, title: "Get it on Google Play", sub: "Android", icon: "i-play", os};
    if (apk && apk.live) return {href: apk.href, title: "Download for Android", sub: apk.sub.replace("APK beta", "APK, beta"), download: apk.download, icon: "i-android", os};
    return {href: "#signin", title: "Use BranchChat in your browser", sub: "Works now \u00b7 no install", note: "Sign in here to use BranchChat in your browser.", soon: false, icon: "i-globe", os};
  }
  if (os === "ios") {
    const st = item("appstore");
    if (st && st.live) return {href: st.href, title: "Download on the App Store", sub: "iPhone and iPad", icon: "i-phone", os};
    // 26.10.3: no "App Store, coming soon" banner; iPhone visitors are pointed at the browser, which works today.
    return {href: "#signin", title: "Use BranchChat in your browser", sub: "Tip: Share \u203a Add to Home Screen for an app icon",
            note: "Sign in here to use BranchChat in your browser.", soon: false, icon: "i-globe", os};
  }
  if (NAMES[os]) {
    const d = item(os);
    if (d && d.live) return {href: d.href, title: "Download for " + NAMES[os], sub: d.sub, download: d.download, icon: "i-down", os};
    return {href: "#signin", title: "Use BranchChat in your browser", sub: NAMES[os] + " app coming soon",
            note: "The " + (os === "macos" ? "macOS" : NAMES[os]) + " app is coming soon. Sign in here in your browser for now.", soon: false, icon: "i-globe", os};
  }
  return {href: "#bc-dl-list", title: "Choose your platform", sub: "Phone, tablet or computer", icon: "i-down", os: "unknown"};
}

// public.6: Linux format menu. Phone: bottom sheet over a backdrop. Desktop: inline panel under the tiles.
// Either way it is a modal dialog: Escape or a tap/click outside closes it, Tab stays inside, the page behind
// does not scroll while it is open, and focus goes back to the tile that opened it.
let lxOpener = null;
const FOCUSABLE = 'a[href], button:not([disabled]), summary, [tabindex]:not([tabindex="-1"])';

// Visible focusable elements in the Linux formats menu (for keeping Tab inside it).
function lxFocusables(panel) {
  return Array.from(panel.querySelectorAll(FOCUSABLE)).filter((el) => el.offsetParent !== null || el === document.activeElement);
}

// Open the Linux formats menu and remember what opened it.
export function openLinux(opener) {
  const panel = document.getElementById("bc-linux");
  const back = document.querySelector(".lx-backdrop");
  if (!panel) return;
  lxOpener = opener || null;
  panel.hidden = false; if (back) back.hidden = false;
  document.documentElement.classList.add("lx-open");
  if (opener) opener.setAttribute("aria-expanded", "true");
  if (!matchMedia("(max-width: 960px)").matches) panel.scrollIntoView({block: "nearest", behavior: reduced() ? "auto" : "smooth"});
  const close = panel.querySelector(".lx-close");
  try { (close || panel).focus({preventScroll: true}); } catch { (close || panel).focus(); }
}

// Close the Linux formats menu and, by default, put focus back where it was.
export function closeLinux(restore = true) {
  const panel = document.getElementById("bc-linux");
  const back = document.querySelector(".lx-backdrop");
  if (!panel || panel.hidden) return;
  panel.hidden = true; if (back) back.hidden = true;
  document.documentElement.classList.remove("lx-open");
  document.querySelectorAll('[aria-controls="bc-linux"]').forEach((el) => el.setAttribute("aria-expanded", "false"));
  if (restore && lxOpener) { try { lxOpener.focus({preventScroll: true}); } catch { lxOpener.focus(); } }
  lxOpener = null;
}

// Wire up the Linux formats menu: open/close buttons, Escape, clicks outside, Tab trapping and Copy buttons.
function initLinuxMenu() {
  const panel = document.getElementById("bc-linux");
  if (!panel) return;
  // /get page: the Linux formats are shown open, in the page (not a pop-up); the Linux buttons scroll to them.
  if (document.documentElement.dataset.bcGet) {
    panel.hidden = false; panel.classList.add("lx-inline");
    panel.removeAttribute("role"); panel.removeAttribute("aria-modal");
    document.addEventListener("click", (ev) => {
      const t = ev.target.closest && ev.target.closest('a[href="#bc-linux"]');
      if (!t || ev.button !== 0) return;
      ev.preventDefault(); panel.scrollIntoView({block: "start", behavior: reduced() ? "auto" : "smooth"});
    });
    if (location.hash === "#bc-linux") panel.scrollIntoView({block: "start"});
    initCopy(panel);
    return;
  }
  document.querySelectorAll('[aria-controls="bc-linux"]').forEach((el) => el.setAttribute("aria-expanded", "false"));
  document.addEventListener("click", (ev) => {
    const t = ev.target.closest && ev.target.closest('a[href="#bc-linux"]');
    if (!t || ev.button !== 0 || ev.metaKey || ev.ctrlKey || ev.shiftKey || ev.altKey) return;
    ev.preventDefault();
    if (!panel.hidden && lxOpener === t) { closeLinux(); return; }
    openLinux(t);
  });
  panel.querySelector(".lx-close").addEventListener("click", () => closeLinux());
  // Outside tap/click closes (the backdrop on phones, anywhere outside the panel on desktop).
  document.addEventListener("pointerdown", (ev) => {
    if (panel.hidden || panel.contains(ev.target)) return;
    if (ev.target.closest && ev.target.closest('a[href="#bc-linux"]')) return;   // the opener toggles itself
    closeLinux();
  }, true);
  document.addEventListener("keydown", (ev) => {
    if (panel.hidden) return;
    if (ev.key === "Escape") { ev.preventDefault(); closeLinux(); return; }
    if (ev.key !== "Tab") return;
    const f = lxFocusables(panel);
    if (!f.length) { ev.preventDefault(); return; }
    const first = f[0], last = f[f.length - 1];
    if (!panel.contains(document.activeElement)) { ev.preventDefault(); first.focus(); }
    else if (ev.shiftKey && document.activeElement === first) { ev.preventDefault(); last.focus(); }
    else if (!ev.shiftKey && document.activeElement === last) { ev.preventDefault(); first.focus(); }
  });
  panel.addEventListener("focusout", (ev) => {
    if (!panel.hidden && ev.relatedTarget && !panel.contains(ev.relatedTarget)) { const f = lxFocusables(panel); if (f[0]) f[0].focus(); }
  });
  // No scroll leaking: wheel/touch on the backdrop is swallowed; inside the panel only the panel scrolls.
  const back = document.querySelector(".lx-backdrop");
  if (back) { back.addEventListener("wheel", (e) => e.preventDefault(), {passive: false}); back.addEventListener("touchmove", (e) => e.preventDefault(), {passive: false}); }
  panel.addEventListener("wheel", (e) => {
    const el = panel; const up = e.deltaY < 0;
    if ((up && el.scrollTop <= 0) || (!up && el.scrollTop + el.clientHeight >= el.scrollHeight - 1)) e.preventDefault();
  }, {passive: false});
  initCopy(panel);
}

// Copy buttons on the Linux command boxes.
function initCopy(panel) {
  panel.querySelectorAll(".lx-copy").forEach((b) => b.addEventListener("click", async () => {
    const code = b.parentElement.querySelector("code"); const text = code ? code.textContent : "";
    let ok = false;
    try { await navigator.clipboard.writeText(text); ok = true; } catch {
      try { const r = document.createRange(); r.selectNodeContents(code); const sel = getSelection(); sel.removeAllRanges(); sel.addRange(r); ok = document.execCommand("copy"); } catch {}
    }
    b.textContent = ok ? "Copied" : "Select + copy"; setTimeout(() => { b.textContent = "Copy"; }, 1800);
  }));
}

// Page start: detect the platform, fill the main button, move this platform's tile first, set up the Linux menu.
function init() {
  const main = document.getElementById("bc-dl-main");
  const os = detectOS();
  document.documentElement.dataset.bcOs = os;
  if (main) fill(main, choosePrimary(os));
  // Green outline on the tile for this device (Android: the APK tile unless Play is live).
  const play = item("play");
  const mine = os === "android" ? (play && play.live ? "play" : "apk") : os === "ios" ? "appstore" : os;
  document.querySelectorAll("#bc-dl-list .plat").forEach((a) => a.classList.toggle("current", a.dataset.dl === mine));
  // public.5: the visitor's own platform goes first in the list when it is available (a "coming soon" tile stays
  // with the other coming-soon tiles, which the server already put last).
  const cur = document.querySelector("#bc-dl-list .plat.current:not(.soon)");
  const li = cur && cur.closest("li");
  if (li && li.parentElement && li.parentElement.firstElementChild !== li) li.parentElement.prepend(li);
  initLinuxMenu();
  document.addEventListener("click", (ev) => {
    const a = ev.target.closest && ev.target.closest('a[href="#signin"]');
    if (!a || ev.defaultPrevented || ev.button !== 0 || ev.metaKey || ev.ctrlKey || ev.shiftKey || ev.altKey) return;
    ev.preventDefault();
    if (a.closest("#bc-linux")) closeLinux(false);
    goSignin(a.dataset.dlNote || "");
  });
}

if (typeof document !== "undefined") init();
