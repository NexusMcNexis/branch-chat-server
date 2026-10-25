# BranchChat Server

Host your own BranchChat: a self-hosted, end-to-end encrypted chat server for a family or group.
This repo holds the **server downloads** (on the [Releases](https://github.com/NexusMcNexis/branch-chat-server/releases) page)
and the setup guides. The phone and desktop apps are in [branch-chat-releases](https://github.com/NexusMcNexis/branch-chat-releases/releases/latest).

## Which file do I need?

| Your setup | File (from the latest release) |
|---|---|
| Raspberry Pi OS, Debian or Ubuntu, plain install (amd64, arm64, armhf: one file for all) | `branch-chat-server-<version>-install.tar.gz` |
| Linux with Docker (amd64 or arm64; the image is built on your machine) | `branch-chat-server-<version>-install.tar.gz` with `--mode docker`, or `branch-chat-server-<version>-docker-compose.tar.gz` |
| Proxmox | a Debian 12 VM or LXC, then the Linux steps ([guide](docs/server/proxmox.md)) |
| Windows or macOS (Docker Desktop, not yet tested) | `branch-chat-server-<version>-docker-compose.tar.gz` ([guide](docs/server/docker-desktop.md)) |

Each release has a `SHA256SUMS` file: `sha256sum -c SHA256SUMS` (macOS: `shasum -a 256 -c SHA256SUMS`).

## Quick install (Linux)

```sh
tar -xzf branch-chat-server-<version>-install.tar.gz
cd branch-chat-server-<version>
sudo ./server/install.sh
```

The installer asks 6 short questions (install method, group name and look, admin account, how people reach it,
email for sign-in codes, backups), shows a summary and asks before changing anything. After that, manage
settings from `/admin` in your browser.

## Guides

- [Self-hosting guide](docs/self-hosting.md)
- [Raspberry Pi](docs/server/raspberry-pi.md)
- [Proxmox](docs/server/proxmox.md)
- [Windows / macOS with Docker Desktop](docs/server/docker-desktop.md)

## License

Free for noncommercial use under [PolyForm Noncommercial 1.0.0](LICENSE.md); small businesses under
[PolyForm Small Business](LICENSE-SMALL-BUSINESS.md). See [LICENSING.md](LICENSING.md).
