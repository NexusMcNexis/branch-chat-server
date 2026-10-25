/* Branch Chat public-instance features (v0.22.8-public.4).
   Only served when the server runs with PUBLIC_MODE=1 (or BC_LANGUAGE_FILTER / BC_REPORT_BLOCK /
   BC_FULL_ACCOUNT_DELETE). The private server never sends this file, so nothing here runs there.
   app.js calls window.bcHooks.* at a few points (init, sync, text, hide, menu, threadHead, stamp);
   with no hooks those calls do nothing.

   - Mature language filter: masks words on this device only. Messages are end-to-end encrypted,
     so the server never sees the text and cannot filter it.
   - Report and block: from the message menu (Lobby, groups and DMs) and the person's chat header.
   - Group safety (26.10.2): in Group info, report the group or one member, block a member, or leave and
     block the group (nobody can add you back). Group names and messages are end-to-end encrypted, so the
     server can't filter them; reports are how admins hear about a bad group, and the owner can close it.
     A report includes the message text only when you tick "Include the message text"; the server
     cannot read the message itself.
   - Account deletion: confirm dialog, password, plus the authenticator code when 2FA is on.
   - Locked mature-language filter (BC_LANGUAGE_LOCK): the setting stays visible but greyed out and on; the
     server refuses "off". Masking uses the shared filter (contentfilter.js) everywhere: Lobby, DMs, previews.
   - Lobby link filter (BC_LOBBY_LINK_FILTER): "Links aren't allowed in the Lobby" before sending; the server
     re-checks every Lobby message (the per-message key goes with it) and is the one that decides. */
import * as CF from "./contentfilter.js?v=2";
const FEATS = new Set((document.documentElement.dataset.bcPublic || "").split(/\s+/).filter(Boolean));
const $ = (id) => document.getElementById(id);
let ctx = null;
let pub = {};
let filterOn = false;
let filterTouched = 0;
let blocked = new Set();
let blockedGroups = new Set();  // 26.10.2: ids of groups this account left and blocked (from /api/sync)
let stamp = "";
const LOCKED = FEATS.has("filter-locked");
if (LOCKED) filterOn = true;
let cfReady = false;
if (FEATS.has("filter") || FEATS.has("lobby-check")) {
  CF.ready("/static/filter-data.json?v=2").then(() => { cfReady = true; refresh(); }).catch(() => {});
}

/* ---------- mature language filter ---------- */
// Whole words only (with common endings), so "class", "Scunthorpe" or "assess" are left alone.
const WORDS = [
  "fuck", "fucks", "fucked", "fucker", "fuckers", "fucking", "fuckin", "fuckhead", "fuckface", "motherfucker", "motherfuckers", "motherfucking",
  "fuk", "fuks", "fukin", "fucken", "fck", "fcking", "fuq", "effing", "wtf", "stfu", "gtfo", "mf",
  "shit", "shits", "shitty", "shitting", "shitted", "shithead", "shitheads", "shithole", "bullshit", "horseshit", "dipshit", "sh1t", "shite",
  "bitch", "bitches", "bitching", "bitchy", "biatch", "b1tch", "sonofabitch",
  "bastard", "bastards", "asshole", "assholes", "arsehole", "ass", "asses", "arse", "jackass", "dumbass", "smartass", "badass", "fatass", "asshat", "a55",
  "dick", "dicks", "dickhead", "dickheads", "cock", "cocks", "cocksucker", "cocksuckers", "prick", "pricks", "knobhead",
  "cunt", "cunts", "twat", "twats", "pussy", "pussies", "wanker", "wankers", "wank", "tosser", "bollocks",
  "whore", "whores", "slut", "sluts", "slutty", "skank", "hoe", "hoes", "thot",
  "cum", "cumming", "jizz", "dildo", "dildos", "blowjob", "blowjobs", "handjob", "handjobs", "porn", "porno", "boobs", "tits", "titties", "milf", "nudes",
  "goddamn", "goddamnit", "damnit", "piss", "pissed", "pissing",
  "nigger", "niggers", "nigga", "niggas", "n1gger", "n1gga", "faggot", "faggots", "fag", "fags", "f4g", "retard", "retards", "retarded",
  "spic", "spics", "chink", "chinks", "kike", "kikes", "tranny", "trannies", "dyke", "dykes", "wetback", "wetbacks", "gook", "gooks", "raghead",
  "kys",
];
const WORD_RE = new RegExp("(^|[^\\p{L}\\p{N}_])(" + WORDS.map((w) => w.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")).sort((a, b) => b.length - a.length).join("|") + ")(?=$|[^\\p{L}\\p{N}_])", "giu");
// Mask swear words and slurs in text, using the shared filter list once it has loaded.
export function maskText(text) {
  if (typeof text !== "string" || !text) return text;
  if (cfReady) return CF.maskWords(text)[0];  // shared, heftier list + normalization (same rules as the server)
  return text.replace(WORD_RE, (all, lead, word) => lead + word[0] + "\u2217".repeat(Math.max(2, word.length - 1)));
}

/* ---------- helpers ---------- */
function el(tag, attrs, ...kids) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === false || v == null) continue;
    if (k === "class") node.className = v;
    else if (k === "text") node.textContent = v;
    else if (k.startsWith("on")) node.addEventListener(k.slice(2), v);
    else node.setAttribute(k, v === true ? "" : v);
  }
  for (const kid of kids) if (kid != null) node.append(kid);
  return node;
}
// The signed-in account's own info from app.js.
function me() { const s = ctx && ctx.state(); return (s && s.me) || {}; }
// Someone's display name (falls back to their username).
function dn(name) { return (ctx && ctx.dn) ? ctx.dn(name) : name; }
let toastTimer = 0;
// A short message at the bottom of the screen that hides itself after a few seconds.
function toast(text) {
  const t = $("toast");
  if (!t) return;
  t.textContent = text;
  t.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { t.hidden = true; }, 3200);
}
// Ask app.js to redraw messages when filter or block settings changed (stamp tells it what changed).
function refresh() {
  stamp = [filterOn ? "f" : "", cfReady ? "c" : "", [...blocked].sort().join(",")].join("|");
  if (ctx) ctx.rerender();
}
// Replace the list of blocked people and redraw.
function setBlocked(list) {
  blocked = new Set(Array.isArray(list) ? list : []);
  paintBlockedList();
  refresh();
}

