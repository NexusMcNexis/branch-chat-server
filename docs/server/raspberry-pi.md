# Branch Chat Server on a Raspberry Pi (arm64)

Tested on a Raspberry Pi 5 running Raspberry Pi OS 64-bit (Bookworm). A Pi 4 with 2 GB or more works too.

1. **Use the 64-bit OS.** `uname -m` must print `aarch64`. The 32-bit OS (`armv7l`) is not supported.
2. **Storage:** a USB SSD is best (photos and files are stored here, encrypted). With an SD card, use an A2 card.
3. **Get the server:** download `branch-chat-server-<version>-install.tar.gz` from the GitHub release, then
   ```bash
   tar xzf branch-chat-server-*-install.tar.gz && cd branch-chat-server-*/
   sudo ./server/install.sh          # choose "native" (lightest on a Pi) or "docker"
   ```
4. **HTTPS:** pick `tunnel` (Cloudflare Tunnel, no router changes) or `lan` in the wizard. See `docs/self-hosting.md`.
5. **Updates:** unpack the newer release next to the old one and run `sudo ./server/install.sh update` from it.
   It backs up first and rolls back if the new version doesn't start.

Docker on a Pi: the same `Dockerfile` builds natively for `linux/arm64` (`docker compose build`). The first
build takes a few minutes on a Pi.

Tips: give the Pi a fixed address (DHCP reservation), keep it wired, and check `sudo ./server/install.sh status`
after power cuts. Backups run nightly (`/var/backups/branch-chat` by default). Copy them off the Pi now and then.
