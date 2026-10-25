# Installing from git

Good if you like `git pull` updates. You need `git` (`sudo apt install git`, `sudo dnf install git`, `sudo pacman -S git`).

```sh
git clone https://github.com/NexusMcNexis/branch-chat.git
cd branch-chat
sudo ./server/install.sh
```

> The source repository is private for now, so this needs access to it. Everyone else: use the
> one-command install or a release download ([README](../../README.md)).

## Updating a git install

```sh
cd branch-chat
sudo ./server/install.sh update
```

`update` runs `git pull` for you, backs up first, installs the new code, checks it is healthy and rolls
back if not. To stay on a specific release instead: `git checkout v26.10.13.13` then `sudo ./server/install.sh update`.