/* ---------- dialogs ---------- */
const AUTO_LABEL = ["filter", "Automatic filter"];
const REASONS = [["spam", "Spam or scam"], ["harassment", "Harassment or bullying"], ["hate", "Hate speech"], ["sexual", "Sexual content"], ["violence", "Violence or threats"], ["self-harm", "Self-harm"], ["other", "Something else"]];
// The Report dialog, built once and reused.
function reportDialog() {
  let d = $("pub-report-dialog");
  if (d) return d;
  d = el("dialog", {id: "pub-report-dialog", "aria-labelledby": "pub-report-title"},
    el("h2", {id: "pub-report-title", text: "Report"}),
    el("p", {id: "pub-report-lead", class: "sub"}),
    el("fieldset", {id: "pub-report-reasons", class: "pub-reasons"}, el("legend", {text: "What is wrong?"}),
      ...REASONS.map(([value, label]) => el("label", {class: "pub-radio"}, el("input", {type: "radio", name: "pub-reason", value}), el("span", {text: label})))),
    el("div", {id: "pub-report-msgbox", class: "pub-msgbox", hidden: true},
      el("label", {class: "check-row pub-check"}, el("input", {id: "pub-report-include", type: "checkbox", checked: true}), el("span", {text: "Include the message text"})),
      el("blockquote", {id: "pub-report-quote", class: "pub-quote"}),
      el("p", {class: "sub pub-small", text: "Messages are end-to-end encrypted. The admins can only read this message if you include it. They will see that it came from you."})),
    el("label", {for: "pub-report-note", text: "Anything else? (optional)"}),
    el("textarea", {id: "pub-report-note", maxlength: "500", rows: "3"}),
    el("label", {class: "check-row pub-check"}, el("input", {id: "pub-report-block", type: "checkbox"}), el("span", {id: "pub-report-block-label", text: "Also block this person"})),
    el("p", {id: "pub-report-note-out", class: "sub", role: "status"}),
    el("div", {class: "actions"},
      el("button", {id: "pub-report-cancel", class: "ghost", type: "button", text: "Cancel", onclick: () => d.close()}),
      el("button", {id: "pub-report-go", class: "danger", type: "button", text: "Send report"})));
  document.body.append(d);
  return d;
}
// openReport(username, msg, text, gid)
//   username: who is reported ("" for a whole-group report)
//   msg/text: the message and its decrypted text when reporting one message (text is sent only if ticked)
//   gid:      26.10.2, the group the report is about. Group messages carry it in msg.g.
// For a group report the name this browser decrypted is sent too (the server can't read it), and the
// tick box becomes "Also leave and block this group" when there is no one person to block.
function openReport(username, msg, text, gid) {
  const d = reportDialog();
  const group = gid || (msg && msg.box === "group" ? msg.g : "");
  const wholeGroup = !!group && !username;
  const gname = group && ctx.groupName ? ctx.groupName(group) : "";
  $("pub-report-title").textContent = msg ? "Report this message" : wholeGroup ? "Report the group " + gname : "Report " + dn(username);
  $("pub-report-lead").textContent = msg
    ? "From " + dn(username) + (group ? " in " + gname : "") + ". The admins of this BranchChat review reports."
    : wholeGroup ? "The admins of this BranchChat review reports. They will see the group's name and who reported it, not its messages."
    : "The admins of this BranchChat review reports. Pick what is wrong.";
  for (const r of d.querySelectorAll('input[name="pub-reason"]')) r.checked = false;
  $("pub-report-note").value = "";
  $("pub-report-block").checked = false;
  if (wholeGroup) {
    $("pub-report-block-label").textContent = "Also leave and block this group";
    $("pub-report-block").disabled = false;
  } else {
    $("pub-report-block-label").textContent = blocked.has(username) ? "Already blocked" : "Also block " + dn(username);
    $("pub-report-block").disabled = blocked.has(username);
  }
  $("pub-report-note-out").textContent = "";
  const hasText = !!(msg && text);
  $("pub-report-msgbox").hidden = !hasText;
  $("pub-report-include").checked = true;
  $("pub-report-quote").textContent = hasText ? (text.length > 400 ? text.slice(0, 400) + "\u2026" : text) : "";
  const go = $("pub-report-go");
  go.disabled = false;
  go.onclick = async () => {
    const reason = (d.querySelector('input[name="pub-reason"]:checked') || {}).value;
    if (!reason) { $("pub-report-note-out").textContent = "Pick what is wrong first."; return; }
    go.disabled = true;
    try {
      const ticked = $("pub-report-block").checked;
      const body = {reason, note: $("pub-report-note").value.trim()};
      if (username) { body.username = username; body.block = ticked && !blocked.has(username); }
      if (group) { body.group = group; body.group_name = gname.slice(0, 80); }
      if (wholeGroup) body.leave = ticked;  // 26.10.2: report + leave + block the group in one step
      if (msg) body.message_id = msg.id;
      if (hasText && $("pub-report-include").checked) body.text = text.slice(0, 2000);
      const data = await ctx.post("/api/report", body);
      d.close();
      if (data.blocked) { blocked.add(username); setBlocked([...blocked]); }
      if (data.left) await afterGroupBlock(group, gname);
      toast(data.left ? "Report sent. You left and blocked the group." : data.blocked ? "Report sent and " + dn(username) + " is blocked." : "Report sent. Thank you.");
    } catch (err) {
      $("pub-report-note-out").textContent = err.message || "The report was not sent.";
      go.disabled = false;
    }
  };
  d.showModal();
}
// The Block person confirm dialog, built once and reused.
function blockDialog() {
  let d = $("pub-block-dialog");
  if (d) return d;
  d = el("dialog", {id: "pub-block-dialog", "aria-labelledby": "pub-block-title"},
    el("h2", {id: "pub-block-title"}),
    el("ul", {class: "pub-list"},
      el("li", {text: "You stop seeing their messages in the Lobby, in groups and in direct messages."}),
      el("li", {text: "They can't message you, send you a friend request or add you to a group, and you are no longer friends."}),
      el("li", {text: "They are not told. You can unblock them in Profile settings \u2192 Safety."})),
    el("p", {id: "pub-block-out", class: "sub", role: "status"}),
    el("div", {class: "actions"},
      el("button", {class: "ghost", type: "button", text: "Cancel", onclick: () => d.close()}),
      el("button", {id: "pub-block-go", class: "danger", type: "button", text: "Block"})));
  document.body.append(d);
  return d;
}
// Ask to confirm, then block this person.
function openBlock(username) {
  const d = blockDialog();
  $("pub-block-title").textContent = "Block " + dn(username) + "?";
  $("pub-block-out").textContent = "";
  const go = $("pub-block-go");
  go.disabled = false;
  go.onclick = async () => {
    go.disabled = true;
    try {
      const data = await ctx.post("/api/block", {username});
      d.close();
      setBlocked(data.blocked);
      toast(dn(username) + " is blocked.");
    } catch (err) {
      $("pub-block-out").textContent = err.message || "That did not work.";
      go.disabled = false;
    }
  };
  d.showModal();
}
// personDialog(username, gid): Report / Block one person. gid (26.10.2) attaches the group they were
// reported from, so the admin sees where it happened.
function personDialog(username, gid) {
  let d = $("pub-person-dialog");
  if (!d) {
    d = el("dialog", {id: "pub-person-dialog", "aria-labelledby": "pub-person-title"},
      el("h2", {id: "pub-person-title"}),
      el("p", {class: "sub", text: "Report this person to the admins, or block them so they can't reach you."}),
      el("div", {class: "pub-person-actions"},
        el("button", {id: "pub-person-report", class: "ghost pub-wide", type: "button"}),
        el("button", {id: "pub-person-block", class: "danger pub-wide", type: "button"})),
      el("div", {class: "actions"}, el("button", {class: "ghost", type: "button", text: "Close", onclick: () => d.close()})));
    document.body.append(d);
  }
  $("pub-person-title").textContent = dn(username);
  $("pub-person-report").textContent = "Report " + dn(username);
  $("pub-person-block").textContent = blocked.has(username) ? "Unblock " + dn(username) : "Block " + dn(username);
  $("pub-person-report").onclick = () => { d.close(); openReport(username, null, "", gid || ""); };
  $("pub-person-block").onclick = async () => {
    d.close();
    if (blocked.has(username)) await unblock(username);
    else openBlock(username);
  };
  d.showModal();
}
// Unblock someone (friendship isn't restored; they need a new friend request).
async function unblock(username) {
  try {
    const data = await ctx.post("/api/unblock", {username});
    setBlocked(data.blocked);
    toast(dn(username) + " is unblocked. Send a friend request to chat again. Older messages show after a reload.");
  } catch (err) { toast(err.message || "That did not work."); }
}

