# Arch Linux (and Manjaro, EndeavourOS)

Not yet tested on a real machine. There is no AUR package yet; use the one command or the tarball.

```sh
curl -fsSL https://raw.githubusercontent.com/NexusMcNexis/branch-chat-server/main/install.sh | sudo bash
```

The installer uses `pacman` to add what it needs: `python`, `python-cryptography`, `curl`, and
`cloudflared` or `caddy` if you choose those options.

Update later by running the same command again with `update` on the end:

```sh
curl -fsSL https://raw.githubusercontent.com/NexusMcNexis/branch-chat-server/main/install.sh | sudo bash -s -- update
```
