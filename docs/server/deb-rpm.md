# Installing with a .deb or .rpm package

One package works on every CPU (Raspberry Pi, Intel/AMD PCs) because the server is pure Python.

| System | File | Install |
|---|---|---|
| Debian, Ubuntu, Raspberry Pi OS | `branch-chat-server_<version>-1_all.deb` | `sudo apt install ./branch-chat-server_*_all.deb` |
| Fedora, RHEL, Rocky, Alma | `branch-chat-server-<version>-1.noarch.rpm` | `sudo dnf install ./branch-chat-server-*.noarch.rpm` |
| openSUSE | same .rpm | `sudo zypper install ./branch-chat-server-*.noarch.rpm` |

Then run the setup questions once:

```sh
sudo branch-chat-setup
```

## What the package puts where

- Program: `/opt/branch-chat/src` (the setup wizard copies the running version to `/opt/branch-chat/app`)
- Settings: `/etc/branch-chat/config.env` (kept when you remove the package)
- Chats and data: `/var/lib/branch-chat`; nightly backups: `/var/backups/branch-chat`
- Service: `branch-chat` (`sudo systemctl status branch-chat`)

## Updating

Install the newer package the same way. If the server is already set up, the package backs it up,
updates it, checks it is healthy, and rolls back on its own if not.

## Removing

```sh
sudo apt remove branch-chat-server     # or: sudo dnf remove branch-chat-server
sudo apt purge branch-chat-server      # Debian/Ubuntu: also deletes the settings
```

Your chats and backups are **never** deleted by the package. Delete them yourself only if you are sure:
`sudo rm -rf /var/lib/branch-chat /var/backups/branch-chat`

Don't use `branch-chat-setup uninstall` on a package install; use apt/dnf instead.