/* ---------- 26.10.2 group safety ---------- */
// Remember group names this browser decrypted, so the Blocked groups list can still show a name after
// leaving (the server only knows the group id; names are end-to-end encrypted).
const GNAMES_KEY = "bc-blocked-group-names";
// Group names saved on this device for groups you left and blocked.
function groupNames() { try { return JSON.parse(localStorage.getItem(GNAMES_KEY) || "{}") || {}; } catch (e) { return {}; } }
// Save a blocked group's name on this device.
function rememberGroupName(gid, name) {
  try { const m = groupNames(); m[gid] = name || "Group"; localStorage.setItem(GNAMES_KEY, JSON.stringify(m)); } catch (e) {}
}
// After leaving + blocking a group: note it locally, close Group info and drop the group from the chat list.
async function afterGroupBlock(gid, name) {
  rememberGroupName(gid, name);
  blockedGroups.add(gid);
  paintBlockedGroups();
  if (ctx.leftGroup) await ctx.leftGroup(gid);
}
// "Leave and block" confirm dialog (no report). Blocking means nobody can add you back and invite
// links to this group stop working for you, until you unblock it in Profile settings > Safety.
function leaveBlockDialog() {
  let d = $("pub-gblock-dialog");
  if (d) return d;
  d = el("dialog", {id: "pub-gblock-dialog", "aria-labelledby": "pub-gblock-title"},
    el("h2", {id: "pub-gblock-title"}),
    el("ul", {class: "pub-list"},
      el("li", {text: "You leave the group and stop getting its messages."}),
      el("li", {text: "Nobody can add you back, and invite links to it won't work for you."}),
      el("li", {text: "The group is not told you blocked it, only that you left. You can unblock it in Profile settings \u2192 Safety."})),
    el("p", {id: "pub-gblock-out", class: "sub", role: "status"}),
    el("div", {class: "actions"},
      el("button", {class: "ghost", type: "button", text: "Cancel", onclick: () => d.close()}),
      el("button", {id: "pub-gblock-go", class: "danger", type: "button", text: "Leave and block"})));
  document.body.append(d);
  return d;
}
// Open the Leave and block confirm for a group.
function openLeaveBlock(gid) {
  const d = leaveBlockDialog();
  const name = ctx.groupName ? ctx.groupName(gid) : "this group";
  $("pub-gblock-title").textContent = "Leave and block " + name + "?";
  $("pub-gblock-out").textContent = "";
  const go = $("pub-gblock-go");
  go.disabled = false;
  go.onclick = async () => {
    go.disabled = true;
    try {
      await ctx.post("/api/groups/leave-block", {id: gid});
      d.close();
      await afterGroupBlock(gid, name);
      toast("You left and blocked " + name + ".");
    } catch (err) {
      $("pub-gblock-out").textContent = err.message || "That did not work.";
      go.disabled = false;
    }
  };
  d.showModal();
}
// Group info hook (app.js calls it every time the dialog repaints): a small shield on each other member's
// row (Report / Block that person, with the group attached to the report), and a row with
// "Report group" and "Leave and block" above the dialog's buttons.
function onGroupInfo(g, list) {
  if (!FEATS.has("report") || !g || !list) return;
  for (const row of list.querySelectorAll(".ginfo-row[data-user]")) {
    const user = row.dataset.user;
    if (!user || user === me().username || row.querySelector(".pub-gsafety")) continue;
    const b = el("button", {type: "button", class: "ghost small pub-gsafety", "aria-label": "Report or block " + dn(user), title: "Report or block"});
    b.innerHTML = '<svg viewBox="0 0 24 24" width="18" height="18" aria-hidden="true"><path fill="currentColor" d="M12 2 4 5v6c0 5 3.4 9.4 8 11 4.6-1.6 8-6 8-11V5l-8-3zm0 2.2 6 2.2V11c0 4-2.6 7.6-6 8.9-3.4-1.3-6-4.9-6-8.9V6.4l6-2.2zM11 7h2v6h-2V7zm0 8h2v2h-2v-2z"/></svg>';
    b.addEventListener("click", () => { $("ginfo-dialog").close(); personDialog(user, g.id); });
    row.append(b);
  }
  let bar = $("pub-gsafety-bar");
  if (!bar) {
    bar = el("div", {id: "pub-gsafety-bar", class: "pub-gsafety-bar"},
      el("button", {id: "pub-greport", class: "ghost small", type: "button", text: "Report group"}),
      el("button", {id: "pub-gleaveblock", class: "ghost small pub-danger-text", type: "button", text: "Leave and block"}));
    const note = $("ginfo-note");
    note.before(bar);
  }
  $("pub-greport").onclick = () => { $("ginfo-dialog").close(); openReport("", null, "", g.id); };
  $("pub-gleaveblock").onclick = () => { $("ginfo-dialog").close(); openLeaveBlock(g.id); };
}
// Profile settings > Safety > Blocked groups: list with Unblock (lets you be added or join by link again).
function paintBlockedGroups() {
  const list = $("pub-blocked-groups");
  if (!list) return;
  list.replaceChildren();
  if (!blockedGroups.size) { list.append(el("li", {class: "sub", text: "No groups are blocked."})); return; }
  const names = groupNames();
  for (const gid of blockedGroups) {
    list.append(el("li", {}, el("span", {text: names[gid] || "A group you left"}),
      el("button", {class: "ghost", type: "button", text: "Unblock", onclick: async () => {
        try {
          const data = await ctx.post("/api/groups/unblock", {id: gid});
          blockedGroups = new Set(data.blocked_groups || []);
          paintBlockedGroups();
          toast("Unblocked. Someone in the group can add you again.");
        } catch (err) { toast(err.message || "That did not work."); }
      }})));
  }
}

