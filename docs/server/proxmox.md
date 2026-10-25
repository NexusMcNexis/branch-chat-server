# Branch Chat Server on Proxmox VE

Run it in its own small VM (recommended) or an unprivileged LXC container. Never on the Proxmox host itself.

## VM (recommended)
1. Create a VM: Debian 12 or 13 netinst ISO, 1–2 vCPU, 1–2 GB RAM, 16 GB disk (more if people share lots
   of photos and videos), VirtIO network on your LAN bridge, **QEMU guest agent** on.
2. Install Debian with SSH server, no desktop. Give the VM a DHCP reservation on your router.
3. Copy the release onto the VM and install:
   ```bash
   tar xzf branch-chat-server-*-install.tar.gz && cd branch-chat-server-*/
   sudo ./server/install.sh          # native, or docker for the Compose setup
   ```
4. HTTPS: `tunnel` mode (Cloudflare Tunnel) needs no port forwarding. Keep the Proxmox web UI (port 8006)
   off the internet. Only the tunnel should be reachable from outside.
5. Firewall (optional): enable the Proxmox firewall for the VM and allow only SSH from your LAN. The tunnel
   connects outward, so it needs no inbound rule.
6. Backups: Datacenter → Backup → add a nightly job for the VM (snapshot mode), in addition to the app's own
   nightly backups (`sudo ./server/install.sh backup`).

## LXC
An unprivileged Debian 12 container with `nesting=1` works for the native install. For Docker inside LXC also set
`keyctl=1`. A VM is simpler and isolates better.

## Updating
Take a Proxmox snapshot, unpack the newer release, run `sudo ./server/install.sh update`. If anything looks wrong,
roll back the snapshot.
