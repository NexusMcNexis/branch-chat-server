/* Administrator page (0.23.3-beta.15 / 0.22.8-public.13, Oct 18 2026).
   Flow: ask the server who we are -> if the admin session is missing, show "Confirm it's you" (re-login or approval
   from another verified device) -> then load the overview, accounts, reports, directory and the settings form.
   Settings edits are only "staged" in this page until Apply; Cancel throws them away; leaving with staged edits asks
   first (links on this page, the Back button, and closing/reloading the tab). The server checks everything again;
   nothing here is trusted for security. */
"use strict";
(function () {
  const $ = (id) => document.getElementById(id);
  const API = "/api/server-admin/";
  let cfg = null;            // last settings view from the server (values, schema, file hash)
  let staged = {};           // key -> new value ("" = remove the line / use the default)
  let expiresAt = 0;         // when the admin session ends (ms since epoch)
  let afterVerify = null;    // something to retry once the owner has confirmed again (e.g. a pending Apply)
  let approvalTimer = 0;
  let leavingOk = false;     // set right before we navigate away on purpose
  let historyArmed = false;  // we pushed one history entry so Back can be caught while edits are staged

  /* ---------- talking to the server ---------- */
  // Every call returns parsed JSON or throws an Error whose .data holds the server's JSON (fields, admin_required...).
  async function call(path, body) {
    const opts = body === undefined ? {credentials: "same-origin"} :
      {method: "POST", credentials: "same-origin", headers: {"Content-Type": "application/json"}, body: JSON.stringify(body)};
    let res;
    try { res = await fetch(API + path, opts); } catch (e) { const err = new Error("The page could not reach the server."); err.network = true; throw err; }
    if (res.status === 401) { leavingOk = true; location.replace("/"); throw new Error("Sign in first."); }
    let data = {};
    try { data = await res.json(); } catch (e) { data = {}; }
    if (!res.ok) {
      const err = new Error(data.error || "That did not work. Try again.");
      err.data = data; err.status = res.status;
      // The 15 minutes ran out: show the confirm step again, keep everything staged, and retry afterwards.
      if (data.admin_required) showGate("Your admin session ended. Confirm it's you to continue.");
      throw err;
    }
    return data;
  }
  const say = (id, text, bad) => { const el = $(id); if (!el) return; el.textContent = text || ""; el.classList.toggle("is-bad", !!bad); };

  /* ---------- dialogs (themed, no browser alert/confirm) ---------- */
  // Resolves with the value of the button pressed, or null when closed with Escape / the backdrop.
  function ask({title, body, buttons, typeName}) {
    const dlg = $("ap-dialog");
    $("ap-dialog-title").textContent = title;
    const bodyEl = $("ap-dialog-body");
    bodyEl.textContent = "";
    if (typeof body === "string") { const p = document.createElement("p"); p.textContent = body; bodyEl.append(p); }
    else if (body) bodyEl.append(body);
    const typeBox = $("ap-dialog-typebox"); const typeIn = $("ap-dialog-type");
    typeBox.hidden = !typeName; typeIn.value = "";
    $("ap-dialog-typelabel").textContent = typeName ? "Type " + typeName + " to confirm" : "";
    const row = $("ap-dialog-actions");
    row.textContent = "";
    return new Promise((resolve) => {
      let done = false;
      const finish = (v) => { if (done) return; done = true; if (dlg.open) dlg.close(); resolve(v); };
      const guarded = [];
      for (const b of buttons) {
        const el = document.createElement("button");
        el.type = "button"; el.className = b.cls || "ghost"; el.textContent = b.label;
        if (b.needsType && typeName) { el.disabled = true; guarded.push(el); }
        el.addEventListener("click", () => { if (!el.disabled) finish(b.value); });
        row.append(el);
      }
      typeIn.oninput = () => { for (const el of guarded) el.disabled = typeIn.value.trim() !== typeName; };
      dlg.oncancel = (ev) => { ev.preventDefault(); finish(null); };
      dlg.onclick = (ev) => { if (ev.target === dlg) finish(null); };
      dlg.onclose = () => finish(null);
      dlg.showModal();
      const first = row.querySelector("button"); if (first) first.focus();
    });
  }

  /* ---------- step 1: confirm it's you ---------- */
  let stage = "";   // "", "totp" or "email": which code the code box is asking for
  function showGate(note) {
    $("ap-gate").hidden = false;
    $("ap-main").hidden = true;
    $("ap-bar").hidden = true;
    say("ap-gate-note", note || "");
    pickTab(stage ? "pass" : (document.querySelector(".ap-tab.is-on") || {}).id === "ap-tab-approve" ? "approve" : "pass");
    const pw = $("ap-password"); if (!stage && pw && !$("ap-pass-form").hidden) pw.focus();
  }
  function pickTab(which) {
    const pass = which === "pass";
    $("ap-tab-pass").classList.toggle("is-on", pass); $("ap-tab-pass").setAttribute("aria-selected", String(pass));
    $("ap-tab-approve").classList.toggle("is-on", !pass); $("ap-tab-approve").setAttribute("aria-selected", String(!pass));
    $("ap-pass-form").hidden = !pass || !!stage;
    $("ap-code-form").hidden = !pass || !stage;
    $("ap-approve-box").hidden = pass;
  }
  function askCode(kind, note) {
    stage = kind;
    $("ap-code-label").textContent = kind === "totp" ? "Code from your authenticator app (or a backup code)" : "6-digit code we emailed you";
    $("ap-code-resend").hidden = kind !== "email";
    $("ap-code").value = "";
    pickTab("pass");
    say("ap-gate-note", note || "");
    $("ap-code").focus();
  }
  async function verified(d) {
    stage = "";
    $("ap-password").value = "";
    expiresAt = Date.now() + (d.expires_in || 900) * 1000;
    $("ap-gate").hidden = true;
    $("ap-main").hidden = false;
    say("ap-gate-note", "");
    stopApproval();
    if (!cfg) await loadAll(); else paintBar();
    if (afterVerify) { const job = afterVerify; afterVerify = null; job(); }
  }
  $("ap-tab-pass").addEventListener("click", () => { stopApproval(); pickTab("pass"); });
  $("ap-tab-approve").addEventListener("click", () => pickTab("approve"));
  $("ap-pass-form").addEventListener("submit", async (ev) => {
    ev.preventDefault();
    say("ap-gate-note", "Checking…");
    try {
      const d = await call("verify/password", {password: $("ap-password").value});
      $("ap-password").value = "";
      if (d.need === "totp") askCode("totp");
      else if (d.need === "email") askCode("email", d.note);
      else await verified(d);
    } catch (e) { say("ap-gate-note", e.message, true); }
  });
  $("ap-code-form").addEventListener("submit", async (ev) => {
    ev.preventDefault();
    try {
      const d = await call("verify/code", {code: $("ap-code").value.trim()});
      if (d.need === "email") askCode("email", d.note); else await verified(d);
    } catch (e) {
      say("ap-gate-note", e.message, true);
      if (e.data && /Start again/.test(e.message)) { stage = ""; pickTab("pass"); }
    }
  });
  $("ap-code-resend").addEventListener("click", async () => {
    try { const d = await call("verify/resend", {}); say("ap-gate-note", d.note || "Sent."); } catch (e) { say("ap-gate-note", e.message, true); }
  });
  $("ap-code-back").addEventListener("click", () => { stage = ""; pickTab("pass"); say("ap-gate-note", ""); });

  // Approval from another device: show a code here, then ask the server every 2 seconds whether it was approved.
  function stopApproval() { if (approvalTimer) clearInterval(approvalTimer); approvalTimer = 0; $("ap-approve-wait").hidden = true; }
  $("ap-approve-start").addEventListener("click", async () => {
    stopApproval();
    try {
      const d = await call("approval/start", {});
      const codeEl = $("ap-approve-code");
      codeEl.hidden = false; codeEl.textContent = d.code.slice(0, 3) + " " + d.code.slice(3);
      $("ap-approve-wait").hidden = false;
      say("ap-gate-note", "Request sent. It expires in 2 minutes.");
      const id = d.id;
      approvalTimer = setInterval(async () => {
        try {
          const r = await call("approval/poll", {id});
          if (r.verified) { $("ap-approve-code").hidden = true; await verified(r); return; }
          if (r.status === "denied" || r.status === "expired") {
            stopApproval(); $("ap-approve-code").hidden = true;
            say("ap-gate-note", r.status === "denied" ? "The request was denied on your other device." : "The request expired. Send a new one.", true);
          }
        } catch (e) { stopApproval(); say("ap-gate-note", e.message, true); }
      }, 2000);
    } catch (e) { say("ap-gate-note", e.message, true); }
  });

  // Session countdown and the Lock button.
  setInterval(() => {
    if ($("ap-main").hidden || !expiresAt) return;
    const left = Math.max(0, Math.round((expiresAt - Date.now()) / 1000));
    $("ap-session-text").textContent = left > 0 ? "Verified · locks in " + Math.floor(left / 60) + ":" + String(left % 60).padStart(2, "0") : "Locked";
    if (left <= 0) { expiresAt = 0; showGate("Your admin session ended. Confirm it's you to continue."); }
  }, 1000);
  $("ap-lock").addEventListener("click", async () => {
    try { await call("lock", {}); } catch (e) { /* locking locally is enough */ }
    expiresAt = 0; showGate("Locked. Confirm it's you to open the page again.");
  });

  /* ---------- step 2: the page ---------- */
  async function loadAll() {
    await Promise.all([loadOverview(), loadUsers(), loadConfig()]);
  }
  function fact(dl, label, value, mono) {
    const dt = document.createElement("dt"); dt.textContent = label;
    const dd = document.createElement("dd");
    if (mono) { const c = document.createElement("code"); c.textContent = value; dd.append(c); } else dd.textContent = value;
    dl.append(dt, dd);
  }
  const when = (ts) => ts ? new Date(ts * 1000).toLocaleString([], {month: "short", day: "numeric", year: "numeric", hour: "numeric", minute: "2-digit"}) : "Never";
  function since(ts) {
    const s = Math.max(0, Math.round(Date.now() / 1000 - ts)); const d = Math.floor(s / 86400), h = Math.floor(s % 86400 / 3600), m = Math.floor(s % 3600 / 60);
    return (d ? d + " d " : "") + (d || h ? h + " h " : "") + m + " min";
  }
  async function loadOverview() {
    try {
      const d = await call("overview");
      const dl = $("ap-facts"); dl.textContent = "";
      fact(dl, "Version", "v" + d.version);
      fact(dl, "Running for", since(d.started_at));
      fact(dl, "Install type", d.install);
      fact(dl, "Public address", d.public_url);
      fact(dl, "Settings file", d.config_path, true);
      fact(dl, "Data folder", d.state_dir, true);
      $("ap-update-how").textContent = "How to update: " + d.update_how;
      paintDirectory(d.directory);
      const hasReports = (d.tools || []).some((t) => /reports/.test(t));
      $("ap-reports").hidden = !hasReports; $("ap-nav-reports").hidden = !hasReports;
      if (hasReports) loadReports();
    } catch (e) { if (!e.data || !e.data.admin_required) say("ap-page-note", e.message, true); }
  }

  /* ----- accounts (same server actions as the Admin Tools in the profile menu) ----- */
  let users = [];
  async function loadUsers() {
    try { const d = await call("users"); users = d.users || []; paintUsers(); }
    catch (e) { if (!e.data || !e.data.admin_required) say("ap-accounts-note", e.message, true); }
  }
  function button(label, cls, fn) { const b = document.createElement("button"); b.type = "button"; b.className = cls + " ap-mini"; b.textContent = label; b.addEventListener("click", fn); return b; }
  async function act(path, body, okText) {
    say("ap-accounts-note", "");
    try { const d = await call(path, body); say("ap-accounts-note", okText || "Done."); await loadUsers(); return d; }
    catch (e) { say("ap-accounts-note", e.message, true); return null; }
  }
  function paintUsers() {
    const q = $("ap-search").value.trim().toLowerCase();
    const list = $("ap-users"); list.textContent = "";
    for (const r of users) {
      if (q && !((r.username || "") + " " + (r.email || "")).toLowerCase().includes(q)) continue;
      const li = document.createElement("li"); li.className = "ap-user" + (r.disabled ? " is-disabled" : "");
      const info = document.createElement("div"); info.className = "ap-user-info";
      // 26.10.13.8: display name bold on top, then @username and email on a smaller line.
      const name = document.createElement("strong"); name.textContent = r.display_name || r.username || r.email;
      const meta = document.createElement("span"); meta.className = "sub";
      const bits = [r.role === "admin" ? "Admin" : "Member"];
      if (r.owner) bits.push("Owner (administrator)");
      if (r.disabled) bits.push("Disabled");
      if (r.pending) bits.push(r.expired ? "Invite expired" : "Invite pending");
      else bits.push("Last active " + when(r.last_active));
      meta.textContent = bits.join(" · ");
      // 26.10.13.8: the account's email (owner-only list; the server never sends it to anyone else).
      const mail = document.createElement("span"); mail.className = "sub ap-user-email";
      mail.textContent = (r.username ? "@" + r.username + " · " : "") + (r.email || "(none)"); mail.title = r.email || "No email address on file";
      info.append(name, mail, meta);
      const tools = document.createElement("div"); tools.className = "ap-user-tools";
      if (r.pending || !r.ready) {
        if (r.email) tools.append(button("Resend invite", "ghost", () => act("users/resend", {email: r.email}, "A fresh invite link was made.")));
        // 26.10.2: cancel a pending invite (the owner may cancel anyone's). Its link stops working at once.
        if (r.invite_id) tools.append(button("Cancel invite", "ghost", () => act("users/cancel-invite", {id: r.invite_id}, "Invite cancelled. Its link no longer works.")));
      } else if (!r.owner) {
        tools.append(button(r.role === "admin" ? "Make member" : "Make admin", "ghost", () => act("users/role", {username: r.username, role: r.role === "admin" ? "member" : "admin"}, "Role updated.")));
        tools.append(button(r.disabled ? "Enable" : "Disable", "ghost", async () => {
          if (r.disabled) { act("users/disable", {username: r.username, disabled: false}, "Account enabled."); return; }
          const ok = await ask({title: "Disable " + r.username + "?", body: "They'll be signed out and hidden everywhere. Nothing is deleted; you can enable them again any time.",
            buttons: [{label: "Keep", value: false}, {label: "Disable " + r.username, value: true, cls: "danger"}]});
          if (ok) act("users/disable", {username: r.username, disabled: true}, "Account disabled.");
        }));
        // 26.10.13.8: email a one-time reset link that also tells them their username.
        const rb = button("Send password reset email", "ghost", async () => {
          const ok = await ask({title: "Send a password reset email to " + r.username + "?", body: "They'll get an email with their username and a link to choose a new password. The link works once, for 1 hour. Their current password keeps working until they use it.",
            buttons: [{label: "Cancel", value: false}, {label: "Send reset email", value: true, cls: "primary"}]});
          if (!ok) return;
          say("ap-accounts-note", "");
          try { await call("users/reset-email", {username: r.username}); say("ap-accounts-note", "Password reset email sent to " + r.username + "."); }
          catch (e) { say("ap-accounts-note", e.message, true); }
        });
        rb.dataset.resetFor = r.username;
        if (!r.email) { rb.disabled = true; rb.title = "No email address on file for this account"; }
        tools.append(rb);
        tools.append(button("Remove", "danger", async () => {
          const ok = await ask({title: "Permanently remove " + r.username + "?", body: "This can't be undone. Their old messages stay.", typeName: r.username,
            buttons: [{label: "Keep", value: false}, {label: "Remove " + r.username, value: true, cls: "danger", needsType: true}]});
          if (ok) act("users/remove", {username: r.username}, "Account removed.");
        }));
      }
      li.append(info, tools); list.append(li);
    }
    if (!list.children.length) { const li = document.createElement("li"); li.className = "sub"; li.textContent = q ? "No one matches." : "No accounts yet."; list.append(li); }
  }
  $("ap-search").addEventListener("input", paintUsers);
  $("ap-invite-send").addEventListener("click", async () => {
    const email = $("ap-invite-email").value.trim();
    if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email)) { say("ap-accounts-note", "Enter a real email address.", true); return; }
    const d = await act("users/invite", {email, role: $("ap-invite-role").value});
    if (d) {
      $("ap-invite-email").value = "";
      say("ap-accounts-note", (d.mailed ? "Invite emailed." : (d.error || "The invite is saved, but the email did not send.")) + (d.link ? " Link (works once, 72 hours): " + d.link : ""));
    }
  });

  /* ----- reports (public servers) ----- */
  // Readable names for report reasons (the same words people pick when they report; "filter" = automatic).
  const REASON_LABELS = {spam: "Spam or scam", harassment: "Harassment or bullying", hate: "Hate speech", sexual: "Sexual content",
    violence: "Violence or threats", "self-harm": "Self-harm", other: "Something else", filter: "Automatic filter"};
  // reportTitle: one line saying what was reported. 26.10.2 adds group chats: a whole group, a message in a
  // group, or a member of a group (the group name comes from the reporter and can't be checked by the server).
  function reportTitle(r) {
    const by = " reported by " + (r.reporter || "?");
    if (!r.group) return (r.reported || "?") + by;
    const gname = r.group.name ? "“" + r.group.name + "”" : "a group";
    if (!r.reported) return "Whole group " + gname + by;
    if (r.message) return "Message from " + r.reported + " in " + gname + by;
    return r.reported + " (member of " + gname + ")" + by;
  }
  async function loadReports() {
    try {
      const d = await call("reports");
      const list = $("ap-report-list"); list.textContent = "";
      for (const r of (d.reports || []).slice(0, 100)) {
        const li = document.createElement("li"); li.className = "ap-user" + (r.status === "resolved" ? " is-disabled" : "");
        const info = document.createElement("div"); info.className = "ap-user-info";
        const name = document.createElement("strong"); name.textContent = reportTitle(r);
        const meta = document.createElement("span"); meta.className = "sub";
        meta.textContent = [when(r.ts), REASON_LABELS[r.reason] || r.reason, r.note ? "Note: " + r.note : "", r.text ? "“" + r.text + "” (text the reporter included; not verifiable)" : "", r.status === "resolved" ? "Resolved by " + r.resolved_by : "Open"].filter(Boolean).join(" · ");
        info.append(name, meta);
        // 26.10.2: details that used to show only in the profile pop-up's Reports list (moved here): which kind of
        // message it was and when, whether it was removed or had a file, how many reports name this person, and
        // whether their account is gone or disabled.
        const facts = [];
        if (r.message) {
          const kind = r.message.box === "lobby" ? "Lobby message" : r.message.box === "group" ? "Group message" : "Direct message";
          facts.push(kind + " from " + when(r.message.ts) + (r.message.deleted ? " (since removed)" : "") + (r.message.has_file ? " · had a file" : ""));
        }
        if (r.reported) facts.push(r.against_count + (r.against_count === 1 ? " report" : " reports") + " about this person" + (r.reported_exists ? "" : " · account deleted") + (r.reported_disabled ? " · disabled" : ""));
        if (facts.length) { const fl = document.createElement("span"); fl.className = "sub"; fl.textContent = facts.join(" · "); info.append(fl); }
        // 26.10.2: a report about a group chat gets one more line with what the server knows about the group
        // (open or closed, members now, how many reports name it). Group messages are end-to-end encrypted, so
        // the name shown is the one the reporter's browser sent, and these reports are how admins spot a bad group.
        if (r.group) {
          const g = r.group, gl = document.createElement("span"); gl.className = "sub ap-group-line";
          gl.textContent = [g.live ? "Group open" : "Group closed", g.live ? g.members_now + (g.members_now === 1 ? " member" : " members") : "",
            g.reports + (g.reports === 1 ? " report" : " reports") + " about this group",
            g.blocked_by ? g.blocked_by + (g.blocked_by === 1 ? " person" : " people") + " left and blocked it" : "",
            g.creator ? "started by " + g.creator : ""].filter(Boolean).join(" · ");
          info.append(gl);
        }
        const tools = document.createElement("div"); tools.className = "ap-user-tools";
        tools.append(button(r.status === "resolved" ? "Reopen" : "Mark resolved", "ghost", async () => {
          try { await call("reports/status", {id: r.id, status: r.status === "resolved" ? "open" : "resolved"}); loadReports(); } catch (e) { say("ap-reports-note", e.message, true); }
        }));
        // 26.10.2 (moved from the profile pop-up): lift the Lobby mute of someone the automatic filter reported.
        if (r.reason === "filter" && r.reported && r.reported_exists) {
          tools.append(button("Lift Lobby mute", "ghost", async () => {
            try { await call("filter/unmute", {username: r.reported}); say("ap-reports-note", "Lobby mute lifted for " + r.reported + "."); } catch (e) { say("ap-reports-note", e.message, true); }
          }));
        }
        // 26.10.2 (moved from the profile pop-up): disable the reported account straight from the report (two taps).
        if (r.reported && r.reported_exists && !r.reported_disabled) {
          const db = button("Disable account", "ghost", async () => {
            if (db.dataset.armed !== "1") { db.dataset.armed = "1"; db.textContent = "Tap again to disable " + r.reported; setTimeout(() => { db.dataset.armed = ""; db.textContent = "Disable account"; }, 4000); return; }
            try { await call("users/disable", {username: r.reported, disabled: true}); say("ap-reports-note", r.reported + " is disabled."); loadReports(); } catch (e) { say("ap-reports-note", e.message, true); }
          });
          tools.append(db);
        }
        // 26.10.2: Close group ends a reported group for everyone (its reports are marked resolved). It takes two
        // taps, because it can't be undone from the page; the group file is kept on the server.
        if (r.group && r.group.live) {
          const cb = button("Close group", "ghost", async () => {
            if (cb.dataset.armed !== "1") { cb.dataset.armed = "1"; cb.textContent = "Tap again to close"; setTimeout(() => { cb.dataset.armed = ""; cb.textContent = "Close group"; }, 4000); return; }
            try { await call("groups/close", {id: r.group.id}); say("ap-reports-note", "Group closed."); loadReports(); } catch (e) { say("ap-reports-note", e.message, true); }
          });
          tools.append(cb);
        }
        li.append(info, tools); list.append(li);
      }
      if (!list.children.length) { const li = document.createElement("li"); li.className = "sub"; li.textContent = "No reports."; list.append(li); }
      const mutes = $("ap-mute-list"); mutes.textContent = "";
      let f = {mutes: []};
      try { f = await call("filter"); } catch (e) { /* the filter may be off on this server */ }
      for (const m of f.mutes || []) {
        const li = document.createElement("li"); li.className = "ap-user";
        const info = document.createElement("div"); info.className = "ap-user-info";
        const name = document.createElement("strong"); name.textContent = m.username;
        const meta = document.createElement("span"); meta.className = "sub"; meta.textContent = "Muted until " + when(m.until) + (m.reason ? " · " + m.reason : "");
        info.append(name, meta);
        const tools = document.createElement("div"); tools.className = "ap-user-tools";
        tools.append(button("Unmute", "ghost", async () => { try { await call("filter/unmute", {username: m.username}); loadReports(); } catch (e) { say("ap-reports-note", e.message, true); } }));
        li.append(info, tools); mutes.append(li);
      }
      if (!mutes.children.length) { const li = document.createElement("li"); li.className = "sub"; li.textContent = "No one is muted."; mutes.append(li); }
    } catch (e) { if (!e.data || !e.data.admin_required) say("ap-reports-note", e.message, true); }
  }

  /* ----- directory ----- */
  function paintDirectory(d) {
    if (!d) return;
    const dl = $("ap-dir-facts"); dl.textContent = "";
    const paused = d.paused_until && d.paused_until * 1000 > Date.now();
    fact(dl, "Listed as", d.name || "");
    fact(dl, "Reporting", d.home_mode ? "This server is a directory home" : d.reporting ? (paused ? "Paused until " + when(d.paused_until) : "On, to " + d.home_url) : "Off");
    if (d.reporting && d.last && d.last.at) fact(dl, "Last note", when(d.last.at) + " · " + (d.last.state || d.last.status || "") );
    if (d.fingerprint) fact(dl, "Server key", d.fingerprint, true);
    $("ap-dir-pause").hidden = !d.reporting || paused;
    $("ap-dir-resume").hidden = !d.reporting || !paused;
  }
  async function pause(minutes) {
    try { const d = await call("directory/pause", {minutes}); paintDirectory(d.directory); say("ap-dir-note", minutes ? "Paused. The listing disappears from the directory until it resumes." : "Resumed. The listing comes back within a few minutes."); }
    catch (e) { say("ap-dir-note", e.message, true); }
  }
  $("ap-dir-pause").addEventListener("click", async () => {
    const ok = await ask({title: "Pause the directory listing?", body: "This server stops telling the directory it's online for one hour, so it drops out of the list. Invites keep working.",
      buttons: [{label: "Keep listed", value: false}, {label: "Pause for 1 hour", value: true, cls: "solid"}]});
    if (ok) pause(60);
  });
  $("ap-dir-resume").addEventListener("click", () => pause(0));

  /* ----- settings: build the form from the server's schema ----- */
  async function loadConfig() {
    try { cfg = await call("config"); staged = {}; paintConfig(); paintBar(); }
    catch (e) { if (!e.data || !e.data.admin_required) say("ap-page-note", e.message, true); }
  }
  // What the field shows when nothing is staged: the file value, the environment value, or "" for the default.
  const original = (row) => row.type === "secret" ? null : (row.value == null ? "" : row.value);
  function paintConfig() {
    $("ap-path").textContent = cfg.path;
    const notes = [];
    notes.push(cfg.exists ? "Each Apply keeps the previous file in " + cfg.backup_dir + "." : "The file doesn't exist yet; Apply creates it.");
    if (cfg.docker) notes.push("Docker install: settings from the container's environment (env_file) win over this file and are shown locked below.");
    if (!cfg.writable) notes.push("The server can't write this file right now, so Apply is off. Fix its permissions or edit it by hand.");
    $("ap-file-note").textContent = notes.join(" ");
    const host = $("ap-groups");
    // Keep the groups the owner had open when the form is rebuilt (after Cancel or Apply).
    const wasOpen = new Set([...host.querySelectorAll("details[open] > summary")].map((s) => s.textContent));
    const first = !host.children.length;
    host.textContent = "";
    for (const g of cfg.groups) {
      const rows = cfg.settings.filter((r) => r.group === g);
      if (!rows.length) continue;
      const det = document.createElement("details"); det.className = "fold ap-group"; det.open = first ? g === cfg.groups[0] : wasOpen.has(g);
      const sum = document.createElement("summary"); sum.textContent = g; det.append(sum);
      for (const row of rows) det.append(field(row));
      host.append(det);
    }
    const bl = $("ap-backup-list"); bl.textContent = "";
    for (const name of cfg.backups || []) { const li = document.createElement("li"); li.textContent = name; bl.append(li); }
    if (!bl.children.length) { const li = document.createElement("li"); li.textContent = "None yet."; bl.append(li); }
  }
  function sourceText(row) {
    if (row.source === "environment") return "from the environment";
    if (row.source === "file") return "in the file";
    return "default";
  }
  function field(row) {
    const wrap = document.createElement("div"); wrap.className = "ap-field"; wrap.dataset.key = row.key;
    const id = "f-" + row.key;
    const head = document.createElement("div"); head.className = "ap-field-head";
    const label = document.createElement("label"); label.htmlFor = id; label.textContent = row.label;
    const tag = document.createElement("span"); tag.className = "ap-src ap-src-" + row.source; tag.textContent = sourceText(row);
    head.append(label, tag);
    const key = document.createElement("code"); key.className = "ap-key"; key.textContent = row.key;
    wrap.append(head, key);
    if (row.help) { const p = document.createElement("p"); p.className = "sub ap-help"; p.textContent = row.help; wrap.append(p); }
    let input;
    const def = row.default ? "Default: " + row.default : "Default: empty";
    if (row.type === "secret") {
      input = secretField(row, id);
    } else if (row.type === "bool" || row.type === "enum") {
      input = document.createElement("select"); input.id = id;
      const opts = row.type === "bool" ? [["", "Default (" + boolWord(row.default) + ")"], ["1", "On"], ["0", "Off"]]
        : row.options.map((o) => [o, o === "" ? "Default (" + (row.default || "none") + ")" : o]);
      const cur = original(row);
      if (row.type === "bool" && cur !== "" && cur !== "1" && cur !== "0") opts.push([cur, cur]);
      for (const [v, t] of opts) { const o = document.createElement("option"); o.value = v; o.textContent = t; input.append(o); }
      input.value = cur;
      input.addEventListener("change", () => stageChange(row, input.value));
    } else {
      input = document.createElement("input"); input.id = id; input.type = row.type === "int" ? "text" : "text";
      if (row.type === "int") input.inputMode = "numeric";
      input.autocomplete = "off"; input.spellcheck = false; input.maxLength = 2000;
      input.placeholder = def; input.value = original(row);
      input.addEventListener("input", () => stageChange(row, input.value));
    }
    if (row.readonly) {
      if (input.matches("input,select")) input.disabled = true; else input.querySelectorAll("input,button").forEach((el) => { el.disabled = true; });
      const p = document.createElement("p"); p.className = "sub ap-locked"; p.textContent = "Locked here: " + row.readonly; wrap.append(input, p);
    } else wrap.append(input);
    if (row.danger) wrap.classList.add("is-danger");
    const err = document.createElement("p"); err.className = "ap-err"; err.id = "err-" + row.key; err.setAttribute("role", "alert"); wrap.append(err);
    return wrap;
  }
  const boolWord = (d) => d === "1" ? "on" : d === "0" ? "off" : (d || "off");
  // Secrets: the page never receives the value. It can only be replaced with a new one, or removed.
  function secretField(row, id) {
    const box = document.createElement("div"); box.className = "ap-secret";
    const state = document.createElement("span"); state.className = "ap-secret-state";
    const input = document.createElement("input"); input.type = "password"; input.id = id; input.autocomplete = "new-password"; input.hidden = true; input.maxLength = 2000;
    input.placeholder = "New value";
    const replace = document.createElement("button"); replace.type = "button"; replace.className = "ghost ap-mini"; replace.textContent = row.is_set ? "Replace" : "Set";
    const remove = document.createElement("button"); remove.type = "button"; remove.className = "ghost ap-mini"; remove.textContent = "Remove"; remove.hidden = !row.is_set;
    const undo = document.createElement("button"); undo.type = "button"; undo.className = "ghost ap-mini"; undo.textContent = "Undo"; undo.hidden = true;
    const paint = () => {
      const has = Object.prototype.hasOwnProperty.call(staged, row.key);
      state.textContent = has ? (staged[row.key] === "" ? "Will be removed" : "New value staged") : (row.is_set ? "Set (hidden)" : "Not set");
      undo.hidden = !has; replace.hidden = has && staged[row.key] !== ""; remove.hidden = !row.is_set || has;
      input.hidden = !(has && staged[row.key] !== "") && input.hidden;
    };
    replace.addEventListener("click", () => { input.hidden = false; input.value = ""; input.focus(); replace.hidden = true; });
    input.addEventListener("input", () => { if (input.value) staged[row.key] = input.value; else delete staged[row.key]; paint(); paintBar(); });
    remove.addEventListener("click", () => { staged[row.key] = ""; input.hidden = true; input.value = ""; paint(); paintBar(); });
    undo.addEventListener("click", () => { delete staged[row.key]; input.hidden = true; input.value = ""; paint(); paintBar(); replace.hidden = false; });
    box.append(state, input, replace, remove, undo);
    box.repaint = paint; paint();
    return box;
  }
  // Remember a change only when it differs from what the file has now; typing it back removes it again.
  function stageChange(row, value) {
    const err = $("err-" + row.key); if (err) err.textContent = "";
    if (value.trim() === (original(row) || "").trim()) delete staged[row.key]; else staged[row.key] = value;
    const wrap = document.querySelector('.ap-field[data-key="' + row.key + '"]');
    if (wrap) wrap.classList.toggle("is-changed", Object.prototype.hasOwnProperty.call(staged, row.key));
    paintBar();
  }
  const dirty = () => Object.keys(staged).length > 0;
  // The Apply / Cancel bar, and the Back-button guard while anything is staged.
  function paintBar() {
    const n = Object.keys(staged).length;
    $("ap-bar").hidden = n === 0 || $("ap-main").hidden;
    $("ap-bar-text").textContent = n === 1 ? "1 change not applied" : n + " changes not applied";
    $("ap-apply").disabled = !cfg || !cfg.writable;
    if (n && !historyArmed) { history.pushState({apGuard: 1}, ""); historyArmed = true; }
  }
  function rowOf(key) { return cfg.settings.find((r) => r.key === key); }
  function changeList() {
    const ul = document.createElement("ul"); ul.className = "ap-change-list";
    for (const [k, v] of Object.entries(staged)) {
      const row = rowOf(k) || {label: k, type: "text"};
      const li = document.createElement("li");
      const b = document.createElement("strong"); b.textContent = row.label;
      let text;
      if (row.type === "secret") text = v === "" ? "removed" : "replaced (hidden)";
      else text = (original(row) || "default") + "  →  " + (v.trim() === "" ? "default" : v.trim());
      li.append(b, document.createTextNode(": " + text));
      if (row.danger) { const w = document.createElement("span"); w.className = "ap-warn"; w.textContent = " Check this one carefully."; li.append(w); }
      ul.append(li);
    }
    return ul;
  }
  async function applyNow() {
    const body = document.createElement("div");
    const p = document.createElement("p"); p.textContent = "These changes will be saved to the settings file. The current file is backed up first, then the server restarts (a few seconds). Everyone stays signed in.";
    body.append(p, changeList());
    if (staged.BC_OWNER !== undefined) { const w = document.createElement("p"); w.className = "ap-warn"; w.textContent = "Changing the owner moves the Administrator page to that account. You will lose access to it."; body.append(w); }
    const ok = await ask({title: "Apply changes?", body, buttons: [{label: "Keep editing", value: false}, {label: "Apply", value: true, cls: "solid"}]});
    if (!ok) return false;
    return sendApply();
  }
  async function sendApply() {
    document.querySelectorAll(".ap-err").forEach((e) => { e.textContent = ""; });
    try {
      const d = await call("config/apply", {changes: staged, hash: cfg.hash});
      staged = {}; paintBar();
      await waitForRestart(d);
      return true;
    } catch (e) {
      const data = e.data || {};
      if (data.admin_required) { afterVerify = () => sendApply(); return false; }
      if (data.fields) {
        for (const [k, msg] of Object.entries(data.fields)) { const el = $("err-" + k); if (el) el.textContent = msg; }
        const first = document.querySelector(".ap-err:not(:empty)"); if (first) { const det = first.closest("details"); if (det) det.open = true; first.scrollIntoView({block: "center"}); }
      }
      say("ap-page-note", e.message, true);
      if (data.stale) await ask({title: "The settings file changed", body: "Someone or something changed the file since this page loaded. Your edits are still here; reload the page to see the new file first.", buttons: [{label: "OK", value: true, cls: "solid"}]});
      return false;
    }
  }
  // After Apply the server restarts itself. Wait until it answers again, then reload the form from the new file.
  async function waitForRestart(d) {
    $("ap-restart").hidden = false;
    $("ap-restart-text").textContent = "Saved to " + d.path + (d.backup ? " (old file kept as " + d.backup + ")" : "") + ". Restarting the server…";
    await new Promise((r) => setTimeout(r, 2500));
    let up = false;
    for (let i = 0; i < 60 && !up; i++) {
      try { const r = await fetch("/api/health", {cache: "no-store"}); up = r.ok; } catch (e) { up = false; }
      if (!up) await new Promise((r) => setTimeout(r, 1000));
    }
    $("ap-restart").hidden = true;
    if (!up) { say("ap-page-note", "The server has not come back yet. If it can't start with the new settings it puts the old file back by itself; reload in a minute.", true); return; }
    say("ap-page-note", "Applied: " + d.changed.join(", ") + ". The server restarted with the new settings.");
    try { const s = await call("state"); if (s.expires_in > 0) expiresAt = Date.now() + s.expires_in * 1000; } catch (e) { /* the gate shows if needed */ }
    await loadConfig(); loadOverview();
  }
  $("ap-apply").addEventListener("click", applyNow);
  $("ap-cancel").addEventListener("click", async () => {
    const ok = await ask({title: "Discard your changes?", body: changeList(), buttons: [{label: "Keep editing", value: false}, {label: "Discard changes", value: true, cls: "danger"}]});
    if (ok) { staged = {}; paintConfig(); paintBar(); say("ap-page-note", "Changes discarded. Nothing was saved."); }
  });
  $("ap-copy-path").addEventListener("click", async () => {
    try { await navigator.clipboard.writeText(cfg.path); say("ap-page-note", "Path copied."); } catch (e) { say("ap-page-note", "Copy didn't work; select the path and copy it."); }
  });

  /* ---------- leaving with staged changes ---------- */
  // Asks Apply / Discard / Keep editing. Resolves true when it is fine to leave.
  async function confirmLeave() {
    if (!dirty()) return true;
    const body = document.createElement("div");
    const p = document.createElement("p"); p.textContent = "You have changes that are not applied yet. Apply them, or leave and lose them?";
    body.append(p, changeList());
    const v = await ask({title: "Apply your changes?", body, buttons: [{label: "Keep editing", value: "stay"}, {label: "Discard and leave", value: "discard", cls: "danger"}, {label: "Apply", value: "apply", cls: "solid"}]});
    if (v === "discard") { staged = {}; return true; }
    if (v === "apply") return applyNow();
    return false;
  }
  // Links on this page (logo, version, patch notes) go through the question first.
  document.addEventListener("click", async (ev) => {
    const a = ev.target.closest && ev.target.closest("a[href]");
    if (!a || !dirty() || a.getAttribute("href").startsWith("#")) return;
    ev.preventDefault();
    if (await confirmLeave()) { leavingOk = true; location.href = a.href; }
  });
  // Back button: one extra history entry is pushed while edits are staged, so Back lands here first.
  addEventListener("popstate", async () => {
    if (!historyArmed) return;
    historyArmed = false;
    if (!dirty()) { history.back(); return; }
    if (await confirmLeave()) { leavingOk = true; history.back(); }
    else { history.pushState({apGuard: 1}, ""); historyArmed = true; }
  });
  // Closing or reloading the tab: browsers only allow their own generic "Leave site?" question here.
  addEventListener("beforeunload", (ev) => {
    if (leavingOk || !dirty()) return;
    ev.preventDefault(); ev.returnValue = "";
  });

  /* ---------- start ---------- */
  (async () => {
    try {
      const s = await call("state");
      if (s.locked_for) say("ap-gate-note", "Too many wrong tries. Wait a few minutes.", true);
      if (s.expires_in > 0) await verified(s);
      else showGate(s.verified_browser ? "" : "This browser hasn't been verified before, so we'll also email you a code.");
    } catch (e) { say("ap-page-note", e.message, true); }
  })();
})();
