# Self-hosting Branch Chat Server

Run your own private, end-to-end encrypted Branch Chat for your family or group on a Raspberry Pi,
any Debian/Ubuntu machine, or anywhere Docker runs. One setup script asks a few questions,
personalises it, and gets it online.

> Status: preview. The installer works from an unpacked release package
> (https://github.com/NexusMcNexis/branch-chat-server/releases/latest). A ready-made Docker image is planned.

## Contents
- [Supported platforms](#supported-platforms)
- [What you need](#what-you-need)
- [Quick start (Raspberry Pi / Debian / Ubuntu)](#quick-start)
- [How people reach your server](#access-modes): Cloudflare Tunnel, LAN-only, bring-your-own proxy
- [Email](#email)
- [Docker / Docker Compose (any OS)](#docker)
- [Everyday tasks](#everyday-tasks): update, backup, restore, logs, admin commands, uninstall
- [Settings reference](#settings-reference)
- [Where things live](#where-things-live)
- [Security notes](#security-notes)
- [Troubleshooting](#troubleshooting)

## Supported platforms

| Platform | How to install | Guide | Tested |
|---|---|---|---|
| Debian 12, Ubuntu 24.04 (VPS or bare metal) | one-command, tarball, git (`.deb` coming soon) | [debian-ubuntu.md](server/debian-ubuntu.md) | Debian 12 + Ubuntu 24.04 containers |
| Raspberry Pi OS (Pi 3/4/5) | one-command, tarball (`.deb` coming soon) | [raspberry-pi.md](server/raspberry-pi.md) | not re-tested for packages |
| Fedora / RHEL / Rocky / Alma | one-command, tarball (`.rpm` coming soon) | [fedora-rhel.md](server/fedora-rhel.md) | Fedora 40 container |
| openSUSE | one-command (`.rpm` coming soon) | [fedora-rhel.md](server/fedora-rhel.md) | not yet |
| Arch Linux | one-command, tarball | [arch.md](server/arch.md) | not yet |
| Proxmox | Debian 12 VM/LXC, then Debian steps | [proxmox.md](server/proxmox.md) | not re-tested |
| Any Linux with Docker | Docker Compose | [Docker](#docker) | not re-tested |
| Windows / macOS | Docker Desktop | [docker-desktop.md](server/docker-desktop.md) | not yet |
| From source | `git clone` | [from-git.md](server/from-git.md) | not yet |
| Packages | `.deb` / `.rpm` | coming soon | — |

## What you need

| | Minimum | Recommended |
|---|---|---|
| Computer | Any 64-bit Linux box (older Pis should work for a few people, untested) | **Raspberry Pi 4 or 5** (2 GB RAM or more), or any small PC / VM |
| OS | Raspberry Pi OS (64-bit), Debian 11+, Ubuntu 22.04+ | Raspberry Pi OS **64-bit** (Bookworm) or Debian 12 |
| Storage | 8 GB SD card | **USB SSD**, or a good A2 SD card. Photos and files you share are stored here (encrypted). |
| Python | 3.9+ (native install; installed for you) | 3.11+ |
| Network | Internet access for the tunnel and email | Wired Ethernet |
| For access from anywhere | A domain name on Cloudflare (free plan) | |

Branch Chat Server is pure Python standard library plus `cryptography` (for Web Push). No database
server, so it is light enough for a Raspberry Pi.

HTTPS is required: browsers only allow the encryption keys, notifications and "install app" on
HTTPS pages (or `localhost`). Every access mode below gives you HTTPS.

## Quick start

On the Pi (or server), as a user with `sudo`:

```bash
# download branch-chat-server-<version>-install.tar.gz from
# https://github.com/NexusMcNexis/branch-chat-server/releases/latest
tar xzf branch-chat-server-*-install.tar.gz
cd branch-chat-server-*/
sudo ./server/install.sh
```

The wizard asks six things (press Enter to accept the value in brackets):

1. **Install method**: `native` (a system service; lightest, best on a Pi) or `docker`.
2. **Your group**: name (shown in page titles, the home-screen app name and invite emails),
   an optional accent colour (`#rrggbb`) and an optional background picture.
3. **Admin account**: your username, email and password. You become the owner.
4. **Access**: `tunnel`, `lan` or `local` (see below).
5. **Email**: an SMTP account for invites and sign-in codes, or `log` (codes are shown in the server log).
6. **Backups**: how many nightly backups to keep (default 14).

It then installs what is needed, creates a dedicated `branchchat` system user, writes
`/etc/branch-chat/config.env`, starts the service, checks `/healthz`, and turns on nightly backups.
**Re-running it is safe**: it keeps your data and your previous answers, and backs up first.

Then open your address, sign in, and use **Invite friend or family** in the menu.

### Non-interactive install

Every question has a flag (`sudo ./server/install.sh --help`). Example:

```bash
printf '%s\n' 'a-long-admin-password' | sudo tee /root/bc-admin-pw >/dev/null; sudo chmod 600 /root/bc-admin-pw
sudo ./server/install.sh -y --mode native --name "The Smith Family" \
  --admin sam --admin-email sam@example.com --admin-password-file /root/bc-admin-pw \
  --access tunnel --hostname chat.example.com --tunnel-token-file /root/cf-token \
  --mail smtp --mail-from "Smith Family Chat <chat@example.com>" \
  --smtp-host smtp.example.com --smtp-port 587 --smtp-user chat@example.com --smtp-password-file /root/smtp-pw
sudo shred -u /root/bc-admin-pw
```

## Access modes

### Cloudflare Tunnel (recommended: works from anywhere, no router changes)

The Pi makes an outbound connection to Cloudflare; Cloudflare serves your address over HTTPS.
No ports are opened on your router and your home IP is not revealed.

1. Add your domain to Cloudflare (free plan is fine).
2. In the Cloudflare dashboard go to **Zero Trust → Networks → Tunnels → Create a tunnel**,
   choose **Cloudflared**, name it (e.g. `branch-chat`), and copy the **token** from the install command
   it shows (the long string after `--token`). You do not need to run Cloudflare's command.
3. Run the installer with access `tunnel`, your chosen host name (e.g. `chat.example.com`) and the token.
   The token is stored in `/etc/branch-chat/secrets/tunnel.env`, readable only by root.
4. Back in the tunnel's **Public hostnames** tab, add: hostname `chat.example.com`, service
   `http://localhost:8766` (native) or `http://branch-chat:8766` (Docker).
5. Open `https://chat.example.com`.

Uploads go through Cloudflare, which limits a single request to 100 MB on the free plan; Branch Chat
already uploads large files in 8 MB chunks.

### LAN-only (home network only)

Choose access `lan`. The installer sets up [Caddy](https://caddyserver.com) with its own private
certificate authority and serves `https://<name>.local` (and `https://<the Pi's IP>`).
Nothing is reachable from the internet.

Each phone or computer must trust Caddy's root certificate **once**, or the browser will warn and
encryption features will not work:

- Native: `sudo cat /var/lib/caddy/.local/share/caddy/pki/authorities/local/root.crt`
- Docker: `sudo cat /opt/branch-chat/caddy-data/caddy/pki/authorities/local/root.crt`

Copy that file to the device and install it (Android: *Settings → Security → Encryption & credentials →
Install a certificate → CA certificate*; iPhone: open it, install the profile, then enable it in
*Settings → General → About → Certificate Trust Settings*; desktop: import into the browser or OS
certificate store as a trusted root). `.local` names need mDNS (Raspberry Pi OS has it); otherwise use the IP address.

Alternative: if you use Tailscale, choose `local` and run `tailscale serve --bg 8766` to get a
trusted HTTPS address on your tailnet.

### Local (bring your own proxy)

Choose access `local`: the server listens on `127.0.0.1:8766` only, and you put your own HTTPS
reverse proxy (Caddy, nginx, Traefik) or VPN in front. Pass the public host name so links in emails
are right. Forward the `Host` header unchanged.

## Email

New devices confirm sign-in with a 6-digit code sent by email; invites and password resets are
emailed too. Choose one:

- **`smtp`** (recommended): any provider works (your ISP, Fastmail, Proton Bridge, Mailgun, Gmail with
  an *app password*, ...). Port 587 uses STARTTLS, port 465 uses TLS. The password is stored in
  `/etc/branch-chat/secrets/smtp_password` (root and the service only), never in `config.env`.
- **`log`**: nothing is emailed; the code and invite link are written to the server log, and the admin
  reads them with `sudo ./server/install.sh logs` and passes them on (or creates invite links with
  `manage.py invite`). Good for testing or a household where the admin is always around.

## Docker

The same image runs on `linux/amd64` and `linux/arm64` (Raspberry Pi 4/5 with a 64-bit OS, Apple
Silicon, most servers). On Windows use Docker Desktop (WSL 2 backend).

The easiest way is still the installer with `--mode docker` (it installs Docker Engine from Docker's
official repository if needed, builds the image, writes `/opt/branch-chat/docker-compose.yml`, and
sets up backups). To do it by hand:

```bash
cd branch-chat/server
cp config.env.example config.env && chmod 600 config.env     # edit it: BC_PUBLIC_URL, name, owner, mail
# (for SMTP in Docker, use BC_SMTP_PASSWORD in config.env, or mount a file and point BC_SMTP_PASSWORD_FILE at it)
mkdir -p data && sudo chown 10001:10001 data && chmod 700 data
docker compose build
docker compose run --rm branch-chat python manage.py create-admin --username sam --email sam@example.com
docker compose up -d                                         # http://127.0.0.1:8766/healthz -> ok
# With a Cloudflare Tunnel: put TUNNEL_TOKEN=... in server/.env (chmod 600), then
docker compose --profile tunnel up -d
```

Image details: non-root user (UID 10001), all state in the `/data` volume, read-only root file
system, all capabilities dropped, `HEALTHCHECK` on `/healthz`. Nothing secret is baked into the image.
Run exactly **one** container per data directory.

Build for both architectures (needs `docker buildx`; on an x86 PC also QEMU/binfmt):

```bash
docker buildx build --platform linux/amd64,linux/arm64 -t youruser/branch-chat:$(cat VERSION) --push .
```

## Everyday tasks

All commands run from the folder you installed from.

| Task | Command |
|---|---|
| Status and health | `sudo ./server/install.sh status` |
| Update | `sudo ./server/install.sh update` |
| Back up now | `sudo ./server/install.sh backup` |
| Restore | `sudo ./server/install.sh restore /var/backups/branch-chat/branch-chat-YYYYmmdd-HHMMSS.tar.gz` |
| Server log (and codes in `log` mail mode) | `sudo ./server/install.sh logs` |
| Change settings | profile menu > **Administrator** > Settings (owner only; backs up the old file and restarts for you), re-run `sudo ./server/install.sh`, or edit `/etc/branch-chat/config.env` and `sudo systemctl restart branch-chat` |
| Uninstall (keep data) | `sudo ./server/install.sh uninstall` |
| Uninstall and delete everything | `sudo ./server/install.sh uninstall --purge` |

**Update** pulls the latest code (`git pull --ff-only` in a git checkout; or unpack a newer release
into the folder first), makes a backup, installs the new version next to the old one, restarts and
checks `/healthz`. If the new version does not come up healthy it **rolls back automatically** to the
previous version (native: the previous release folder; Docker: the `branch-chat:previous` image).

**Backups** run nightly at about 03:30 (systemd timer `branch-chat-backup.timer`) into
`/var/backups/branch-chat/` as `branch-chat-YYYYmmdd-HHMMSS.tar.gz` (mode 600), keeping the newest 14
(or what you chose). Each contains the data directory and `/etc/branch-chat` (including secrets),
so keep copies somewhere safe, e.g. copy them to a NAS or USB drive:
`rsync -a /var/backups/branch-chat/ /mnt/usb/branch-chat-backups/`.
A backup is skipped (and the job fails) when free space is low. Messages and files in backups are
end-to-end encrypted, but account data, key backups and secrets are in there too: treat backups as sensitive.

**Restore** stops the server, moves the current data aside to `/var/lib/branch-chat.before-restore-<time>`
(nothing is deleted), unpacks the backup, fixes ownership and starts the server again.

**Admin commands** (`manage.py`):

```bash
# native
sudo -u branchchat BC_CONFIG=/etc/branch-chat/config.env BC_STATE_DIR=/var/lib/branch-chat \
  python3 /opt/branch-chat/app/manage.py status
# docker
sudo docker compose -f /opt/branch-chat/docker-compose.yml -p branch-chat exec branch-chat python manage.py status
```

`status`, `create-admin`, `invite --email E [--role member|admin]` (prints an invite link),
`set-password --username U`, `healthcheck`, `migrate`. Stop the server before commands that change
accounts (`create-admin`, `set-password`), or use the web UI.

## Settings reference

`/etc/branch-chat/config.env` (native) or the `env_file` (Docker). `KEY=VALUE`, one per line;
environment variables override the file. Full annotated list: [`server/config.env.example`](../server/config.env.example).

| Setting | Default | Meaning |
|---|---|---|
| `BC_PUBLIC_URL` | `http://localhost:8766` | The address people open; used in emails, links and push |
| `BC_HOSTS` | (host of `BC_PUBLIC_URL`) | Extra host names/IPs the server answers to |
| `BC_HOST` / `BC_PORT` | `127.0.0.1` / `8766` | Listen address |
| `BC_STATE_DIR` | `/var/lib/huzz-chat` | Data directory (installer uses `/var/lib/branch-chat`) |
| `BC_APP_DIR` | folder of `server.py` | Where the HTML/JS/CSS are |
| `BC_INSTANCE_NAME` | (none) | Group name in titles, app name and emails |
| `BC_ACCENT` | (none) | `#rrggbb` accent for the default theme |
| `BC_BACKGROUND` | (none) | Background picture in `STATE_DIR/branding/` |
| `BC_OWNER` | `admin` | Owner username (owner tools) |
| `BC_MAIL_MODE` | `sendmail` (`smtp` if `BC_SMTP_HOST` set) | `sendmail`, `smtp` or `log` |
| `BC_MAIL_FROM` | `Branch Chat <noreply@localhost>` | From address |
| `BC_SMTP_HOST/PORT/USER/TLS` | `-` / `587` / `-` / `starttls` (`ssl` on 465) | SMTP server |
| `BC_SMTP_PASSWORD_FILE` | | File with the SMTP password (or `BC_SMTP_PASSWORD`) |
| `BC_SENDMAIL` | `/usr/sbin/sendmail` | sendmail binary for `sendmail` mode |
| `BC_INVITE_NOTE` | (empty) | Extra line in invite emails |

The installer always writes all the values that matter for your server.

## Where things live

| Path | What |
|---|---|
| `/opt/branch-chat/app` → `releases/<time>-<version>` | The code (native; newest 3 releases kept for rollback) |
| `/opt/branch-chat/docker-compose.yml` | Docker install |
| `/etc/branch-chat/config.env` | Settings (640 root:branchchat) |
| `/etc/branch-chat/secrets/` | SMTP password, tunnel token |
| `/var/lib/branch-chat/` | All data: accounts, encrypted messages and files, push keys (700) |
| `/var/backups/branch-chat/` | Nightly backups (700) |
| `/etc/systemd/system/branch-chat*.service/.timer` | Services |
| `/var/log/branch-chat-install.log` | Installer log |

## Security notes

- The server stores and relays **ciphertext only**; it cannot read messages or files.
- Native service hardening: dedicated `branchchat` user, `NoNewPrivileges`, `ProtectSystem=strict`,
  `ProtectHome`, `PrivateTmp`, `PrivateDevices`, no capabilities, restricted address families,
  writable only in its state directory (`systemd-analyze security branch-chat` scores about 3.0 "OK").
- The tunnel token is readable only by root; the SMTP password only by root and the service.
- The server listens on localhost; only the tunnel or Caddy is exposed.
- Keep the OS updated (`sudo apt update && sudo apt full-upgrade`), and consider unattended-upgrades.
- Run one server per data directory; do not copy the data directory while the server is writing
  (use the backup command).

## Troubleshooting

- **`/healthz` does not answer**: `sudo ./server/install.sh logs` (native: `journalctl -u branch-chat -n 80`).
- **"That page was not found." for every page**: the host name in the browser is not `BC_PUBLIC_URL`'s
  host or in `BC_HOSTS`.
- **Login works but the app asks for a code that never arrives**: check SMTP settings, or switch to
  `log` mode and read the code from the logs.
- **Browser warns about the certificate (LAN mode)**: install Caddy's root certificate on that device.
- **Tunnel shows 502/1033**: check the public hostname's service URL in Cloudflare and
  `systemctl status branch-chat-tunnel` (Docker: `docker compose ... logs cloudflared`).


## The Administrator page (0.23.3-beta.15)

The owner account (`BC_OWNER`) gets an **Administrator** entry at the bottom of the profile menu and in Admin Tools.
It opens `/admin`, which nobody else can load (the server answers "not found"), and asks the owner to confirm it's them
first: sign in again (password, then the authenticator code when two-factor is on, then an emailed code when this browser
was never verified), or approve the request on another device where the owner is already signed in. The admin session
lasts 15 minutes.

The page holds the account and invite tools, the directory listing status (with pause/resume), version and update
information, report and filter tools on public servers, and **every setting in the settings file**:

- The file's path is shown at the top of Settings. Native: `/etc/branch-chat/config.env`. Docker: settings in the
  env_file win and are shown locked; the page saves its changes to `config.env` in the data folder.
- Changes are only staged until **Apply**. Apply validates every value, keeps a dated copy of the old file in
  `config-backups/` (next to the file, or in the data folder if that folder is read-only), writes the new file atomically
  and restarts the server in place. If the server cannot start with the new file, it restores the backup and starts again.
- **Cancel** discards the staged changes; leaving the page with staged changes asks to apply or discard them.
- Secrets (`BC_SMTP_PASSWORD`, `BC_TURNSTILE_SECRET`, anything named like a password, token or key) are never sent to the
  browser; they can only be replaced or removed.
- Listen address/port, data and program folders and the admin-panel listener are shown but locked: edit those by hand.
- Native installs need the service to be allowed to write the file: `config.env` is `660 root:branchchat` and the unit
  has `ReadWritePaths=/etc/branch-chat/config.env` (install.sh sets both). Without that, the page shows the file as
  read-only and Apply is off.