/* ---------- message menu ---------- */
function menuButtons() {
  if ($("react-report")) return;
  const anchor = $("react-delete");
  if (!anchor) return;
  const report = el("button", {id: "react-report", type: "button", hidden: true, text: "Report message"});
  const blk = el("button", {id: "react-block", type: "button", hidden: true, text: "Block"});
  anchor.after(report, blk);
}
// Message menu hook: add Report message and Block for other people's messages (Lobby, groups, DMs).
function onMenu(msg, fp) {
  if (!FEATS.has("report")) return;
  menuButtons();
  const mine = !msg || msg.sender === me().username || !msg.sender;
  const report = $("react-report"), blk = $("react-block");
  if (!report || !blk) return;
  report.hidden = mine;
  blk.hidden = mine || blocked.has(msg.sender);
  if (mine) return;
  blk.textContent = "Block " + dn(msg.sender);
  const text = (fp && typeof fp.text === "string") ? fp.text : "";
  report.onclick = (ev) => { ev.stopPropagation(); ctx.closeReact(); openReport(msg.sender, msg, text); };
  blk.onclick = (ev) => { ev.stopPropagation(); ctx.closeReact(); openBlock(msg.sender); };
}
// Direct-message header hook: a shield button that opens Report / Block for that person.
function onThreadHead(peer, actions) {
  if (!FEATS.has("report") || !peer || !actions) return;
  const b = el("button", {type: "button", class: "ghost icon pub-safety-btn", "aria-label": "Report or block " + peer, title: "Report or block"});
  b.innerHTML = '<svg viewBox="0 0 24 24" width="22" height="22" aria-hidden="true"><path fill="currentColor" d="M12 2 4 5v6c0 5 3.4 9.4 8 11 4.6-1.6 8-6 8-11V5l-8-3zm0 2.2 6 2.2V11c0 4-2.6 7.6-6 8.9-3.4-1.3-6-4.9-6-8.9V6.4l6-2.2zM11 7h2v6h-2V7zm0 8h2v2h-2v-2z"/></svg>';
  b.addEventListener("click", () => personDialog(peer));
  actions.prepend(b);
}

