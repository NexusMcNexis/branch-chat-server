"""HTML emails for Branch Chat (26.10.2): the light "sunlit morning" design.

Every email the server sends goes out as two versions in one message (multipart/alternative):
a plain-text version, for mail apps that cannot show HTML, and this HTML version.
accounts.py builds the plain text and puts the two together; this file only builds the HTML.

The layout is copied from the approved mock-up (mockups/email-templates, light theme):
  - logo row: BranchChat icon + two-tone "BranchChat" wordmark
  - one rounded card: the forest banner on top, then the message, then one centred button
  - a "Signing in to <server>" line on the sign-in and reset emails, with the server's own icon
  - an invite box that is narrow (up to 400px) and centred, icon and text centred together
  - a small footer with the reason you got the email and the BranchChat credit

Everything important is written inline on each tag, because Gmail and Outlook drop most <style> rules.
Pictures (banner, logo, group icon) always come from this server itself, as full https links.
If the server has no https address, the pictures are left out and the email still reads fine.
"""
import html
from urllib.parse import urlsplit

import bcconfig

# The brand name written in emails. Kept in step with accounts.EMAIL_BRAND (one word, "BranchChat").
BRAND = "BranchChat"


def _esc(value):
    """Make any real value (names, addresses, links) safe to put inside HTML."""
    return html.escape(str(value or ""), quote=True)


def _base_url():
    """This server's own public address, e.g. https://branchchat.app, or "" when it is not https."""
    url = (bcconfig.PUBLIC_URL or "").rstrip("/")
    return url if url.lower().startswith("https://") else ""


def _host():
    """The server's address as people see it (e.g. chat.example.com, or chat.example.com:8443)."""
    return urlsplit(bcconfig.PUBLIC_URL or "").netloc or bcconfig.PUBLIC_URL or ""


def _asset(name):
    """Full https link to one of the email pictures in web/static/email/ on this server.
    The ?v= part is the server version, so a new picture in a later version is never stale in caches."""
    base = _base_url()
    if not base:
        return ""
    return "%s/static/email/%s?v=%s" % (base, name, _esc(bcconfig.VERSION))


def server_name():
    """The name this server goes by in emails: its own group name if it has one
    (e.g. "The Woods family"), otherwise the plain brand name "BranchChat"."""
    import directory  # imported here so accounts.py -> mailtemplates.py never makes an import loop
    has_own = bcconfig.get("BC_ORG_NAME", "").strip() or bcconfig.INSTANCE_NAME
    return directory.org_name() if has_own else BRAND


def group_icon_url():
    """Full https link to the server's own icon (BC_ORG_ICON), or "" when it has none.
    It uses the existing public /directory-icon address; the ?h= part changes when the picture changes."""
    import directory
    base = _base_url()
    if not base:
        return ""
    raw, kind = directory.configured_org_icon()
    if not raw:
        return ""
    return "%s/directory-icon?h=%s" % (base, directory.icon_hash(raw))


# ---------- small building blocks (all colours are the light theme from the mock-up) ----------

def _p(inner_html):
    """One normal paragraph."""
    return '<p style="margin:0 0 14px 0;">%s</p>' % inner_html


def _bold(text):
    """A bold, darker word or name inside a paragraph."""
    return '<strong style="color:#13221a;">%s</strong>' % _esc(text)


