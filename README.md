# BranchChat Server

Host your own BranchChat: a self-hosted, end-to-end encrypted chat server for a family or group.
This repo holds the **server downloads** (on the [Releases](https://github.com/NexusMcNexis/branch-chat-server/releases) page)
and the setup guides. The phone and desktop apps are in [branch-chat-releases](https://github.com/NexusMcNexis/branch-chat-releases/releases/latest).

## Install (recommended): one command

On a Linux computer, Raspberry Pi or server, paste this into the terminal:

```sh
curl -fsSL https://raw.githubusercontent.com/NexusMcNexis/branch-chat-server/main/install.sh | sudo bash
```

It works out which Linux you have, installs what it needs, downloads the latest server, checks the download
is genuine (SHA256), and starts the setup. The setup asks 6 short questions (install method, group name and look,
admin account, how people reach it, email for sign-in codes, backups), shows a summary and asks before changing
anything. After that, manage settings from `/admin` in your browser.

Update later: run the same line with `-s -- update` on the end (`... | sudo bash -s -- update`).

## Supported platforms

| Your setup | Easiest way | Other ways | Guide |
|---|---|---|---|
| Debian, Ubuntu (VPS, PC or bare metal) | one command | `.deb`, tarball, git | [debian-ubuntu.md](docs/server/debian-ubuntu.md) |
| Raspberry Pi OS (64-bit recommended) | one command | `.deb`, tarball | [raspberry-pi.md](docs/server/raspberry-pi.md) |
| Fedora, RHEL, Rocky, AlmaLinux | one command | `.rpm`, tarball | [fedora-rhel.md](docs/server/fedora-rhel.md) |
| openSUSE | one command | `.rpm` | [fedora-rhel.md](docs/server/fedora-rhel.md) |
| Arch Linux, Manjaro | one command | tarball | [arch.md](docs/server/arch.md) |
| Proxmox | Debian 12 VM or LXC, then one command | | [proxmox.md](docs/server/proxmox.md) |
| Any Linux with Docker | install tarball with `--mode docker` | docker-compose bundle | [self-hosting.md](docs/self-hosting.md#docker) |
| Windows or macOS (not yet tested) | Docker Desktop | | [docker-desktop.md](docs/server/docker-desktop.md) |

Not tested on real hardware yet: openSUSE, Arch, Windows, macOS. Tested in containers: Debian 12, Ubuntu 24.04, Fedora 40.

## Release files

| File | For |
|---|---|
| `branch-chat-server-<version>-install.tar.gz` | every Linux above (also `branch-chat-server-latest-install.tar.gz`) |
| `branch-chat-server_<version>-1_all.deb` | Debian / Ubuntu / Pi OS package, any CPU ([how](docs/server/deb-rpm.md)) |
| `branch-chat-server-<version>-1.noarch.rpm` | Fedora / RHEL / openSUSE package, any CPU ([how](docs/server/deb-rpm.md)) |
| `branch-chat-server-<version>-docker-compose.tar.gz` | Docker Compose / Docker Desktop |
| `SHA256SUMS` | check files: `sha256sum -c SHA256SUMS --ignore-missing` |

Files named `...-latest...` are copies of the newest release's files, so links like
`releases/latest/download/branch-chat-server-latest_all.deb` always work.

## Install by hand (tarball)

```sh
tar -xzf branch-chat-server-<version>-install.tar.gz
cd branch-chat-server-<version>
sudo ./server/install.sh
```

## Guides

- [Self-hosting guide](docs/self-hosting.md)
- [Raspberry Pi](docs/server/raspberry-pi.md)
- [Proxmox](docs/server/proxmox.md)
- [Windows / macOS with Docker Desktop](docs/server/docker-desktop.md)
- [Debian / Ubuntu (VPS and bare metal)](docs/server/debian-ubuntu.md)
- [Fedora / RHEL / Rocky / openSUSE](docs/server/fedora-rhel.md)
- [Arch Linux](docs/server/arch.md)
- [.deb and .rpm packages](docs/server/deb-rpm.md)
- [From git](docs/server/from-git.md)

## License

Free for noncommercial use under [PolyForm Noncommercial 1.0.0](LICENSE.md); small businesses under
[PolyForm Small Business](LICENSE-SMALL-BUSINESS.md). See [LICENSING.md](LICENSING.md).