/* ---------- Profile settings: Safety, Reports, Delete ---------- */
function safetyFold() {
  if ($("pub-safety-fold")) return;
  const before = $("delete-fold");
  if (!before) return;
  const fold = el("details", {id: "pub-safety-fold", class: "fold"}, el("summary", {text: "Safety"}));
  if (FEATS.has("filter") && LOCKED) {
    // Locked on for the public server: visible, greyed out, not a control you can use (and the server refuses "off").
    const box = el("input", {id: "pub-filter", type: "checkbox", checked: true, disabled: true, "aria-disabled": "true", tabindex: "-1",
      "aria-describedby": "pub-filter-lock pub-filter-note"});
    box.checked = true;
    box.addEventListener("click", (ev) => { ev.preventDefault(); box.checked = true; });
    fold.append(el("label", {class: "check-row pub-check pub-locked", "aria-disabled": "true"}, box,
        el("span", {text: "Hide mature language"}),
        el("span", {id: "pub-filter-lock", class: "pub-lock-tag"}, lockIcon(), el("span", {text: "Always on for the public server"}))),
      el("p", {id: "pub-filter-note", class: "sub", text: "Swear words, slurs and sexual terms are masked everywhere on this server: in the Lobby by the server, and in direct messages on your device."}));
  } else if (FEATS.has("filter")) {
    const box = el("input", {id: "pub-filter", type: "checkbox"});
    box.checked = filterOn;
    box.addEventListener("change", async () => {
      const on = box.checked;
      filterOn = on; filterTouched = Date.now(); refresh();
      try { await ctx.post("/api/account/filter", {on}); $("pub-filter-note").textContent = on ? "Mature language is hidden on this account." : "Mature language is shown."; }
      catch (err) { $("pub-filter-note").textContent = err.message || "That setting was not saved."; }
    });
    fold.append(el("label", {class: "check-row pub-check"}, box, el("span", {text: "Hide mature language"})),
      el("p", {id: "pub-filter-note", class: "sub", text: "Swear words and slurs are masked in messages and previews. This happens on your device; nobody else's view changes."}));
  }
  if (FEATS.has("report")) {
    fold.append(el("h4", {class: "pub-h", text: "Blocked people"}), el("ul", {id: "pub-blocked", class: "pub-blocked"}),
      el("p", {class: "sub", text: "To report or block someone, tap one of their messages, or the shield in your chat with them."}),
      // 26.10.2: groups you used "Leave and block" on.
      el("h4", {class: "pub-h", text: "Blocked groups"}), el("ul", {id: "pub-blocked-groups", class: "pub-blocked"}),
      el("p", {class: "sub", text: "To report or leave and block a group, open the group and tap its name (Group info)."}));
  }
  before.before(fold);
  paintBlockedList();
  paintBlockedGroups();
}
// Small padlock icon for the locked mature-language setting.
function lockIcon() {
  const s = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  s.setAttribute("viewBox", "0 0 24 24"); s.setAttribute("aria-hidden", "true"); s.setAttribute("focusable", "false"); s.setAttribute("class", "pub-lock-ico");
  s.innerHTML = '<rect x="5" y="10.5" width="14" height="10" rx="2"/><path d="M8 10.5V7.5a4 4 0 0 1 8 0v3"/>';
  return s;
}
// Draw the Blocked people list in Profile settings > Safety.
function paintBlockedList() {
  const list = $("pub-blocked");
  if (!list) return;
  list.replaceChildren();
  if (!blocked.size) { list.append(el("li", {class: "sub", text: "Nobody is blocked."})); return; }
  for (const name of [...blocked].sort((a, b) => a.localeCompare(b))) {
    list.append(el("li", {}, el("span", {text: dn(name)}), el("button", {class: "ghost", type: "button", text: "Unblock", onclick: () => unblock(name)})));
  }
}
// 26.10.2: reports are no longer in the profile pop-up. The owner reviews them (group reports, Close group,
// Lift Lobby mute, Disable account) on the Administrator page's Reports section. This only removes the old section
// if an earlier page version left it in the open pop-up.
function reportsFold() {
  const fold = $("pub-reports-fold");
  if (fold) fold.remove();
}
let deleteConfirmed = false;
// Account deletion: full-deletion wording and the authenticator code field when 2FA is on.
function deleteBits() {
  if (!FEATS.has("delete")) return;
  const fold = $("delete-fold");
  if (!fold) return;
  const lead = fold.querySelector("p");
  if (lead && !lead.dataset.pub) {
    lead.dataset.pub = "1";
    lead.textContent = "This permanently deletes your account: your login on every device, your profile, friends and blocks, alert subscriptions, every message you sent (in direct messages and the Lobby) and every photo or file you uploaded. Copies already on other people's devices can't be reached. This can't be undone.";
  }
  if (!$("delete-factor")) {
    const label = el("label", {for: "delete-factor", id: "delete-factor-label", text: "Authenticator or backup code"});
    const input = el("input", {id: "delete-factor", autocomplete: "one-time-code", maxlength: "16", inputmode: "text"});
    const name = $("delete-name");
    if (name) { name.after(label, input); }
  }
  const on = !!me().totp;
  $("delete-factor").hidden = !on;
  $("delete-factor-label").hidden = !on;
}
// The final "Delete your account forever?" confirm dialog.
function deleteConfirmDialog() {
  let d = $("pub-delete-dialog");
  if (d) return d;
  d = el("dialog", {id: "pub-delete-dialog", "aria-labelledby": "pub-delete-title"},
    el("h2", {id: "pub-delete-title", text: "Delete your account forever?"}),
    el("p", {text: "Your account, every message you sent and every file you uploaded will be deleted for everyone. You will be signed out on all devices. This can't be undone."}),
    el("div", {class: "actions"},
      el("button", {id: "pub-delete-keep", class: "ghost", type: "button", text: "Keep my account", onclick: () => d.close()}),
      el("button", {id: "pub-delete-go", class: "danger", type: "button", text: "Delete forever", onclick: () => {
        d.close(); deleteConfirmed = true;
        try { $("delete-account").click(); } finally { deleteConfirmed = false; }
      }})));
  // Inside the settings popup, so a tap in the dialog doesn't count as a tap outside it (which would close Settings).
  ($("profile-pop") || document.body).append(d);
  return d;
}
document.addEventListener("click", (ev) => {
  if (!FEATS.has("delete") || deleteConfirmed) return;
  const t = ev.target instanceof Element ? ev.target.closest("#delete-account") : null;
  if (!t) return;
  const pw = $("delete-password"), nm = $("delete-name");
  if (!pw || !pw.value || !nm || !nm.value.trim()) return; // let the normal check say what is missing
  if (me().totp && $("delete-factor") && !$("delete-factor").value.trim()) {
    ev.stopImmediatePropagation(); ev.preventDefault();
    $("delete-note").textContent = "Enter a code from your authenticator app, or a backup code.";
    $("delete-factor").focus();
    return;
  }
  ev.stopImmediatePropagation(); ev.preventDefault();
  $("delete-note").textContent = "";
  deleteConfirmDialog().showModal();
}, true);
// Add Delete account to the menu, opening the deletion section of Profile settings.
function menuDelete() {
  if (!FEATS.has("delete") || $("menu-delete")) return;
  const logout = $("menu-logout");
  if (!logout) return;
  const b = el("button", {id: "menu-delete", type: "button", text: "Delete account"});
  b.addEventListener("click", (ev) => {
    ev.stopPropagation();
    $("menu-profile").click();
    setTimeout(() => { const f = $("delete-fold"); if (f) { f.open = true; f.scrollIntoView({block: "center"}); const p = $("delete-password"); if (p) p.focus(); } }, 60);
  });
  logout.before(b);
}