def button(label, url):
    """The centred "bulletproof" button: a drawn (VML) one for Outlook, a rounded table cell for everyone else."""
    label, url = _esc(label), _esc(url)
    return '''<tr><td class="pad" align="center" style="padding:10px 40px 14px 40px;">
          <!--[if mso]><table role="presentation" align="center" cellpadding="0" cellspacing="0" border="0"><tr><td><v:roundrect xmlns:v="urn:schemas-microsoft-com:vml" href="%(url)s" style="height:48px;v-text-anchor:middle;width:240px;" arcsize="25%%" stroke="f" fillcolor="#2f7a49"><w:anchorlock/><center style="color:#ffffff;font-family:Segoe UI,Arial,sans-serif;font-size:16px;font-weight:bold;">%(label)s</center></v:roundrect></td></tr></table><![endif]-->
          <!--[if !mso]><!-->
          <table role="presentation" align="center" cellpadding="0" cellspacing="0" border="0" style="margin:0 auto;"><tr>
            <td class="btn" bgcolor="#2f7a49" style="background-color:#2f7a49; border-radius:12px; box-shadow:0 2px 8px rgba(0,0,0,.35);">
              <a href="%(url)s" style="display:inline-block; padding:13px 30px; font-family:'Segoe UI',Helvetica,Arial,sans-serif; font-size:16px; line-height:22px; font-weight:600; color:#ffffff; text-decoration:none; border-radius:12px;">%(label)s</a>
            </td></tr></table>
          <!--<![endif]-->
        </td></tr>''' % {"url": url, "label": label}


def link_fallback(url):
    """The "Button not working?" line with the full link written out."""
    url = _esc(url)
    return ('<p style="margin:0 0 10px 0;">Button not working? Copy this link into your browser:<br>'
            '<a href="%s" style="color:#256b3c; word-break:break-all;">%s</a></p>' % (url, url))


# Each header line is this tall; the icon is three lines tall so it matches the text block beside it.
HEADER_LINE_PX = 20
HEADER_ICON_PX = 3 * HEADER_LINE_PX


def group_header(lead):
    """Server/group header (Oct 19 design), three lines stacked:
         line 1: what is happening, ending in a colon   e.g. "Signing in to:"
         line 2: the family / group name on its own line e.g. "The Woods family"
         line 3: the server address on its own line      e.g. "chat.example.com"
    The server's own icon sits beside the block, sized to the height of all three lines, and the icon
    plus text are centred together as one group (so they line up with the centred button below).
    A server without its own icon gets just the three centred lines, with no empty picture box."""
    icon = group_icon_url()
    name = server_name()
    lead = lead.rstrip(":") + ":"
    cell = ""
    if icon:
        cell = '''<td valign="middle" width="%(px)d" style="width:%(px)dpx; padding-right:14px;">
                <img src="%(src)s" width="%(px)d" height="%(px)d" alt="%(alt)s icon" style="display:block; width:%(px)dpx; height:%(px)dpx; border:0; border-radius:13px; color:#526457; font-size:10px;">
              </td>
              ''' % {"px": HEADER_ICON_PX, "src": _esc(icon), "alt": _esc(name)}
    align = "left" if icon else "center"
    return '''<!-- Group header: "Lead:" / group name / server address, with the server's icon (if any) beside it. -->
            <table class="grp" role="presentation" align="center" cellpadding="0" cellspacing="0" border="0" style="margin:0 auto;"><tr>
              <!-- The three lines. Fixed line height (mso rule for Outlook) keeps them exactly icon-tall;
                   nowrap stops a narrow phone from splitting a line into two. -->
              %(cell)s<td valign="middle" align="%(align)s" style="font-family:'Segoe UI',Helvetica,Arial,sans-serif; font-size:14px; line-height:%(lh)dpx; mso-line-height-rule:exactly; color:#2a3a30; text-align:%(align)s; white-space:nowrap;">
                <span style="color:#2a3a30;">%(lead)s</span><br>
                <strong style="font-size:16px; line-height:%(lh)dpx; font-weight:600; color:#13221a;">%(name)s</strong><br>
                <span style="color:#526457;">%(host)s</span>
              </td>
            </tr></table>''' % {"cell": cell, "align": align, "lh": HEADER_LINE_PX, "lead": _esc(lead),
                                   "name": _esc(name), "host": _esc(_host())}


def server_line(lead):
    """The group header on its own (sign-in, reset and announcement emails), centred across the card."""
    return '''<!-- Server line: centred row holding the group header. -->
          <table role="presentation" width="100%%" cellpadding="0" cellspacing="0" border="0" style="margin:2px 0 16px 0;"><tr><td align="center">
            %s
          </td></tr></table>''' % group_header(lead)


