# BranchChat Server on Windows or macOS (Docker Desktop)

Status: not yet tested by us on Windows or macOS. The same image is used on Linux (amd64 and arm64).

1. Install Docker Desktop (Windows: turn on the WSL 2 backend; Mac: Apple Silicon or Intel).
2. Download `branch-chat-server-<version>-docker-compose.tar.gz` from the GitHub release and unpack it
   (Windows 10/11: `tar -xzf branch-chat-server-<version>-docker-compose.tar.gz` in PowerShell).
3. Go into the `server` folder and copy `config.env.example` to `config.env`. Edit it:
   - `BC_PUBLIC_URL` = the address people will use (e.g. `http://localhost:8766` to try it)
   - `BC_INSTANCE_NAME`, `BC_OWNER`
   - for a first try set `BC_MAIL_MODE=log` (sign-in codes appear in `docker compose logs`)
4. Start it: `docker compose up -d --build` (first build takes a few minutes).
5. Open http://localhost:8766. To let others in from outside your home, add a Cloudflare Tunnel:
   put `TUNNEL_TOKEN=...` in a `.env` file next to `docker-compose.yml` and run
   `docker compose --profile tunnel up -d`.

Data lives in `server/data`; back that folder up. Update: unpack the new bundle over the old one
(keep `config.env` and `data`) and run `docker compose up -d --build` again.