/* ---------- hooks ---------- */
/* ---------- Lobby filter ---------- */
let lobbyNoteEl = null;
// Note above the composer in the public Lobby saying what the server checks there.
function lobbyNote() {
  if (!FEATS.has("lobby-check")) return;
  const form = $("composer"), row = $("lobby-row");
  if (!form || !row) return;
  if (!lobbyNoteEl) {
    lobbyNoteEl = el("p", {id: "pub-lobby-note", class: "pub-lobby-note", role: "note"},
      el("span", {text: FEATS.has("lobby-links")
        ? "Public Lobby: links aren't allowed and mature language is masked. This server checks Lobby messages; direct messages stay end-to-end encrypted."
        : "Public Lobby: mature language is masked. This server checks Lobby messages; direct messages stay end-to-end encrypted."}));
    form.before(lobbyNoteEl);
    new MutationObserver(paintLobbyNote).observe(row, {attributes: true, attributeFilter: ["class"]});
  }
  paintLobbyNote();
}
// Show the Lobby note only while the Lobby is the open chat.
function paintLobbyNote() { if (lobbyNoteEl) lobbyNoteEl.hidden = !$("lobby-row").classList.contains("active"); }
// Until when this account can't post in the Lobby (0 when it can).
function mutedFor() {
  const until = (pub.lobby_filter && pub.lobby_filter.muted_until) || 0;
  return until * 1000 > Date.now() ? until : 0;
}
// Before sending in the Lobby: refuse while muted, or when the message has a link and links aren't allowed.
function lobbyCheck(payload) {
  if (!FEATS.has("lobby-check")) return null;
  const until = mutedFor();
  if (until) {
    const mins = Math.max(1, Math.ceil((until * 1000 - Date.now()) / 60000));
    return "You can't post in the Lobby for " + mins + " more minute" + (mins === 1 ? "" : "s") + ": too many messages tried to get around the filter.";
  }
  if (!FEATS.has("lobby-links") || !cfReady || !payload) return null;
  const text = typeof payload.text === "string" ? payload.text : "";
  if (text && CF.findLinks(text).length) return "Links aren't allowed in the Lobby.";
  return null;
}
// After sending: tell the person when the server removed links or masked words, or paused their Lobby posting.
function onFiltered(info) {
  if (!info) return;
  if (info.muted_until) { pub.lobby_filter = Object.assign({}, pub.lobby_filter, {muted_until: info.muted_until}); }
  const bits = [];
  if (info.links) bits.push(info.links === 1 ? "a link was removed" : info.links + " links were removed");
  if (info.words && !info.links) bits.push("some words were masked");
  let msg = bits.length ? "Sent, but " + bits.join(" and ") + " (Lobby rules)." : "";
  if (info.muted_until) msg = "Lobby posting is paused for a while: too many messages tried to get around the filter. A moderator was told.";
  if (msg) toast(msg);
}