def code_box(code):
    """The 6-digit code: big and in one piece ("123456") so a double-tap or drag copies it cleanly.
    26.10.9.1 hotfix: no space, &nbsp; or letter-spacing between the digits (they broke copy and paste)."""
    shown = _esc(code)
    return '''<!-- The code: big, one unbroken run of digits, easy to select and copy. -->
          <table role="presentation" width="100%%" cellpadding="0" cellspacing="0" border="0" style="margin:6px 0 18px 0;"><tr>
            <td align="center" bgcolor="#f0f4ee" class="code ibox" style="background-color:#f0f4ee; border:1px solid #cfdacb; border-radius:12px; padding:18px 10px; font-family:'SFMono-Regular',Consolas,'Liberation Mono',Menlo,monospace; font-size:40px; line-height:48px; font-weight:700; color:#13221a;"><span style="-webkit-user-select:all; user-select:all;">%s</span></td>
          </tr></table>''' % shown


def safety_box(text):
    """Warm "Didn't ask for this?" box, so it stands out without looking alarming."""
    return '''<!-- Safety note box, slightly warmer so it stands out without looking alarming. -->
          <table role="presentation" width="100%%" cellpadding="0" cellspacing="0" border="0" style="margin:6px 0 0 0;"><tr>
            <td bgcolor="#fbf2dc" style="background-color:#fbf2dc; border:1px solid #e5d3a6; border-radius:12px; padding:12px 16px; font-size:14px; line-height:21px; color:#5a471b;">
              <strong style="color:#4f3a0c;">Didn&rsquo;t ask for this?</strong> %s
            </td></tr></table>''' % _esc(text)


# ---------- the page around every email ----------

def page(subject, preheader, kind, headline, body, button_html, note, why, extra_footer=""):
    """Wrap one email's parts in the shared light layout. body/button_html/note/extra_footer are HTML
    already built by the helpers above; every other value is plain text and is escaped here."""
    logo, banner = _asset("bc-icon-96.png"), _asset("banner-light.jpg")
    logo_cell = ('<td valign="middle" style="padding-right:10px;"><img src="%s" width="36" height="36" alt="" '
                 'style="display:block; border:0; border-radius:9px;"></td>' % logo) if logo else ""
    # The banner row is left out completely when there is no https picture address (e.g. a LAN-only server).
    banner_row = ('''<tr><td style="border-radius:16px 16px 0 0; overflow:hidden; line-height:0; font-size:0;">
          <img src="%s" width="600" alt="A quiet campfire by a tent and a stream in the forest" style="display:block; width:100%%; max-width:600px; height:auto; border:0; border-radius:16px 16px 0 0; color:#5d6d62; font-family:Helvetica,Arial,sans-serif; font-size:13px; line-height:18px;">
        </td></tr>''' % banner) if banner else '<tr><td style="height:24px; line-height:24px; font-size:0;">&nbsp;</td></tr>'
    return _PAGE % {
        "subject": _esc(subject), "preheader": _esc(preheader), "kind": _esc(kind), "headline": _esc(headline),
        "body": body, "button": button_html, "note": note, "why": _esc(why), "extra_footer": extra_footer,
        "logo_cell": logo_cell, "banner_row": banner_row,
    }


