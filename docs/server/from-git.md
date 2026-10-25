# Installing from git

Good if you like `git pull` updates. You need `git` (`sudo apt install git`, `sudo dnf install git`,
`sudo pacman -S git`) and `python3`.

```sh
git clone https://github.com/NexusMcNexis/branch-chat-server.git
cd branch-chat-server
sudo ./server/install.sh
```

This repository holds the full server source (`web/`, `server/`), so the clone is all you need. The setup
asks the same questions as the other install methods; add `--mode docker` to build and run it with Docker
instead (the `Dockerfile` is in the repository root).

To install a specific release instead of the newest code:

```sh
git checkout v26.10.13.13      # any tag from the Releases page
sudo ./server/install.sh
```

## Updating a git install

```sh
cd branch-chat-server
sudo ./server/install.sh update
```

`update` runs `git pull --ff-only` for you, backs up first, installs the new code, checks it is healthy and
rolls back if not. If you checked out a tag, run `git checkout main` first (or check out the newer tag and
run `update`).
