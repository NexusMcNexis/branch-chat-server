# Debian and Ubuntu (VPS, home PC or bare metal)

Works on Debian 12+, Ubuntu 22.04/24.04 and Raspberry Pi OS, on a rented VPS (DigitalOcean, Hetzner, Linode...)
or your own PC. 1 GB RAM and 2 GB disk are plenty. Copy and paste each box into the terminal.

## Easiest: one command

```sh
curl -fsSL https://raw.githubusercontent.com/NexusMcNexis/branch-chat-server/main/install.sh | sudo bash
```

It downloads the latest server, checks it, and starts the setup questions.

## Or: the .deb package (updates with `apt`)

```sh
curl -fLO https://github.com/NexusMcNexis/branch-chat-server/releases/latest/download/branch-chat-server-latest_all.deb
sudo apt install ./branch-chat-server-latest_all.deb
sudo branch-chat-setup
```

See [deb-rpm.md](deb-rpm.md) for updates and removal.

## Or: the tarball

```sh
curl -fLO https://github.com/NexusMcNexis/branch-chat-server/releases/latest/download/branch-chat-server-latest-install.tar.gz
tar -xzf branch-chat-server-latest-install.tar.gz
cd branch-chat-server-*/
sudo ./server/install.sh
```

## VPS tips

- Pick **Cloudflare Tunnel** when asked how people reach it: no ports to open, works from anywhere.
- If you pick "local" with your own proxy instead, open ports 80 and 443 only (`sudo ufw allow 80,443/tcp`).
- Choose **SMTP** for email on a VPS so sign-in codes reach people.

## Afterwards

```sh
sudo branch-chat-setup status    # or: sudo ./server/install.sh status
```