# The light-theme page from the mock-up (_template.html with the "light" colours). %(...)s marks the slots.
_PAGE = '''<!DOCTYPE html>
<html lang="en" xmlns="http://www.w3.org/1999/xhtml" xmlns:v="urn:schemas-microsoft-com:vml" xmlns:o="urn:schemas-microsoft-com:office:office">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<!-- Tell mail apps this email is already light, so they don't try to "invert" it and wash it out. -->
<meta name="color-scheme" content="light">
<meta name="supported-color-schemes" content="light">
<meta name="x-apple-disable-message-reformatting">
<title>%(subject)s</title>
<!--[if mso]><noscript><xml><o:OfficeDocumentSettings><o:PixelsPerInch>96</o:PixelsPerInch></o:OfficeDocumentSettings></xml></noscript><![endif]-->
<style>
  /* Only small extras live here. Everything important is written inline on each tag,
     because Gmail and Outlook throw away most <style> rules. */
  body { margin: 0; padding: 0; }
  a { color: #256b3c; }
  /* On phones, give the card a little less side padding so text has room. */
  @media (max-width: 480px) {
    .pad { padding-left: 22px !important; padding-right: 22px !important; }
    .code { font-size: 34px !important; }
    .h1 { font-size: 22px !important; }
    /* Invite box: a little less side padding on phones so the icon + three lines always fit. */
    .ipad { padding-left: 16px !important; padding-right: 16px !important; }
  }
</style>
</head>
<body style="margin:0; padding:0; background-color:#e7ece4;">
<!-- Hidden preview line: the grey text mail apps show next to the subject in the inbox list. -->
<div style="display:none; max-height:0; overflow:hidden; mso-hide:all;">%(preheader)s&#8199;&#65279;&#847;&#8199;&#65279;&#847;&#8199;&#65279;&#847;</div>

<!-- Outer page: a soft pale-sage colour. This is what shows behind everything. -->
<table role="presentation" width="100%%" cellpadding="0" cellspacing="0" border="0" bgcolor="#e7ece4" style="background-color:#e7ece4;">
<tr><td align="center" style="padding:24px 12px 32px 12px;">

  <!-- The email column: never wider than 600px, shrinks to fit phones. -->
  <!--[if mso]><table role="presentation" width="600" cellpadding="0" cellspacing="0" border="0"><tr><td><![endif]-->
  <table role="presentation" width="100%%" cellpadding="0" cellspacing="0" border="0" style="max-width:600px;">

    <!-- LOGO ROW: app icon + the two-tone BranchChat wordmark, same spot in every email. -->
    <tr><td align="left" style="padding:0 4px 14px 4px;">
      <table role="presentation" cellpadding="0" cellspacing="0" border="0"><tr>
        %(logo_cell)s
        <td valign="middle" style="font-family:Outfit,'Segoe UI',Helvetica,Arial,sans-serif; font-size:22px; line-height:26px; color:#1b2a20;">
          <!-- Matches the site: "Branch" bold, "Chat" light green, no space, small TM. -->
          <span style="font-weight:700; color:#1b2a20;">Branch</span><span style="font-weight:300; color:#2e7d48;">Chat</span><sup style="font-size:9px; line-height:0; color:#5d6d62; vertical-align:top; position:relative; top:2px;">TM</sup>
        </td>
      </tr></table>
    </td></tr>

    <!-- CARD: one rounded off-white box holding the banner, the message and the button. -->
    <tr><td class="card" bgcolor="#fbfcf9" style="background-color:#fbfcf9; border:1px solid #d3ddd0; border-radius:16px; overflow:hidden;">
      <table role="presentation" width="100%%" cellpadding="0" cellspacing="0" border="0">

        <!-- BANNER: the BranchChat stream/tent/campfire scene in morning light.
             Its bottom edge fades into the card colour so the two blend together. -->
        %(banner_row)s

        <!-- MESSAGE: the only part that changes between email types. -->
        <tr><td class="pad" style="padding:4px 40px 8px 40px; font-family:'Segoe UI',Helvetica,Arial,sans-serif; color:#26362c; font-size:16px; line-height:25px;">
          <!-- Small label so people can tell at a glance what kind of email this is. -->
          <p style="margin:0 0 6px 0; font-size:12px; line-height:16px; letter-spacing:1.5px; text-transform:uppercase; color:#2e7d48; font-weight:600;">%(kind)s</p>
          <h1 class="h1" style="margin:0 0 14px 0; font-size:25px; line-height:32px; font-weight:600; color:#13221a;">%(headline)s</h1>
          %(body)s
        </td></tr>

        <!-- BUTTON: same centred button in every email; emails without a button leave this slot empty. -->
        %(button)s

        <!-- SMALL PRINT inside the card: expiry and safety notes. -->
        <tr><td class="pad" style="padding:6px 40px 30px 40px; font-family:'Segoe UI',Helvetica,Arial,sans-serif; color:#45564a; font-size:14px; line-height:21px;">
          %(note)s
        </td></tr>
      </table>
    </td></tr>

    <!-- FOOTER: why you got this, the site link, and the BranchChat credit. Same in every email. -->
    <tr><td align="center" style="padding:22px 16px 0 16px; font-family:'Segoe UI',Helvetica,Arial,sans-serif; color:#4f6054; font-size:12px; line-height:18px;">
      <p style="margin:0 0 8px 0;">%(why)s</p>
      <p style="margin:0 0 8px 0;">This is an automatic message, so replies aren't read. Need help? Visit <a href="https://branchchat.app" style="color:#256b3c; text-decoration:underline;">branchchat.app</a>.</p>
      %(extra_footer)s
      <p style="margin:0; color:#56675b;">Sent by BranchChat&trade; &middot; private chat for families and friends</p>
    </td></tr>

  </table>
  <!--[if mso]></td></tr></table><![endif]-->

</td></tr>
</table>
</body>
</html>
'''


