#!/usr/bin/env bash
# BranchChat Server installer for Raspberry Pi OS, Debian and Ubuntu (arm64 and amd64).
#
#   sudo ./install.sh                 interactive setup wizard (safe to re-run; keeps your answers)
#   sudo ./install.sh -y [options]    non-interactive (see --help)
#   sudo ./install.sh update          back up, install the code from this folder (git pull if it is a
#                                     git checkout), restart, health-check, roll back on failure
#   sudo ./install.sh backup          make a backup now
#   sudo ./install.sh restore FILE    restore a backup (the current state is kept aside first)
#   sudo ./install.sh status          show what is installed and whether it is healthy
#   sudo ./install.sh logs            recent server log (sign-in codes appear here with mail mode "log")
#   sudo ./install.sh uninstall [--purge]   remove services and code; --purge also deletes data + backups
#
# Nothing is ever deleted without a backup or an explicit --purge.
set -Eeuo pipefail
umask 022

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
SRC_ROOT=$(cd "$SCRIPT_DIR/.." && pwd)            # repo / release root (contains web/ and server/)
ETC=/etc/branch-chat
CONFIG=$ETC/config.env
META=$ETC/install.env
SECRETS=$ETC/secrets
BASE=/opt/branch-chat
APP_LINK=$BASE/app
RELEASES=$BASE/releases
STATE_DIR=/var/lib/branch-chat
BACKUP_DIR=/var/backups/branch-chat
SVC_USER=branchchat
DOCKER_UID=10001
PORT_DEFAULT=8766
LOG=/var/log/branch-chat-install.log

# ---------- output helpers ----------
c_b=$'\e[1m'; c_g=$'\e[32m'; c_y=$'\e[33m'; c_r=$'\e[31m'; c_0=$'\e[0m'
[ -t 1 ] || { c_b=; c_g=; c_y=; c_r=; c_0=; }
say()  { printf '%s\n' "$*"; }
step() { printf '\n%s==> %s%s\n' "$c_b" "$*" "$c_0"; log "STEP $*"; }
ok()   { printf '%s  ok%s %s\n' "$c_g" "$c_0" "$*"; }
warn() { printf '%s  warning:%s %s\n' "$c_y" "$c_0" "$*" >&2; log "WARN $*"; }
die()  { printf '%serror:%s %s\n' "$c_r" "$c_0" "$*" >&2; log "ERROR $*"; exit 1; }
log()  { [ -w "$(dirname "$LOG")" ] && printf '%s %s\n' "$(date '+%F %T')" "$*" >> "$LOG" 2>/dev/null || true; }
run()  { log "RUN $*"; "$@"; }
trap 'die "failed at line $LINENO: $BASH_COMMAND (details: $LOG)"' ERR

usage() {
  sed -n '2,16p' "$0" | sed 's/^# \{0,1\}//'
  cat <<'U'

Options for install (each also has an environment variable, e.g. --name / BC_NAME):
  -y, --non-interactive        never prompt; use options, then existing config, then defaults
  --mode native|docker         native = systemd service (default); docker = Docker Compose
  --name NAME                  group or family name (BC_NAME)
  --admin USER                 admin (owner) username (BC_ADMIN)
  --admin-email EMAIL          admin email (BC_ADMIN_EMAIL)
  --admin-password-file FILE   file holding the admin password (BC_ADMIN_PASSWORD_FILE); or set BC_ADMIN_PASSWORD
  --accent '#rrggbb'           accent colour (BC_ACCENT)
  --background FILE            background picture (.jpg/.png/.webp) (BC_BACKGROUND_FILE)
  --access tunnel|lan|local    Cloudflare Tunnel | LAN-only HTTPS via Caddy | localhost only, bring your own proxy
  --hostname HOST              public host name, e.g. chat.example.com (tunnel) or branchchat.local (lan)
  --tunnel-token-file FILE     Cloudflare Tunnel token (BC_TUNNEL_TOKEN_FILE); or set BC_TUNNEL_TOKEN
  --mail smtp|log              how sign-in codes and invites are sent (BC_MAIL)
  --mail-from 'Name <addr>'    sender address (BC_MAIL_FROM)
  --smtp-host H --smtp-port P --smtp-user U --smtp-tls starttls|ssl|none
  --smtp-password-file FILE    (BC_SMTP_PASSWORD_FILE_IN); or set BC_SMTP_PASSWORD
  --port N                     local port (default 8766)
  --keep N                     nightly backups to keep (default 14)
  --skip-packages              do not apt-get install anything (expects dependencies present)
U
}

# ---------- small utilities ----------
need_root() { [ "$(id -u)" -eq 0 ] || die "run with sudo: sudo $0"; }
has_systemd() { [ -d /run/systemd/system ]; }
cfg_get() {  # cfg_get KEY FILE -> value from a KEY=VALUE file (no execution)
  local f=${2:-$CONFIG}; [ -r "$f" ] || return 0
  sed -n "s/^$1=//p" "$f" | tail -n1 | sed -e 's/^"\(.*\)"$/\1/' -e "s/^'\(.*\)'$/\1/"
}
ask() {  # ask VAR "Question" default
  local __v=$1 q=$2 d=${3:-} a
  if [ "$NONINTERACTIVE" = 1 ]; then printf -v "$__v" '%s' "$d"; return; fi
  if [ -n "$d" ]; then read -r -p "  $q [$d]: " a </dev/tty || true; else read -r -p "  $q: " a </dev/tty || true; fi
  printf -v "$__v" '%s' "${a:-$d}"
}
ask_secret() {  # ask_secret VAR "Question"  (not echoed)
  local __v=$1 q=$2 a
  [ "$NONINTERACTIVE" = 1 ] && { printf -v "$__v" '%s' ""; return; }
  read -r -s -p "  $q: " a </dev/tty || true; echo
  printf -v "$__v" '%s' "$a"
}
write_file() {  # write_file PATH MODE OWNER:GROUP  (content on stdin; atomic; keeps a .bak of a changed file)
  local path=$1 mode=$2 own=$3 tmp
  tmp=$(mktemp "$(dirname "$path")/.tmp.XXXXXX")
  cat > "$tmp"; chmod "$mode" "$tmp"; chown "$own" "$tmp"
  if [ -f "$path" ] && cmp -s "$tmp" "$path"; then rm -f "$tmp"; return 0; fi
  [ -f "$path" ] && cp -p "$path" "$path.bak"
  mv -f "$tmp" "$path"
}
valid_user()  { [[ $1 =~ ^[A-Za-z0-9][A-Za-z0-9._-]{2,31}$ ]]; }
valid_email() { [[ $1 =~ ^[^@[:space:]]+@[^@[:space:]]+\.[^@[:space:]]+$ ]]; }
valid_host()  { [[ $1 =~ ^[A-Za-z0-9]([A-Za-z0-9.-]{0,251}[A-Za-z0-9])?$ ]]; }
no_nl()       { [[ $1 != *$'\n'* && $1 != *$'\r'* ]]; }
lan_ip() { ip -4 route get 1.1.1.1 2>/dev/null | awk '{for(i=1;i<=NF;i++) if($i=="src"){print $(i+1); exit}}'; }

