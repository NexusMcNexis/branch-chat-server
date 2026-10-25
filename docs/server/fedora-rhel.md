# Fedora, RHEL, Rocky Linux and AlmaLinux

Not yet tested on real machines; the package was tested in a Fedora 40 container.

## Easiest: one command

```sh
curl -fsSL https://raw.githubusercontent.com/NexusMcNexis/branch-chat-server/main/install.sh | sudo bash
```

## Or: the .rpm package

```sh
sudo dnf install https://github.com/NexusMcNexis/branch-chat-server/releases/latest/download/branch-chat-server-latest.noarch.rpm
sudo branch-chat-setup
```

**RHEL / Rocky / Alma:** turn on EPEL first (it has the Python packages and Caddy):

```sh
sudo dnf install epel-release
```

## Notes

- Cloudflare Tunnel: the installer adds Cloudflare's own dnf repository for `cloudflared`.
- LAN-only HTTPS needs Caddy (`sudo dnf install caddy`; on RHEL-likes it comes from EPEL or the `@caddy/caddy` COPR).
- The firewall is on by default. Only needed if you use LAN mode: `sudo firewall-cmd --add-service=https --permanent && sudo firewall-cmd --reload`
- openSUSE works the same way: `sudo zypper install ./branch-chat-server-latest.noarch.rpm`.
