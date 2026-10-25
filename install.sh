#!/usr/bin/env bash
# BranchChat Server one-line installer:
#   curl -fsSL https://raw.githubusercontent.com/NexusMcNexis/branch-chat-server/main/install.sh | sudo bash
# Extra options go after "-s --", e.g.  ... | sudo bash -s -- -y --name Woods     or   ... | sudo bash -s -- update
# Downloads the latest server, checks its SHA256 checksum, and runs server/install.sh from it.
set -euo pipefail
BASE_URL="${BC_RELEASE_URL:-https://github.com/NexusMcNexis/branch-chat-server/releases/latest/download}"
FILE=branch-chat-server-latest-install.tar.gz

die() { echo "error: $*" >&2; exit 1; }
[ "$(id -u)" -eq 0 ] || die "please run with sudo:  curl -fsSL <this url> | sudo bash"
[ -r /etc/os-release ] || die "cannot tell which Linux this is (no /etc/os-release). Windows/macOS: use Docker Desktop, see the README."
# shellcheck disable=SC1091
. /etc/os-release
case " ${ID:-} ${ID_LIKE:-} " in
  *" debian "*|*" ubuntu "*|*" raspbian "*) need() { apt-get update -qq && DEBIAN_FRONTEND=noninteractive apt-get install -y -qq "$@"; }; PKGS="curl tar python3 ca-certificates" ;;
  *" fedora "*|*" rhel "*|*" centos "*|*" rocky "*|*" almalinux "*) need() { dnf install -y -q "$@"; }; PKGS="curl tar python3 ca-certificates" ;;
  *" arch "*) need() { pacman -Sy --needed --noconfirm "$@"; }; PKGS="curl tar python ca-certificates" ;;
  *" suse "*|*" opensuse "*) need() { zypper --non-interactive install "$@"; }; PKGS="curl tar python3 ca-certificates" ;;
  *) die "${PRETTY_NAME:-this system} is not supported. Supported: Debian, Ubuntu, Raspberry Pi OS, Fedora, RHEL/Rocky/Alma, Arch, openSUSE. Otherwise use Docker (see the README)." ;;
esac
if ! { command -v curl && command -v tar && command -v python3; } >/dev/null; then
  echo "==> Installing curl, tar and python3"
  # shellcheck disable=SC2086
  need $PKGS >/dev/null || die "could not install $PKGS"
fi

TMP=$(mktemp -d); trap 'rm -rf "$TMP"' EXIT
echo "==> Downloading the latest BranchChat Server"
curl -fsSL "$BASE_URL/$FILE" -o "$TMP/$FILE" || die "download failed: $BASE_URL/$FILE"
curl -fsSL "$BASE_URL/SHA256SUMS" -o "$TMP/SHA256SUMS" || die "download failed: $BASE_URL/SHA256SUMS"
echo "==> Checking the download"
(cd "$TMP" && grep " $FILE\$" SHA256SUMS | sha256sum -c --quiet -) || die "checksum mismatch: the download is damaged or was changed. Nothing was installed."
tar -xzf "$TMP/$FILE" -C "$TMP"
DIR=$(find "$TMP" -mindepth 2 -maxdepth 3 -path '*/server/install.sh' -print -quit)
[ -n "$DIR" ] || die "server/install.sh not found in the download"
echo "==> Starting the setup"
# The questions read from the keyboard even though this script came through a pipe.
if [ ! -t 0 ] && { : </dev/tty; } 2>/dev/null; then bash "$DIR" "$@" </dev/tty; else bash "$DIR" "$@"; fi