load_os() {
  [ -r /etc/os-release ] || die "cannot read /etc/os-release"
  . /etc/os-release
  OS_ID=${ID:-}; OS_LIKE=${ID_LIKE:-}; OS_CODENAME=${VERSION_CODENAME:-}
  case " $OS_ID $OS_LIKE " in
    *" debian "*|*" ubuntu "*|*" raspbian "*) ;;
    *) die "this installer supports Raspberry Pi OS, Debian and Ubuntu (found: ${PRETTY_NAME:-unknown}). Use Docker instead (docs/self-hosting.md)." ;;
  esac
  ARCH=$(dpkg --print-architecture)
  case "$ARCH" in amd64|arm64) ;; armhf) warn "32-bit Raspberry Pi OS: native mode works, but use a 64-bit OS for Docker and for best speed." ;; *) warn "untested architecture $ARCH" ;; esac
}

apt_install() {
  [ "$SKIP_PACKAGES" = 1 ] && return 0
  local missing=() p
  for p in "$@"; do dpkg-query -W -f='${Status}' "$p" 2>/dev/null | grep -q "install ok installed" || missing+=("$p"); done
  [ ${#missing[@]} -eq 0 ] && return 0
  step "Installing packages: ${missing[*]}"
  export DEBIAN_FRONTEND=noninteractive
  run apt-get update -qq >>"$LOG" 2>&1 || warn "apt-get update reported problems (continuing)"
  run apt-get install -y -qq --no-install-recommends "${missing[@]}" >>"$LOG" 2>&1 || die "apt-get install ${missing[*]} failed (see $LOG)"
}

repo_codename() {  # codename for third-party apt repos; Raspberry Pi OS uses Debian's
  echo "${OS_CODENAME:-bookworm}"
}

install_docker() {
  if docker compose version >/dev/null 2>&1; then ok "Docker and Compose are installed"; return; fi
  [ "$SKIP_PACKAGES" = 1 ] && die "Docker with the compose plugin is required (--skip-packages given)"
  step "Installing Docker Engine from Docker's official apt repository"
  apt_install ca-certificates curl gnupg
  local distro=debian; case " $OS_ID " in *" ubuntu "*) distro=ubuntu ;; esac
  install -m 0755 -d /etc/apt/keyrings
  curl -fsSL "https://download.docker.com/linux/$distro/gpg" | gpg --dearmor --yes -o /etc/apt/keyrings/docker.gpg
  chmod a+r /etc/apt/keyrings/docker.gpg
  echo "deb [arch=$ARCH signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/$distro $(repo_codename) stable" \
    | write_file /etc/apt/sources.list.d/docker.list 644 root:root
  run apt-get update -qq >>"$LOG" 2>&1
  run apt-get install -y -qq docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin >>"$LOG" 2>&1 || die "Docker install failed (see $LOG)"
  has_systemd && run systemctl enable --now docker >>"$LOG" 2>&1 || true
  docker compose version >/dev/null 2>&1 || die "docker compose is still not available"
}

install_cloudflared_native() {
  command -v cloudflared >/dev/null 2>&1 && { ok "cloudflared is installed"; return; }
  [ "$SKIP_PACKAGES" = 1 ] && die "cloudflared is required for --access tunnel (--skip-packages given)"
  step "Installing cloudflared from Cloudflare's apt repository"
  apt_install ca-certificates curl gnupg
  install -m 0755 -d /usr/share/keyrings
  curl -fsSL https://pkg.cloudflare.com/cloudflare-main.gpg -o /usr/share/keyrings/cloudflare-main.gpg
  echo "deb [signed-by=/usr/share/keyrings/cloudflare-main.gpg] https://pkg.cloudflare.com/cloudflared any main" \
    | write_file /etc/apt/sources.list.d/cloudflared.list 644 root:root
  run apt-get update -qq >>"$LOG" 2>&1
  run apt-get install -y -qq cloudflared >>"$LOG" 2>&1 || die "cloudflared install failed (see $LOG)"
}

# ---------- settings: options > environment > existing config > defaults ----------
NONINTERACTIVE=0; SKIP_PACKAGES=0; PURGE=0
O_MODE=${BC_MODE:-}; O_NAME=${BC_NAME:-}; O_ADMIN=${BC_ADMIN:-}; O_ADMIN_EMAIL=${BC_ADMIN_EMAIL:-}
O_ADMIN_PWFILE=${BC_ADMIN_PASSWORD_FILE:-}; O_ACCENT=${BC_ACCENT_IN:-${BC_ACCENT:-}}; O_BG=${BC_BACKGROUND_FILE:-}
O_ACCESS=${BC_ACCESS:-}; O_HOSTNAME=${BC_HOSTNAME:-}; O_TUNNEL_FILE=${BC_TUNNEL_TOKEN_FILE:-}
O_MAIL=${BC_MAIL:-}; O_MAIL_FROM=${BC_MAIL_FROM_IN:-}; O_SMTP_HOST=${BC_SMTP_HOST_IN:-}; O_SMTP_PORT=${BC_SMTP_PORT_IN:-}
O_SMTP_USER=${BC_SMTP_USER_IN:-}; O_SMTP_TLS=${BC_SMTP_TLS_IN:-}; O_SMTP_PWFILE=${BC_SMTP_PASSWORD_FILE_IN:-}
O_PORT=${BC_PORT_IN:-}; O_KEEP=${BC_KEEP:-}
CMD=install; RESTORE_FILE=

parse_args() {
  while [ $# -gt 0 ]; do
    case "$1" in
      install|update|backup|restore|status|logs|uninstall) CMD=$1; [ "$1" = restore ] && { RESTORE_FILE=${2:-}; shift; } ;;
      -y|--non-interactive|--yes) NONINTERACTIVE=1 ;;
      --mode) O_MODE=$2; shift ;;
      --name) O_NAME=$2; shift ;;
      --admin) O_ADMIN=$2; shift ;;
      --admin-email) O_ADMIN_EMAIL=$2; shift ;;
      --admin-password-file) O_ADMIN_PWFILE=$2; shift ;;
      --accent) O_ACCENT=$2; shift ;;
      --background) O_BG=$2; shift ;;
      --access) O_ACCESS=$2; shift ;;
      --hostname) O_HOSTNAME=$2; shift ;;
      --tunnel-token-file) O_TUNNEL_FILE=$2; shift ;;
      --mail) O_MAIL=$2; shift ;;
      --mail-from) O_MAIL_FROM=$2; shift ;;
      --smtp-host) O_SMTP_HOST=$2; shift ;;
      --smtp-port) O_SMTP_PORT=$2; shift ;;
      --smtp-user) O_SMTP_USER=$2; shift ;;
      --smtp-tls) O_SMTP_TLS=$2; shift ;;
      --smtp-password-file) O_SMTP_PWFILE=$2; shift ;;
      --port) O_PORT=$2; shift ;;
      --keep) O_KEEP=$2; shift ;;
      --skip-packages) SKIP_PACKAGES=1 ;;
      --purge) PURGE=1 ;;
      -h|--help) usage; exit 0 ;;
      *) die "unknown option: $1 (see --help)" ;;
    esac
    shift
  done
}