window.bcHooks = {
  init(c) { ctx = c; },
  lobbyKey() { return FEATS.has("lobby-check"); },
  lobbyCheck,
  filtered: onFiltered,
  stamp() { return stamp; },
  text(t) { return filterOn && typeof t === "string" && t ? maskText(t) : undefined; },
  hide(msg) { return !!(msg && msg.sender && blocked.size && blocked.has(msg.sender) && msg.sender !== me().username); },
  menu: onMenu,
  threadHead: onThreadHead,
  groupInfo: onGroupInfo,  // 26.10.2
  sync(data) {
    pub = (data && data.public) || {};
    let changed = false;
    if (LOCKED) { if (!filterOn) { filterOn = true; changed = true; } }
    else if (FEATS.has("filter") && pub.filter && Date.now() - filterTouched > 8000 && filterOn !== !!pub.filter.on) {
      filterOn = !!pub.filter.on; changed = true;
      const box = $("pub-filter"); if (box) box.checked = filterOn;
    }
    if (FEATS.has("report") && Array.isArray(pub.blocked)) {
      const next = pub.blocked.slice().sort().join(",");
      if (next !== [...blocked].sort().join(",")) { blocked = new Set(pub.blocked); changed = true; paintBlockedList(); }
    }
    // 26.10.2: groups this account left and blocked (ids), shown in Safety.
    if (FEATS.has("report") && Array.isArray(pub.blocked_groups)) {
      const nextG = pub.blocked_groups.slice().sort().join(",");
      if (nextG !== [...blockedGroups].sort().join(",")) { blockedGroups = new Set(pub.blocked_groups); paintBlockedGroups(); }
    }
    safetyFold(); reportsFold(); deleteBits(); menuDelete(); lobbyNote();
    if (changed) refresh();
  },
};