# ---------- the four emails ----------

def sign_in_code(email, code, subject, minutes):
    """Sign-in code email. minutes = how long the code works (the server's real limit)."""
    body = (_p("Enter this code on %s to finish signing in:" % _esc(BRAND))
            + server_line("Signing in to:") + code_box(code))
    note = (_p('This code works for <strong style="color:#2a3a30;">%d minutes</strong> and can only be used once.' % minutes)
            + '<p style="margin:0;">Didn&rsquo;t ask for this? You can safely ignore this email. Nobody can sign in '
              'without the code, and we&rsquo;ll never ask you to share it.</p>')
    return page(subject, "Use this code to finish signing in. It works for %d minutes." % minutes,
                "Sign-in code", "Here\u2019s your sign-in code", body, "", note,
                "You got this because someone asked to sign in to %s with %s." % (server_name(), email))


def reset_code(email, code, subject, minutes):
    """Password reset email. The server resets with a 6-digit code (no link), so it shows the code box."""
    body = (_p("We got a request to reset the password for your %s account. Enter this code on the reset "
               "screen to choose a new one:" % _esc(BRAND))
            + server_line("Resetting your sign-in for:") + code_box(code))
    note = (_p('This code works for <strong style="color:#2a3a30;">%d minutes</strong> and only once.' % minutes)
            + _p("Your message key backup was sealed with the old password, so it is removed. Sign in first on a "
                 "device you already use and it is saved again with the new password. On a new device, older "
                 "messages may not open.")
            + safety_box("Ignore this email and your account stays exactly as it is. %s will never ask for your "
                         "password or code by phone, text or chat." % BRAND))
    return page(subject, "Someone asked to reset the password for your account. This code works for %d minutes." % minutes,
                "Password reset", "Reset your password", body, "", note,
                "You got this because someone asked to reset the password for the account using %s on %s." % (email, server_name()))


def reset_link(email, username, link, subject, minutes):
    """26.10.13.8: admin-sent password reset with the username and a one-time link."""
    body = (_p("Hi %s, an administrator sent you this so you can get back into your %s account." % (_esc(username), _esc(BRAND)))
            + _p("Your username is: " + _bold(username))
            + server_line("Resetting your sign-in for:"))
    note = (link_fallback(link)
            + _p('This link works for <strong style="color:#2a3a30;">%d minutes</strong> and only once.' % minutes)
            + safety_box("If you didn't ask for this, ignore this email and your password stays the same."))
    return page(subject, "Your username is %s. Use the link to choose a new password." % username,
                "Password reset", "Reset your password", body, button("Choose a new password", link), note,
                "You got this because an administrator of %s sent a password reset to %s." % (server_name(), email))