read_file_secret() { [ -n "$1" ] || return 0; [ -r "$1" ] || die "cannot read $1"; head -n1 "$1" | tr -d '\r\n'; }

gather() {
  local prev_mode prev_access prev_url prev_host d
  prev_mode=$(cfg_get BC_INSTALL_MODE "$META"); prev_access=$(cfg_get BC_INSTALL_ACCESS "$META")
  prev_url=$(cfg_get BC_PUBLIC_URL); prev_host=${prev_url#https://}; prev_host=${prev_host%%/*}

  if [ "$NONINTERACTIVE" = 0 ]; then
    say ""; say "${c_b}BranchChat Server setup${c_0}"
    say "Press Enter to keep the value in [brackets]. Re-running this later is safe; your data is kept."
  fi
  step "1/6 Install method"
  d=${O_MODE:-${prev_mode:-native}}
  [ "$NONINTERACTIVE" = 0 ] && say "  native = runs as a system service (lightest, best on a Raspberry Pi); docker = runs in Docker"
  ask MODE "native or docker" "$d"
  case "$MODE" in native|docker) ;; *) die "--mode must be native or docker" ;; esac
  [ -n "$prev_mode" ] && [ "$prev_mode" != "$MODE" ] && die "this machine already has a $prev_mode install. Run '$0 uninstall' first (your data is kept), then install again with --mode $MODE."

  step "2/6 Your group"
  ask NAME "Group or family name" "${O_NAME:-$(cfg_get BC_INSTANCE_NAME)}"
  [ -n "$NAME" ] || NAME="BranchChat"; no_nl "$NAME" && [ ${#NAME} -le 60 ] || die "name must be one line, 60 characters or fewer"
  ask ACCENT "Accent colour (#rrggbb, blank = default blue)" "${O_ACCENT:-$(cfg_get BC_ACCENT)}"
  ACCENT=$(printf '%s' "$ACCENT" | tr 'A-F' 'a-f')
  [ -z "$ACCENT" ] || [[ $ACCENT =~ ^#[0-9a-f]{6}$ ]] || die "accent must look like #3c92ea"
  ask BG_FILE "Background picture file (.jpg/.png/.webp, blank = keep current/default forest)" "$O_BG"
  if [ -n "$BG_FILE" ]; then
    [ -r "$BG_FILE" ] || die "cannot read $BG_FILE"
    case "${BG_FILE,,}" in *.jpg|*.jpeg|*.png|*.webp) ;; *) die "background must be .jpg, .png or .webp" ;; esac
    [ "$(stat -c %s "$BG_FILE")" -le $((8*1024*1024)) ] || die "background picture must be 8 MB or smaller"
  fi

  step "3/6 Admin account (you)"
  ask ADMIN "Admin username" "${O_ADMIN:-$(cfg_get BC_OWNER)}"
  valid_user "$ADMIN" || die "usernames are 3 to 32 letters, numbers, dots, or dashes"
  ask ADMIN_EMAIL "Admin email (for sign-in codes)" "${O_ADMIN_EMAIL:-$(cfg_get BC_INSTALL_ADMIN_EMAIL "$META")}"
  valid_email "$ADMIN_EMAIL" || die "enter a real email address"
  ADMIN_PW=${BC_ADMIN_PASSWORD:-$(read_file_secret "$O_ADMIN_PWFILE")}

  step "4/6 How people reach it"
  if [ "$NONINTERACTIVE" = 0 ]; then
    say "  tunnel = Cloudflare Tunnel: works from anywhere, no router changes, free (needs a domain on Cloudflare)"
    say "  lan    = only on your home network, HTTPS with a private certificate (devices must trust it once)"
    say "  local  = only this machine; you will put your own reverse proxy or VPN in front"
  fi
  ask ACCESS "tunnel, lan or local" "${O_ACCESS:-${prev_access:-tunnel}}"
  TUNNEL_TOKEN=
  case "$ACCESS" in
    tunnel)
      ask PUB_HOST "Public host name (e.g. chat.example.com)" "${O_HOSTNAME:-$prev_host}"
      valid_host "$PUB_HOST" || die "enter a host name like chat.example.com"
      TUNNEL_TOKEN=${BC_TUNNEL_TOKEN:-$(read_file_secret "$O_TUNNEL_FILE")}
      if [ -z "$TUNNEL_TOKEN" ] && [ ! -s "$SECRETS/tunnel.env" ]; then
        [ "$NONINTERACTIVE" = 0 ] && say "  Create a tunnel at https://one.dash.cloudflare.com -> Networks -> Tunnels, choose 'Cloudflared', copy the token."
        ask_secret TUNNEL_TOKEN "Paste the tunnel token (input hidden)"
        [ -n "$TUNNEL_TOKEN" ] || die "a tunnel token is required for --access tunnel (or use --tunnel-token-file)"
      fi
      [ -z "$TUNNEL_TOKEN" ] || [[ $TUNNEL_TOKEN =~ ^[A-Za-z0-9._=+/-]{40,}$ ]] || die "that does not look like a tunnel token"
      ;;
    lan)
      d=${O_HOSTNAME:-$prev_host}; [ -n "$d" ] || d="$(hostname -s 2>/dev/null || echo branchchat).local"
      ask PUB_HOST "Name to open on your network" "$d"
      valid_host "$PUB_HOST" || die "enter a host name like branchchat.local"
      ;;
    local)
      ask PUB_HOST "Public host name your proxy will use" "${O_HOSTNAME:-${prev_host:-localhost}}"
      valid_host "$PUB_HOST" || die "enter a host name"
      ;;
    *) die "--access must be tunnel, lan or local" ;;
  esac
  PUBLIC_URL="https://$PUB_HOST"; [ "$PUB_HOST" = localhost ] && PUBLIC_URL="http://localhost:${O_PORT:-$PORT_DEFAULT}"

  step "5/6 Email for invites and sign-in codes"
  [ "$NONINTERACTIVE" = 0 ] && say "  New devices confirm sign-in with an emailed code. Without SMTP, codes are written to the server log (see: $0 logs)."
  d=${O_MAIL:-$(cfg_get BC_MAIL_MODE)}; [ -n "$d" ] || d=log; [ "$d" = sendmail ] && d=log
  ask MAIL "smtp or log" "$d"
  case "$MAIL" in smtp|log) ;; *) die "--mail must be smtp or log" ;; esac
  ask MAIL_FROM "Sender (Name <address>)" "${O_MAIL_FROM:-$(cfg_get BC_MAIL_FROM)}"
  [ -n "$MAIL_FROM" ] || MAIL_FROM="$NAME <$ADMIN_EMAIL>"
  no_nl "$MAIL_FROM" || die "sender must be one line"
  SMTP_PW=
  if [ "$MAIL" = smtp ]; then
    ask SMTP_HOST "SMTP server" "${O_SMTP_HOST:-$(cfg_get BC_SMTP_HOST)}"; valid_host "$SMTP_HOST" || die "enter an SMTP host name"
    ask SMTP_PORT "SMTP port" "${O_SMTP_PORT:-$(cfg_get BC_SMTP_PORT)}"; [ -n "$SMTP_PORT" ] || SMTP_PORT=587
    [[ $SMTP_PORT =~ ^[0-9]+$ ]] || die "port must be a number"
    d=${O_SMTP_TLS:-$(cfg_get BC_SMTP_TLS)}; [ -n "$d" ] || { [ "$SMTP_PORT" = 465 ] && d=ssl || d=starttls; }
    ask SMTP_TLS "TLS (starttls, ssl, none)" "$d"; case "$SMTP_TLS" in starttls|ssl|none) ;; *) die "bad --smtp-tls" ;; esac
    ask SMTP_USER "SMTP username (blank = no login)" "${O_SMTP_USER:-$(cfg_get BC_SMTP_USER)}"; no_nl "$SMTP_USER" || die "bad username"
    SMTP_PW=${BC_SMTP_PASSWORD:-$(read_file_secret "$O_SMTP_PWFILE")}
    if [ -n "$SMTP_USER" ] && [ -z "$SMTP_PW" ] && [ ! -s "$SECRETS/smtp_password" ]; then
      ask_secret SMTP_PW "SMTP password or app password (input hidden)"
    fi
  fi

  step "6/6 Backups"
  ask KEEP "Nightly backups to keep" "${O_KEEP:-$(cfg_get BC_INSTALL_KEEP "$META")}"; [ -n "$KEEP" ] || KEEP=14
  [[ $KEEP =~ ^[0-9]+$ ]] && [ "$KEEP" -ge 1 ] || die "--keep must be a number of 1 or more"
  PORT=${O_PORT:-$(cfg_get BC_INSTALL_PORT "$META")}; [ -n "$PORT" ] || PORT=$PORT_DEFAULT
  [[ $PORT =~ ^[0-9]+$ ]] || die "--port must be a number"

  if [ "$NONINTERACTIVE" = 0 ]; then
    say ""; say "${c_b}Summary${c_0}: $MODE install, \"$NAME\", admin $ADMIN, $ACCESS access at $PUBLIC_URL, mail: $MAIL, keep $KEEP backups"
    local yn; read -r -p "  Go ahead? [Y/n] " yn </dev/tty || true
    case "${yn:-y}" in y|Y|yes) ;; *) die "cancelled; nothing was changed" ;; esac
  fi
}

