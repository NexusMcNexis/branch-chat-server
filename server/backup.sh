#!/bin/sh
# BranchChat Server nightly backup. Installed as /usr/local/sbin/branch-chat-backup by install.sh.
# Makes BACKUP_DIR/branch-chat-YYYYmmdd-HHMMSS.tar.gz (mode 600) of the state dir and /etc/branch-chat,
# then keeps only the newest $BC_INSTALL_KEEP backups. Skips (and fails) when disk space is low.
set -eu
umask 077
META=/etc/branch-chat/install.env
meta() { if [ -r "$META" ]; then sed -n "s/^$1=//p" "$META" | tail -n1; fi; }   # read, never execute
STATE_DIR=$(meta BC_INSTALL_STATE_DIR); STATE_DIR=${STATE_DIR:-/var/lib/branch-chat}
BACKUP_DIR=$(meta BC_INSTALL_BACKUP_DIR); BACKUP_DIR=${BACKUP_DIR:-/var/backups/branch-chat}
KEEP=$(meta BC_INSTALL_KEEP); KEEP=${KEEP:-14}
MIN_FREE_MB=$(meta BC_INSTALL_MIN_FREE_MB); MIN_FREE_MB=${MIN_FREE_MB:-1024}
case "${1:-}" in --keep) KEEP=$2 ;; esac

[ -d "$STATE_DIR" ] || { echo "branch-chat-backup: no state dir $STATE_DIR" >&2; exit 1; }
mkdir -p "$BACKUP_DIR"; chmod 700 "$BACKUP_DIR"
free_mb=$(df -Pm "$BACKUP_DIR" | awk 'NR==2 {print $4}')
used_mb=$(du -sm --exclude=upload-chunks "$STATE_DIR" 2>/dev/null | awk '{print $1}')
if [ "${free_mb:-0}" -lt $(( MIN_FREE_MB + ${used_mb:-0} )) ]; then
  echo "branch-chat-backup: only ${free_mb} MB free in $BACKUP_DIR (need $MIN_FREE_MB MB + ${used_mb} MB); skipped" >&2
  exit 1
fi
stamp=$(date +%Y%m%d-%H%M%S)
out="$BACKUP_DIR/branch-chat-$stamp.tar.gz"
tmp="$out.part"
# The server writes every JSON file atomically (write + rename), so a live copy is consistent per file.
tar -C / --exclude="${STATE_DIR#/}/upload-chunks" --exclude="${STATE_DIR#/}/*.tmp" \
    -czf "$tmp" "${STATE_DIR#/}" etc/branch-chat
chmod 600 "$tmp"
mv "$tmp" "$out"
echo "branch-chat-backup: wrote $out ($(du -h "$out" | awk '{print $1}'))"
# Retention: newest $KEEP stay.
ls -1t "$BACKUP_DIR"/branch-chat-*.tar.gz 2>/dev/null | awk -v k="$KEEP" 'NR > k' | while read -r old; do
  rm -f -- "$old" && echo "branch-chat-backup: removed old $old"
done