def invite(email, link, subject, inviter, hours, access, extra_note=""):
    """Invite email. inviter = the person's display name ("" when unknown), hours = real invite lifetime,
    access = the sentence that says what kind of account this is, extra_note = the server's own invite note."""
    group = server_name()
    # "join The Woods family on BranchChat", or just "join BranchChat" on a server without its own name.
    place = _bold(group) if group == BRAND else "%s on %s" % (_bold(group), _esc(BRAND))
    if inviter:
        lead = "%s would like you to join %s, a private place to chat, share photos and keep up with each other." % (
            _bold(inviter), place)
        headline = "%s invited you to %s" % (inviter, group)
    else:
        lead = "You&rsquo;re invited to join %s, a private place to chat, share photos and keep up with each other." % place
        headline = "You\u2019re invited to %s" % group
    # Invite details box: centred, comfortable padding, up to 400px wide (full width on small phones),
    # holding the same three-line group header as the other emails. Who invited you is in the headline and lead.
    box = '''<!-- Invite details box: narrow, centred, with breathing room around the group header. -->
          <table role="presentation" align="center" width="100%%" cellpadding="0" cellspacing="0" border="0" style="max-width:400px; margin:4px auto 18px auto;"><tr>
            <td class="ibox ipad" align="center" bgcolor="#f0f4ee" style="background-color:#f0f4ee; border:1px solid #cfdacb; border-radius:12px; padding:18px 24px;">
            %s
            </td></tr></table>''' % group_header(("%s invited you to:" % inviter) if inviter else "You’re invited to:")
    life = "%d hours" % hours if hours % 24 or hours < 48 else "%d hours (%d days)" % (hours, hours // 24)
    again = ("If it runs out, just ask %s for a new one." % _esc(inviter)) if inviter else "If it runs out, just ask for a new one."
    note = (_p('This invite works once, for <strong style="color:#2a3a30;">%s</strong>. %s' % (life, again))
            + _p(_esc(access) + ((" " + _esc(extra_note)) if extra_note else ""))
            + link_fallback(link)
            + '<p style="margin:0;">%s</p>' % (
                ("Don&rsquo;t know %s? You can ignore this email and nothing will happen." % _esc(inviter)) if inviter
                else "Not expecting this? You can ignore this email and nothing will happen."))
    who = ("%s entered %s when sending a %s invite." % (inviter, email, BRAND)) if inviter else (
        "%s was entered when sending a %s invite." % (email, BRAND))
    return page(subject, ("%s saved you a seat by the fire." % inviter) if inviter else "You have a seat saved by the fire.",
                "Invitation", headline, _p(lead) + box, button("Accept invite", link), note,
                "You got this because " + who)


def unsubscribe_footer(url):
    """26.10.11: the visible footer link every feature update email carries (url = the recipient's own signed
    /feature-news/off link, or the "{{unsubscribe}}" placeholder that featuremail.personalize() fills in)."""
    if not url:
        return ""
    return ('<p style="margin:0 0 8px 0;"><a href="%s" style="color:#256b3c; text-decoration:underline;">'
            'Unsubscribe from feature update emails</a></p>' % _esc(url))


def announcement(email, subject, headline, paragraphs, button_label="", button_url="", image_url="", image_alt="",
                 off_url="", why=""):
    """Feature announcement email. 26.10.4: featuremail.py sends the newest one to brand-new accounts.
    paragraphs = plain-text paragraphs; image_url must be an https picture on this server (or "").
    why = the footer's "You got this because..." line (the default fits a feature-news mailing)."""
    body = server_line("New for you on:")
    if image_url:
        body += ('<!-- Optional picture for the announcement. Left out when there is no image. -->\n          '
                 '<img src="%s" width="520" alt="%s" style="display:block; width:100%%; max-width:520px; height:auto; '
                 'border:0; border-radius:12px; margin:2px 0 16px 0; color:#5d6d62; font-size:13px;">' % (_esc(image_url), _esc(image_alt)))
    body += "".join(_p(_esc(text)) for text in paragraphs)
    extra = unsubscribe_footer(off_url)
    return page(subject, paragraphs[0] if paragraphs else headline, "What\u2019s new", headline, body,
                button(button_label, button_url) if (button_label and button_url) else "",
                '<p style="margin:0;">Want the full list of changes? It&rsquo;s in the patch notes.</p>',
                why or "You got this because you have a %s account at %s and feature news is turned on." % (BRAND, email),
                extra)