# ---------- writing config ----------
q() { printf '%s' "$1" | sed -e 's/\\/\\\\/g' -e 's/"/\\"/g' -e 's/\$/\\$/g'; }   # double-quoted single-line values: escape \ " $

write_config() {
  step "Writing settings to $CONFIG"
  local group=$SVC_USER grp_own
  [ "$MODE" = docker ] && group=$DOCKER_UID
  install -d -m 0755 "$ETC"; install -d -m 0750 -o root -g "$group" "$SECRETS"
  local host_line="127.0.0.1" hosts=""
  [ "$ACCESS" = lan ] && { local ipaddr; ipaddr=$(lan_ip || true); [ -n "$ipaddr" ] && hosts="$ipaddr"; }
  local bg_line="#BC_BACKGROUND=" prev_bg ext
  prev_bg=$(cfg_get BC_BACKGROUND)
  if [ -n "$BG_FILE" ]; then ext=${BG_FILE##*.}; bg_line="BC_BACKGROUND=background.${ext,,}"
  elif [ -n "$prev_bg" ]; then bg_line="BC_BACKGROUND=$prev_bg"; fi
  local pw_file_line="#BC_SMTP_PASSWORD_FILE="
  if [ "$MAIL" = smtp ] && [ -n "$SMTP_USER" ]; then
    if [ "$MODE" = docker ]; then pw_file_line="BC_SMTP_PASSWORD_FILE=/run/secrets/branch-chat/smtp_password"
    else pw_file_line="BC_SMTP_PASSWORD_FILE=$SECRETS/smtp_password"; fi
  fi
  grp_own="root:$group"
  # 0.23.3-beta.15: native installs let the service rewrite this file (Administrator page > Settings > Apply).
  local cfg_mode=640; [ "$MODE" = native ] && cfg_mode=660
  {
    echo "# BranchChat Server settings, written by install.sh on $(date '+%F %T')."
    echo "# Re-run 'sudo $SRC_ROOT/server/install.sh' to change them, or edit and restart the service."
    echo "# Reference: server/config.env.example in the BranchChat source."
    echo "BC_PUBLIC_URL=\"$(q "$PUBLIC_URL")\""
    [ -n "$hosts" ] && echo "BC_HOSTS=\"$(q "$hosts")\""
    [ "$MODE" = native ] && { echo "BC_HOST=$host_line"; echo "BC_PORT=$PORT"; }
    echo "BC_INSTANCE_NAME=\"$(q "$NAME")\""
    [ -n "$ACCENT" ] && echo "BC_ACCENT=$ACCENT" || echo "#BC_ACCENT=#3c92ea"
    echo "$bg_line"
    echo "BC_OWNER=$ADMIN"
    echo "BC_MAIL_FROM=\"$(q "$MAIL_FROM")\""
    echo "BC_MAIL_MODE=$MAIL"
    if [ "$MAIL" = smtp ]; then
      echo "BC_SMTP_HOST=$SMTP_HOST"; echo "BC_SMTP_PORT=$SMTP_PORT"; echo "BC_SMTP_TLS=$SMTP_TLS"
      echo "BC_SMTP_USER=\"$(q "$SMTP_USER")\""; echo "$pw_file_line"
    fi
    echo "BC_INVITE_NOTE="
  } | write_file "$CONFIG" "$cfg_mode" "$grp_own"
  if [ -n "$SMTP_PW" ]; then printf '%s\n' "$SMTP_PW" | write_file "$SECRETS/smtp_password" 640 "$grp_own"; fi
  if [ -n "$TUNNEL_TOKEN" ]; then printf 'TUNNEL_TOKEN=%s\n' "$TUNNEL_TOKEN" | write_file "$SECRETS/tunnel.env" 600 root:root; fi
  {
    echo "# install.sh bookkeeping (no secrets)"
    echo "BC_INSTALL_MODE=$MODE"; echo "BC_INSTALL_ACCESS=$ACCESS"; echo "BC_INSTALL_PORT=$PORT"
    echo "BC_INSTALL_STATE_DIR=$STATE_DIR"; echo "BC_INSTALL_BACKUP_DIR=$BACKUP_DIR"; echo "BC_INSTALL_KEEP=$KEEP"
    echo "BC_INSTALL_SRC=$SRC_ROOT"; echo "BC_INSTALL_ADMIN_EMAIL=$ADMIN_EMAIL"
  } | write_file "$META" 644 root:root
  ok "settings saved (secrets in $SECRETS, readable only by root and the service)"
}

state_owner() { [ "$MODE" = docker ] && echo "$DOCKER_UID:$DOCKER_UID" || echo "$SVC_USER:$SVC_USER"; }

prepare_state() {
  local own grp ext; own=$(state_owner); grp=${own#*:}; own=${own%%:*}
  install -d -m 0700 -o "$own" -g "$grp" "$STATE_DIR"
  chown "$own:$grp" "$STATE_DIR"
  if [ -n "$BG_FILE" ]; then
    ext=${BG_FILE##*.}
    install -d -m 0700 -o "$own" -g "$grp" "$STATE_DIR/branding"
    install -m 0600 -o "$own" -g "$grp" "$BG_FILE" "$STATE_DIR/branding/background.${ext,,}"
  fi
}

# ---------- native ----------
native_packages() {
  apt_install python3 python3-cryptography curl ca-certificates tar gzip
  python3 - <<'P' || die "Python 3.9 or newer is required"
import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)
P
  python3 -c 'import cryptography' 2>/dev/null || die "python3-cryptography is missing"
}

ensure_user() {
  id -u "$SVC_USER" >/dev/null 2>&1 && return
  run useradd --system --home-dir "$STATE_DIR" --no-create-home --shell /usr/sbin/nologin --user-group "$SVC_USER"
  ok "created system user $SVC_USER"
}

deploy_release() {  # copy web/ into a new release dir and point $APP_LINK at it; echo the previous target
  [ -f "$SRC_ROOT/web/server.py" ] || die "cannot find web/server.py next to this script ($SRC_ROOT)"
  install -d -m 0755 "$BASE" "$RELEASES"
  local ver rel prev
  ver=$(head -n1 "$SRC_ROOT/VERSION" 2>/dev/null | tr -cd '0-9A-Za-z.-'); [ -n "$ver" ] || ver=$( (cd "$SRC_ROOT" && git describe --tags --always --dirty 2>/dev/null) || echo local)
  rel="$RELEASES/$(date +%Y%m%d-%H%M%S)-${ver//\//-}"
  mkdir -p "$rel"
  tar -C "$SRC_ROOT/web" --exclude='__pycache__' --exclude='*.pyc' --exclude='*.bak*' -cf - . | tar -C "$rel" -xf -
  echo "$ver" > "$rel/VERSION"
  chown -R root:root "$rel"; chmod -R go-w "$rel"
  python3 -m py_compile "$rel/server.py" "$rel/accounts.py" "$rel/bcconfig.py" "$rel/manage.py" || die "the new code does not compile"
  find "$rel" -name __pycache__ -type d -prune -exec rm -rf {} +
  prev=$(readlink "$APP_LINK" 2>/dev/null || true)
  if [ -e "$APP_LINK" ] && [ ! -L "$APP_LINK" ]; then die "$APP_LINK exists and is not a symlink; move it away first"; fi
  ln -sfn "$rel" "$APP_LINK.new" && mv -Tf "$APP_LINK.new" "$APP_LINK"
  # keep the newest 3 releases (current + 2 to roll back to)
  ls -1dt "$RELEASES"/*/ 2>/dev/null | awk 'NR > 3' | while read -r old; do [ "${old%/}" != "$(readlink -f "$APP_LINK")" ] && rm -rf -- "$old"; done
  PREV_RELEASE=$prev
  ok "code installed in $rel"
}

manage_native() {  # run manage.py as the service user with the service's settings
  runuser -u "$SVC_USER" -- env BC_CONFIG="$CONFIG" BC_STATE_DIR="$STATE_DIR" BC_APP_DIR="$APP_LINK" \
    python3 "$APP_LINK/manage.py" "$@"
}

write_units_native() {
  step "Installing the systemd service"
  sed -e "s#^ExecStart=.*#ExecStart=/usr/bin/python3 $APP_LINK/server.py#" "$SCRIPT_DIR/branch-chat.service" \
    | write_file /etc/systemd/system/branch-chat.service 644 root:root
  if [ "$ACCESS" = tunnel ]; then
    write_file /etc/systemd/system/branch-chat-tunnel.service 644 root:root <<U
# Cloudflare Tunnel for BranchChat. The token is in $SECRETS/tunnel.env (root only).
[Unit]
Description=BranchChat Cloudflare Tunnel
After=network-online.target branch-chat.service
Wants=network-online.target

[Service]
Type=simple
EnvironmentFile=$SECRETS/tunnel.env
ExecStart=/usr/bin/cloudflared --no-autoupdate tunnel run
Restart=on-failure
RestartSec=5
DynamicUser=yes
NoNewPrivileges=yes
ProtectSystem=strict
ProtectHome=yes
PrivateTmp=yes

[Install]
WantedBy=multi-user.target
U
  fi
  write_backup_timer
}

write_backup_timer() {
  install -m 0755 "$SCRIPT_DIR/backup.sh" /usr/local/sbin/branch-chat-backup
  if has_systemd; then
    write_file /etc/systemd/system/branch-chat-backup.service 644 root:root <<U
[Unit]
Description=BranchChat nightly backup
[Service]
Type=oneshot
ExecStart=/usr/local/sbin/branch-chat-backup
Nice=10
IOSchedulingClass=idle
U
    write_file /etc/systemd/system/branch-chat-backup.timer 644 root:root <<U
[Unit]
Description=BranchChat nightly backup (03:30, keeps the newest $KEEP)
[Timer]
OnCalendar=*-*-* 03:30:00
RandomizedDelaySec=20m
Persistent=true
[Install]
WantedBy=timers.target
U
  else
    write_file /etc/cron.d/branch-chat-backup 644 root:root <<U
# BranchChat nightly backup (this system has no systemd)
30 3 * * * root /usr/local/sbin/branch-chat-backup >> /var/log/branch-chat-backup.log 2>&1
U
  fi
}

lan_caddy_native() {
  apt_install caddy
  local cf=/etc/caddy/Caddyfile marker="# managed by branch-chat install.sh"
  if [ -f "$cf" ] && ! grep -q "$marker" "$cf"; then
    if grep -qvE '^\s*(#|$)' "$cf" && ! grep -q 'root \* /usr/share/caddy' "$cf"; then
      die "$cf already has your own configuration. Add this site to it yourself:  $PUB_HOST, $(lan_ip) { tls internal; reverse_proxy 127.0.0.1:$PORT }"
    fi
    cp -p "$cf" "$cf.before-branch-chat"
  fi
  write_file "$cf" 644 root:root <<C
$marker
# LAN-only HTTPS for BranchChat with Caddy's private certificate authority.
# Devices must trust Caddy's root certificate once: see docs/self-hosting.md ("LAN-only").
$PUB_HOST${LANIP:+, $LANIP} {
	tls internal
	reverse_proxy 127.0.0.1:$PORT
}
C
}

health_wait() {  # health_wait URL seconds
  local _
  for _ in $(seq 1 "${2:-30}"); do
    curl -fsS --max-time 3 "$1" 2>/dev/null | grep -qx ok && return 0
    sleep 1
  done
  return 1
}

native_start() {
  if has_systemd; then
    run systemctl daemon-reload
    run systemctl enable branch-chat.service branch-chat-backup.timer >>"$LOG" 2>&1
    run systemctl restart branch-chat.service
    run systemctl start branch-chat-backup.timer
    if [ "$ACCESS" = tunnel ]; then run systemctl enable branch-chat-tunnel.service >>"$LOG" 2>&1; run systemctl restart branch-chat-tunnel.service; fi
    if [ "$ACCESS" = lan ]; then run systemctl enable caddy >>"$LOG" 2>&1; run systemctl reload-or-restart caddy; fi
  else
    warn "no systemd here: start the server yourself with"
    say  "    runuser -u $SVC_USER -- env BC_CONFIG=$CONFIG BC_STATE_DIR=$STATE_DIR BC_APP_DIR=$APP_LINK python3 $APP_LINK/server.py"
  fi
}

native_stop() { has_systemd && systemctl is-active -q branch-chat.service && run systemctl stop branch-chat.service || true; }

do_install_native() {
  native_packages
  [ "$ACCESS" = tunnel ] && install_cloudflared_native
  ensure_user
  write_config
  prepare_state
  deploy_release
  write_units_native
  [ "$ACCESS" = lan ] && { LANIP=$(lan_ip || true); lan_caddy_native; }
  native_stop
  create_admin manage_native
  manage_native migrate >/dev/null
  native_start
  if has_systemd; then
    health_wait "http://127.0.0.1:$PORT/healthz" 30 || die "the server did not answer on /healthz; see: journalctl -u branch-chat -n 50"
    ok "server is healthy on 127.0.0.1:$PORT"
  fi
}

create_admin() {  # create_admin <runner...>   (the server is stopped while this runs)
  step "Admin account $ADMIN"
  local out p1 p2
  if "$@" user-exists --username "$ADMIN" >/dev/null 2>&1; then
    ok "account $ADMIN already exists (password unchanged)"
    "$@" create-admin --username "$ADMIN" --email "$ADMIN_EMAIL" --password-stdin </dev/null >/dev/null  # makes sure it is an admin
    return
  fi
  if [ -z "$ADMIN_PW" ]; then
    [ "$NONINTERACTIVE" = 1 ] && die "an admin password is needed: --admin-password-file FILE or BC_ADMIN_PASSWORD"
    while :; do
      ask_secret p1 "Choose the admin password (8+ characters)"; ask_secret p2 "Repeat it"
      [ "$p1" = "$p2" ] && [ ${#p1} -ge 8 ] && break; say "  They did not match or were too short; try again."
    done
    ADMIN_PW=$p1
  fi
  out=$(printf '%s\n' "$ADMIN_PW" | "$@" create-admin --username "$ADMIN" --email "$ADMIN_EMAIL" --password-stdin) || die "could not create the admin account"
  ok "$out"
}

# ---------- docker ----------
compose() { docker compose -f "$BASE/docker-compose.yml" -p branch-chat "$@"; }

write_compose() {
  step "Writing $BASE/docker-compose.yml"
  install -d -m 0755 "$BASE"
  local publish="127.0.0.1:$PORT:8766"
  {
    cat <<Y
# BranchChat Server (Docker). Written by install.sh; re-run it to change settings.
name: branch-chat
services:
  branch-chat:
    image: branch-chat:current
    restart: unless-stopped
    env_file: [$CONFIG]
    environment:
      BC_HOST: 0.0.0.0
      BC_PORT: "8766"
      BC_STATE_DIR: /data
      BC_APP_DIR: /app
    ports: ["$publish"]
    volumes:
      - $STATE_DIR:/data
      - $SECRETS:/run/secrets/branch-chat:ro
    read_only: true
    tmpfs: [/tmp]
    cap_drop: [ALL]
    security_opt: ["no-new-privileges=true"]
    stop_grace_period: 20s
Y
    if [ "$ACCESS" = tunnel ]; then cat <<Y
  cloudflared:
    image: cloudflare/cloudflared:latest
    command: tunnel --no-autoupdate run
    env_file: [$SECRETS/tunnel.env]
    restart: unless-stopped
    depends_on:
      branch-chat: {condition: service_healthy}
Y
    fi
    if [ "$ACCESS" = lan ]; then cat <<Y
  caddy:
    image: caddy:2
    restart: unless-stopped
    ports: ["80:80", "443:443"]
    volumes:
      - $BASE/Caddyfile:/etc/caddy/Caddyfile:ro
      - $BASE/caddy-data:/data
    depends_on: [branch-chat]
Y
    fi
  } | write_file "$BASE/docker-compose.yml" 644 root:root
  if [ "$ACCESS" = lan ]; then
    LANIP=$(lan_ip || true)
    write_file "$BASE/Caddyfile" 644 root:root <<C
# LAN-only HTTPS for BranchChat (Caddy private CA). See docs/self-hosting.md ("LAN-only").
$PUB_HOST${LANIP:+, $LANIP} {
	tls internal
	reverse_proxy branch-chat:8766
}
C
  fi
}

docker_build() {  # build the image from this source tree; keep the old one as :previous
  step "Building the BranchChat image (this takes a few minutes on a Raspberry Pi)"
  [ -f "$SRC_ROOT/Dockerfile" ] || die "cannot find the Dockerfile in $SRC_ROOT"
  local ver; ver=$(head -n1 "$SRC_ROOT/VERSION" 2>/dev/null | tr -cd '0-9A-Za-z.-'); [ -n "$ver" ] || ver=$( (cd "$SRC_ROOT" && git describe --tags --always --dirty 2>/dev/null) || echo local)
  docker image inspect branch-chat:current >/dev/null 2>&1 && run docker tag branch-chat:current branch-chat:previous
  run docker build --build-arg BC_VERSION="$ver" -t "branch-chat:$ver" -t branch-chat:current "$SRC_ROOT" >>"$LOG" 2>&1 || die "docker build failed (see $LOG)"
  ok "image branch-chat:$ver"
}

manage_docker() {  # one-off container with the same settings and volume (server must be stopped for writes)
  compose run --rm -T --no-deps branch-chat python /app/manage.py "$@"
}

do_install_docker() {
  install_docker
  write_config
  prepare_state
  write_compose
  docker_build
  write_backup_timer
  has_systemd && { run systemctl daemon-reload; run systemctl enable --now branch-chat-backup.timer >>"$LOG" 2>&1; } || true
  compose stop branch-chat >/dev/null 2>&1 || true
  create_admin manage_docker
  run compose up -d --remove-orphans >>"$LOG" 2>&1 || die "docker compose up failed (see $LOG)"
  health_wait "http://127.0.0.1:$PORT/healthz" 60 || die "the container did not answer on /healthz; see: docker compose -f $BASE/docker-compose.yml logs"
  ok "server is healthy on 127.0.0.1:$PORT"
}

# ---------- commands ----------
load_meta() {
  [ -r "$META" ] || die "BranchChat Server is not installed here (no $META). Run: sudo $0"
  MODE=$(cfg_get BC_INSTALL_MODE "$META"); ACCESS=$(cfg_get BC_INSTALL_ACCESS "$META"); PORT=$(cfg_get BC_INSTALL_PORT "$META")
  KEEP=$(cfg_get BC_INSTALL_KEEP "$META")
}

cmd_install() {
  need_root; load_os; touch "$LOG"; chmod 600 "$LOG"
  gather
  if [ -d "$STATE_DIR" ] && [ -n "$(ls -A "$STATE_DIR" 2>/dev/null)" ] && [ -x /usr/local/sbin/branch-chat-backup ]; then
    step "Backing up the current data before changing anything"; /usr/local/sbin/branch-chat-backup || warn "backup failed"
  fi
  if [ "$MODE" = native ]; then do_install_native; else do_install_docker; fi
  finish_message
}

finish_message() {
  say ""; say "${c_g}${c_b}BranchChat Server is set up.${c_0}"
  case "$ACCESS" in
    tunnel) say "  Last step in the Cloudflare dashboard (Networks -> Tunnels -> your tunnel -> Public hostnames):"
            say "    hostname: $PUB_HOST   service: http://$([ "$MODE" = docker ] && echo branch-chat:8766 || echo "localhost:$PORT")"
            say "  Then open $PUBLIC_URL and sign in as $ADMIN." ;;
    lan)    say "  Open $PUBLIC_URL on your home network. Each device must trust the private certificate once"
            say "  (see docs/self-hosting.md, \"LAN-only\")." ;;
    local)  say "  The server listens on 127.0.0.1:$PORT. Point your HTTPS reverse proxy at it ($PUBLIC_URL)." ;;
  esac
  say "  Invite people from the menu once you are signed in. Backups: $BACKUP_DIR (nightly, newest $KEEP kept)."
  [ "$MAIL" = log ] && say "  Mail mode is 'log': sign-in codes appear in: sudo $0 logs"
  say "  Update later with: sudo $0 update"
}

cmd_backup() { need_root; load_meta; /usr/local/sbin/branch-chat-backup; }

cmd_update() {
  need_root; load_os; load_meta; touch "$LOG"
  if [ -d "$SRC_ROOT/.git" ]; then
    step "Fetching the latest code (git pull --ff-only)"
    git -C "$SRC_ROOT" pull --ff-only || die "git pull failed; nothing was changed"
  else
    say "  Using the code in $SRC_ROOT (download and unpack a newer release there first)."
  fi
  step "Backing up before the update"; /usr/local/sbin/branch-chat-backup || die "backup failed; update stopped (nothing changed)"
  if [ "$MODE" = native ]; then
    native_packages
    deploy_release
    install -m 0755 "$SCRIPT_DIR/backup.sh" /usr/local/sbin/branch-chat-backup
    native_stop
    if ! { manage_native migrate >/dev/null && { ! has_systemd || { systemctl restart branch-chat.service && health_wait "http://127.0.0.1:$PORT/healthz" 30; }; }; }; then
      warn "the new version did not start cleanly; rolling back to ${PREV_RELEASE:-?}"
      [ -n "$PREV_RELEASE" ] || die "no previous release to roll back to; restore with: $0 restore <backup>"
      local failed; failed=$(readlink -f "$APP_LINK")
      ln -sfn "$PREV_RELEASE" "$APP_LINK.new" && mv -Tf "$APP_LINK.new" "$APP_LINK"
      [ "$failed" != "$(readlink -f "$PREV_RELEASE")" ] && rm -rf -- "$failed"
      has_systemd && systemctl restart branch-chat.service
      health_wait "http://127.0.0.1:$PORT/healthz" 30 && warn "rolled back; the previous version is running again" \
        || warn "rolled back, but /healthz is not answering; check: journalctl -u branch-chat -n 80"
      die "update failed and was rolled back. Logs: journalctl -u branch-chat -n 80"
    fi
  else
    docker_build
    if ! { compose up -d >>"$LOG" 2>&1 && health_wait "http://127.0.0.1:$PORT/healthz" 60; }; then
      warn "the new image is not healthy; rolling back to branch-chat:previous"
      run docker tag branch-chat:previous branch-chat:current && run compose up -d >>"$LOG" 2>&1
      die "update rolled back. Logs: docker compose -f $BASE/docker-compose.yml logs"
    fi
  fi
  ok "updated and healthy"
}

cmd_restore() {
  need_root; load_meta
  [ -n "$RESTORE_FILE" ] && [ -r "$RESTORE_FILE" ] || die "usage: $0 restore /var/backups/branch-chat/branch-chat-YYYYmmdd-HHMMSS.tar.gz"
  tar -tzf "$RESTORE_FILE" | grep -q "^${STATE_DIR#/}/" || die "that file is not a BranchChat backup of $STATE_DIR"
  if [ "$NONINTERACTIVE" = 0 ]; then
    local yn; read -r -p "  Replace the current data with $RESTORE_FILE? The current data is kept aside. [y/N] " yn </dev/tty || true
    [ "$yn" = y ] || [ "$yn" = Y ] || die "cancelled"
  fi
  local aside; aside="$STATE_DIR.before-restore-$(date +%Y%m%d-%H%M%S)"
  if [ "$MODE" = native ]; then native_stop; else compose stop branch-chat >/dev/null 2>&1 || true; fi
  mv "$STATE_DIR" "$aside"; ok "current data kept at $aside"
  tar -C / -xzf "$RESTORE_FILE" "${STATE_DIR#/}"
  chown -R "$(state_owner)" "$STATE_DIR"; chmod 700 "$STATE_DIR"
  if [ "$MODE" = native ]; then has_systemd && run systemctl start branch-chat.service; else run compose up -d >>"$LOG" 2>&1; fi
  health_wait "http://127.0.0.1:$PORT/healthz" 60 && ok "restored and healthy" || warn "restored, but /healthz is not answering yet"
  say "  Settings in the backup ($ETC) were not changed; extract them by hand if you need them."
}

cmd_status() {
  load_meta
  say "mode: $MODE   access: $ACCESS   port: $PORT"
  say "config: $CONFIG   data: $STATE_DIR   backups: $BACKUP_DIR"
  if [ "$MODE" = native ]; then
    say "code: $(readlink -f "$APP_LINK" 2>/dev/null) ($(cat "$APP_LINK/VERSION" 2>/dev/null))"
    has_systemd && systemctl --no-pager --lines=0 status branch-chat.service 2>/dev/null | sed -n '1,3p' || true
  else
    compose ps 2>/dev/null || true
  fi
  printf 'health: '; curl -fsS --max-time 3 "http://127.0.0.1:$PORT/healthz" 2>/dev/null || printf 'NOT ANSWERING'; echo
  say "latest backup: $(ls -1t "$BACKUP_DIR"/branch-chat-*.tar.gz 2>/dev/null | head -n1 || echo none)"
}

cmd_logs() {
  load_meta
  if [ "$MODE" = native ]; then journalctl -u branch-chat --no-pager -n 200; else compose logs --tail 200 branch-chat; fi
}

cmd_uninstall() {
  need_root; load_meta
  if [ "$NONINTERACTIVE" = 0 ]; then
    local yn; read -r -p "  Remove BranchChat Server$([ "$PURGE" = 1 ] && echo ' AND ALL DATA AND BACKUPS')? [y/N] " yn </dev/tty || true
    [ "$yn" = y ] || [ "$yn" = Y ] || die "cancelled"
  fi
  [ "$PURGE" = 0 ] && [ -d "$STATE_DIR" ] && { /usr/local/sbin/branch-chat-backup || warn "final backup failed"; }
  if [ "$MODE" = native ]; then
    if has_systemd; then
      systemctl disable --now branch-chat.service branch-chat-tunnel.service branch-chat-backup.timer >/dev/null 2>&1 || true
    fi
  else
    compose down >/dev/null 2>&1 || true
    has_systemd && systemctl disable --now branch-chat-backup.timer >/dev/null 2>&1 || true
  fi
  rm -f /etc/systemd/system/branch-chat.service /etc/systemd/system/branch-chat-tunnel.service \
        /etc/systemd/system/branch-chat-backup.service /etc/systemd/system/branch-chat-backup.timer /etc/cron.d/branch-chat-backup
  has_systemd && systemctl daemon-reload || true
  rm -rf "$BASE"
  rm -f "$META"
  if [ "$PURGE" = 1 ]; then
    rm -rf "$STATE_DIR" "$STATE_DIR".before-restore-* "$BACKUP_DIR" "$ETC" /usr/local/sbin/branch-chat-backup
    id -u "$SVC_USER" >/dev/null 2>&1 && userdel "$SVC_USER" || true
    if [ "$MODE" = docker ] && command -v docker >/dev/null 2>&1; then
      docker image ls --format '{{.Repository}}:{{.Tag}}' | grep '^branch-chat:' | xargs -r docker image rm >/dev/null 2>&1 || true
    fi
    ok "removed everything, including data and backups"
  else
    ok "removed the services and code. Kept: data $STATE_DIR, backups $BACKUP_DIR, settings $CONFIG"
    say "  (sudo $0 uninstall --purge deletes those too)"
  fi
}

main() {
  parse_args "$@"
  case "$CMD" in
    install) cmd_install ;; update) cmd_update ;; backup) cmd_backup ;; restore) cmd_restore ;;
    status) cmd_status ;; logs) cmd_logs ;; uninstall) cmd_uninstall ;;
  esac
}
main "$@"
