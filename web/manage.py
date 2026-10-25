#!/usr/bin/env python3
"""Branch Chat Server admin commands (standard library only).

Run as the user that owns the state directory, with the same config the server uses:
  native:  sudo -u branchchat BC_CONFIG=/etc/branch-chat/config.env python3 /opt/branch-chat/app/manage.py status
  docker:  docker compose exec branch-chat python manage.py status

Commands
  status                         state dir, schema version, account counts
  create-admin --username U --email E [--password-stdin] [--reset-password]
                                 create the first admin (idempotent). The password comes from
                                 stdin (--password-stdin), $BC_ADMIN_PASSWORD, or an interactive prompt.
  invite --email E [--role member|admin]
                                 create an invite and print the link (use when mail is not set up)
  set-password --username U [--password-stdin]
  user-exists --username U       exit 0 when that account exists (used by the installer)
  healthcheck                    exit 0 when GET /healthz answers "ok" (used by Docker and the installer)
  migrate                        run the startup schema migration step on its own
Directory (0.23.3)
  directory-status               reporting flag, home URL, this server's identity key fingerprint, last ping result
  directory-ping [--offline]     send one lease ping now and print the home's answer
  directory-pause --minutes N    stop the running server's lease pings for N minutes (the listing expires)
  directory-resume               let the running server ping again (next ping within ~10 s)
  directory-list                 (home) every listing with its state
  directory-hide|unhide|ban|unban --server HOST[:PORT]
  directory-vouch --server HOST[:PORT] --key-fp FIRST12 [--name N] [--icon FILE]   (home) list a server the home cannot reach, after
                                 comparing its key fingerprint out of band; directory-unvouch undoes it
  directory-revoked              (home) the key revocation list
  directory-revoke --key FP|HOST [--reason R]   (home) revoke a server key: delisted at once, pings refused
  directory-unrevoke --key FP|HOST
  directory-forget --server HOST[:PORT]   (home) drop a listing record (e.g. a test entry); not a ban
  directory-pin --server URL --name N [--type family] [--login-url URL] [--probe /api/health] [--icon FILE]
                                 (home) list a server without lease code (admin-pinned; online = HTTPS probe)
  directory-unpin --server HOST  (home) remove a pinned entry
  directory-admin-tls            (home) SHA-256 of the admin listener's TLS cert, for the panel to pin
"""
import argparse
import getpass
import os
import sys
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import bcconfig  # noqa: E402


def _password(args, confirm=True):
    if getattr(args, "password_stdin", False):
        pw = sys.stdin.readline().rstrip("\r\n")
    elif os.environ.get("BC_ADMIN_PASSWORD"):
        pw = os.environ["BC_ADMIN_PASSWORD"]
    else:
        pw = getpass.getpass("Password (8+ characters): ")
        if confirm and getpass.getpass("Repeat password: ") != pw:
            sys.exit("Passwords did not match.")
    if len(pw) < 8 or len(pw) > 200:
        sys.exit("Use at least 8 characters.")
    return pw


def cmd_status(_args):
    import accounts
    import server
    data = accounts._read()
    users = [u for u in data["users"] if u.get("password")]
    admins = [u for u in users if u.get("role") == "admin"]
    print("config file:     %s" % (bcconfig.CONFIG_PATH or "(none, using environment/defaults)"))
    print("state dir:       %s" % bcconfig.STATE_DIR)
    print("app dir:         %s" % bcconfig.APP_DIR)
    print("listen:          %s:%d" % (bcconfig.HOST, bcconfig.PORT))
    print("public url:      %s" % bcconfig.PUBLIC_URL)
    print("hosts:           %s" % ", ".join(sorted(bcconfig.HOSTS)))
    print("instance name:   %s" % (bcconfig.INSTANCE_NAME or "(not set)"))
    print("owner:           %s" % bcconfig.OWNER_USERNAME)
    print("mail mode:       %s" % bcconfig.MAIL_MODE)
    print("schema version:  %d (code knows %d)" % (server._read_schema_version() or 1, server.SCHEMA_VERSION))
    print("accounts:        %d (%d admin), pending invites: %d" % (len(users), len(admins), len(data["invites"])))


def cmd_create_admin(args):
    import accounts
    username = args.username.strip()
    if not accounts.USERNAME_RE.match(username):
        sys.exit("Usernames are 3 to 32 letters, numbers, dots, or dashes.")
    existing = accounts.find_user(username)
    if existing and existing.get("password"):
        if args.reset_password:
            err = accounts.set_password(username, _password(args))
            if err:
                sys.exit(err)
            print("Password updated for %s." % username)
        else:
            print("Account %s already exists; nothing changed." % username)
        if existing.get("role") != "admin":
            err = accounts.set_role(username, "admin")
            if err:
                sys.exit(err)
            print("Role set to admin.")
        return
    pw = _password(args)
    token, err = accounts.create_invite(args.email, "admin")
    if err:
        sys.exit(err)
    err = accounts.accept_invite(token, username, pw)
    if err:
        sys.exit(err)
    print("Created admin account %s." % username)
    if username.lower() != bcconfig.OWNER_USERNAME:
        print("Note: the owner (BC_OWNER) is %s. Set BC_OWNER=%s to give this account the owner tools."
              % (bcconfig.OWNER_USERNAME, username.lower()))


def cmd_settle_invites(args):
    """26.10.10f one-time cleanup: mark invites accepted when their email already has an account. Dry run unless --apply."""
    import accounts
    found = accounts.settle_stale_invites(dry_run=not args.apply)
    for inv_id, email, user in found:
        print("%s %s -> %s" % (inv_id[:8], email, user))
    print("%s %d stale invite(s)." % ("Marked accepted:" if args.apply else "Would mark accepted:", len(found)))


def cmd_user_exists(args):
    import accounts
    user = accounts.find_user(args.username)
    sys.exit(0 if user and user.get("password") else 1)


def cmd_invite(args):
    import accounts
    import directory
    token, err = accounts.create_invite(args.email, args.role, invited_by=(args.invited_by or ""))
    if err:
        sys.exit(err)
    print(directory.invite_link(bcconfig.PUBLIC_URL, token))
    print("This link works once and lasts 72 hours.")


def cmd_dir_status(_args):
    import directory
    import json
    print("reporting:       %s" % ("on" if directory.REPORT else "off"))
    print("home mode:       %s" % ("on" if directory.HOME_MODE else "off"))
    print("home url:        %s" % directory.HOME_URL)
    print("org name:        %s" % directory.org_name())
    raw, kind = directory.org_icon()
    print("org icon:        %s %d bytes, hash %s" % (kind or "-", len(raw), directory.icon_hash(raw) or "-"))
    print("identity key:    %s (fingerprint %s; private key in %s, mode 600)" % (directory.public_key_b64(), directory.fingerprint(), directory.IDENTITY_PATH))
    print("ping every:      ~%d s, home lists for %d s after a ping" % (directory.PING_SECONDS, directory.TTL))
    pu = directory.paused_until()
    import time as _t
    if pu > _t.time():
        print("paused until:    %s" % _t.strftime("%Y-%m-%d %H:%M:%S %Z", _t.localtime(pu)))
    try:
        with open(directory.STATUS_PATH) as fh:
            row = json.load(fh)
        print("last ping:       %s  %s" % (_t.strftime("%H:%M:%S", _t.localtime(row.get("at", 0))), row.get("message", "")))
    except (OSError, ValueError):
        print("last ping:       (none yet)")


def cmd_dir_ping(args):
    import directory
    status, reply = directory.ping_once(bcconfig.PUBLIC_URL, online=not args.offline)
    print("HTTP %d %s" % (status, reply))


def cmd_dir_pause(args):
    import directory
    import time as _t
    until = int(_t.time()) + max(1, args.minutes) * 60
    with open(directory.PAUSE_PATH, "w") as fh:
        fh.write(str(until))
    print("Lease pings paused until %s." % _t.strftime("%H:%M:%S %Z", _t.localtime(until)))


def cmd_dir_resume(_args):
    import directory
    try:
        os.remove(directory.PAUSE_PATH)
    except OSError:
        pass
    print("Lease pings resume within ~10 s.")


def cmd_dir_list(_args):
    import directory
    rows = directory.admin_rows()
    if not rows:
        print("(no listings)")
    for r in rows:
        print("%-34s %-8s %-10s last ping %6ds ago  key %s%s" % (r["server"], r["status"], r["verified_by"] or "-",
              r["last_ping_age"], r["key_fp"][:12], ("  [" + r["pending"] + "]") if r["pending"] else ""))


def cmd_dir_forget(args):
    import directory
    print(directory.forget(args.server) or "Forgot %s." % args.server)


def cmd_dir_admin_tls(_args):
    import directory
    print(directory.admin_tls_fingerprint())


def cmd_dir_pin(args):
    import directory
    err = directory.pin(args.server, args.name, args.type, args.login_url, args.probe, args.icon)
    print(err or "Pinned %s." % args.server)
    if err:
        sys.exit(1)


def cmd_dir_unpin(args):
    import directory
    err = directory.unpin(args.server)
    print(err or "Unpinned %s." % args.server)
    if err:
        sys.exit(1)


def cmd_dir_revoked(_args):
    import directory
    import time as _t
    rows = directory.revoked_list()
    if not rows:
        print("(no revoked keys)")
    for r in rows:
        print("%s  revoked %s  %s" % (r.get("fp"), _t.strftime("%Y-%m-%d %H:%M", _t.localtime(r.get("at", 0))), r.get("reason", "")))


def _dir_revoke(undo):
    def run(args):
        import directory
        fp, err = directory.revoke(args.key, getattr(args, "reason", "") or "", undo=undo)
        if err:
            sys.exit(err)
        print("%s %s." % (fp, "un-revoked" if undo else "revoked; delisted now"))
    return run


def _dir_admin(field, value):
    def run(args):
        import directory
        err = directory.admin_set(args.server, field, value, getattr(args, "key_fp", "") or "",
                                  getattr(args, "name", "") or "", getattr(args, "icon", "") or "")
        if err:
            sys.exit(err)
        print("%s %s: done." % (args.server, field if value else "un" + field))
    return run


def cmd_set_password(args):
    import accounts
    err = accounts.set_password(args.username, _password(args))
    if err:
        sys.exit(err)
    print("Password updated.")


def cmd_healthcheck(_args):
    host = bcconfig.HOST
    if host in ("0.0.0.0", "", "::"):
        host = "127.0.0.1"
    if ":" in host:
        host = "[%s]" % host
    try:
        with urllib.request.urlopen("http://%s:%d/healthz" % (host, bcconfig.PORT), timeout=4) as resp:
            ok = resp.status == 200 and resp.read(16).strip() == b"ok"
    except OSError:
        ok = False
    sys.exit(0 if ok else 1)


def cmd_migrate(_args):
    import server
    os.makedirs(bcconfig.STATE_DIR, exist_ok=True)
    server.migrate_state()
    print("Schema version %d." % server._read_schema_version())


def main():
    parser = argparse.ArgumentParser(prog="manage.py", description="BranchChat Server admin commands")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status").set_defaults(fn=cmd_status)
    p = sub.add_parser("create-admin")
    p.add_argument("--username", required=True)
    p.add_argument("--email", required=True)
    p.add_argument("--password-stdin", action="store_true")
    p.add_argument("--reset-password", action="store_true")
    p.set_defaults(fn=cmd_create_admin)
    p = sub.add_parser("settle-invites")
    p.add_argument("--apply", action="store_true")
    p.set_defaults(fn=cmd_settle_invites)
    p = sub.add_parser("user-exists")
    p.add_argument("--username", required=True)
    p.set_defaults(fn=cmd_user_exists)
    p = sub.add_parser("invite")
    p.add_argument("--email", required=True)
    p.add_argument("--role", choices=("member", "admin"), default="member")
    p.add_argument("--invited-by", default="")
    p.set_defaults(fn=cmd_invite)
    sub.add_parser("directory-status").set_defaults(fn=cmd_dir_status)
    p = sub.add_parser("directory-ping")
    p.add_argument("--offline", action="store_true")
    p.set_defaults(fn=cmd_dir_ping)
    p = sub.add_parser("directory-pause")
    p.add_argument("--minutes", type=int, required=True)
    p.set_defaults(fn=cmd_dir_pause)
    sub.add_parser("directory-resume").set_defaults(fn=cmd_dir_resume)
    sub.add_parser("directory-list").set_defaults(fn=cmd_dir_list)
    for name, field, value in (("hide", "hide", True), ("unhide", "hide", False), ("ban", "ban", True),
                               ("unban", "ban", False), ("vouch", "vouch", True), ("unvouch", "vouch", False)):
        p = sub.add_parser("directory-" + name)
        p.add_argument("--server", required=True)
        if name == "vouch":
            p.add_argument("--key-fp", required=True)
            p.add_argument("--name", default="", help="name to show when the home cannot read the server's own")
            p.add_argument("--icon", default="", help="PNG/JPEG/WebP icon to show in that case")
        p.set_defaults(fn=_dir_admin(field, value))
    sub.add_parser("directory-revoked").set_defaults(fn=cmd_dir_revoked)
    p = sub.add_parser("directory-forget")
    p.add_argument("--server", required=True)
    p.set_defaults(fn=cmd_dir_forget)
    sub.add_parser("directory-admin-tls").set_defaults(fn=cmd_dir_admin_tls)
    p = sub.add_parser("directory-pin")
    p.add_argument("--server", required=True)
    p.add_argument("--name", required=True)
    p.add_argument("--type", default="")
    p.add_argument("--login-url", default="")
    p.add_argument("--probe", default="/api/health")
    p.add_argument("--icon", default="")
    p.set_defaults(fn=cmd_dir_pin)
    p = sub.add_parser("directory-unpin")
    p.add_argument("--server", required=True)
    p.set_defaults(fn=cmd_dir_unpin)
    p = sub.add_parser("directory-revoke")
    p.add_argument("--key", required=True)
    p.add_argument("--reason", default="")
    p.set_defaults(fn=_dir_revoke(False))
    p = sub.add_parser("directory-unrevoke")
    p.add_argument("--key", required=True)
    p.set_defaults(fn=_dir_revoke(True))
    p = sub.add_parser("set-password")
    p.add_argument("--username", required=True)
    p.add_argument("--password-stdin", action="store_true")
    p.set_defaults(fn=cmd_set_password)
    sub.add_parser("healthcheck").set_defaults(fn=cmd_healthcheck)
    sub.add_parser("migrate").set_defaults(fn=cmd_migrate)
    args = parser.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()
