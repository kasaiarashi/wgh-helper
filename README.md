# wireguard-helper

Interactive WireGuard server bootstrapper and peer manager. One command sets up the server; another walks you through adding a user and spits out a `.conf` file plus a QR code.

---

## How it works, end to end

There are three moving pieces:

1. **Your Ubuntu server** (internal IP `10.0.0.14`) — runs WireGuard and hosts the services your team wants to reach (SMB, Forge VCS, etc.).
2. **This tool (`wgh`)** — installed on the server. It writes the WireGuard config, tracks peers in SQLite, and generates per-user client configs.
3. **Your teammates' devices** — Windows laptops, iPhones, Android phones. They install the official WireGuard app and import the config you send them (via file or QR).

Once a teammate connects, their device gets a tunnel IP in `10.8.0.0/24` and can reach `10.0.0.0/24` as if they were on the office LAN. Everything else (YouTube, Gmail, etc.) still goes out over their normal internet — this is a **split tunnel**.

```
  Teammate's laptop                Internet                 Your server
  ┌─────────────────┐       ┌─────────────────┐       ┌──────────────────┐
  │ WireGuard app   │──UDP──│vpn.example.com│──UDP──│ wg0 :51820       │
  │ 10.8.0.2        │ :51820│                 │       │ 10.8.0.1         │
  └─────────────────┘       └─────────────────┘       │ 10.0.0.14 (LAN)  │
         │                                            └──────────────────┘
         │ routes 10.0.0.0/24                                  │
         └────────────── through tunnel ─────────────> SMB / Forge / etc.
```

---

## Part 1 — One-time prerequisites (you, before anything else)

Before you touch the server, make sure:

1. **DNS**: `vpn.example.com` resolves to your server's **public** IP.
   Clients use this hostname to find the server over the internet. Check with `nslookup vpn.example.com`.

2. **Firewall / port forward**: UDP port `51820` must reach the server.
   - If your server is behind a home/office router, add a port forward rule: `UDP 51820 → 10.0.0.14:51820`.
   - If it's on a cloud VPS (EC2, Hetzner, etc.), open UDP 51820 in the security group.
   - `wgh bootstrap` automatically opens UFW on the server itself if UFW is active.

3. **Server access**: you can SSH into the Ubuntu server as a sudo user.

---

## Part 2 — Install and configure the server

### 2a. Get the code onto the server

Recommended — clone from GitHub directly on the server:

```bash
ssh user@your-server
git clone https://github.com/kasaiarashi/wgh-helper.git ~/wgh-helper
cd ~/wgh-helper
sudo ./install.sh
```

(Alternative: `./deploy.sh user@host --install` from a Windows/Linux dev box with `rsync` available, for pushing local uncommitted edits without going through GitHub.)

`install.sh` does:

- Installs `python3-venv`
- Creates a venv at `/opt/wireguard-helper/venv/`
- Installs the `wgh` CLI into it
- Symlinks `wgh` to `/usr/local/bin/` so you can type `sudo wgh` anywhere.

### Updating `wgh` later

Pull new commits and re-run the installer — it upgrades the package in the venv in place, your DB and settings are untouched:

```bash
cd ~/wgh-helper
git pull
sudo ./install.sh
```

Existing peers stay connected through upgrades (nothing touches `wg0` unless you run `wgh bootstrap`, `add`, or `remove`).

### 2b. Bootstrap WireGuard

SSH into the server and run (replace `vpn.example.com` with **your** public hostname):

```bash
sudo wgh bootstrap --endpoint-host vpn.example.com
```

> **Important:** the `--endpoint-host` flag is required on first run. It's the hostname (or IP) clients will use to reach your server over the internet. If you skip it, generated client configs will point at the `vpn.example.com` placeholder and won't connect.

That single command does all of this, idempotently:

| Step | What happens |
| --- | --- |
| Install packages | `apt-get install wireguard iptables` |
| Enable IP forwarding | Writes `/etc/sysctl.d/99-wireguard-helper.conf` and applies it live |
| Detect egress interface | Finds the NIC with the default route (usually `eth0` / `ens3`) |
| Generate server keypair | Stores in `/etc/wireguard/server_private.key` (mode `600`) |
| Write `wg0.conf` | `/etc/wireguard/wg0.conf` with the server's `[Interface]` block, listen port `51820`, NAT MASQUERADE rules in PostUp/PostDown |
| Open firewall | If UFW is active, `ufw allow 51820/udp` |
| Start the tunnel | `systemctl enable --now wg-quick@wg0` |
| Initialise state | `/etc/wireguard-helper/peers.db` (SQLite) + `/etc/wireguard-helper/settings.toml` (editable config) |

When it's done, `wg show` should print an `interface: wg0` block with a public key and a listening port.

### 2c. If you ever need to change defaults

Edit `/etc/wireguard-helper/settings.toml` and re-run `sudo wgh bootstrap` — it's idempotent, will pick up changes, and won't clobber existing peers.

Common overrides:

```bash
sudo wgh bootstrap --client-dns 1.1.1.1        # if you don't run a resolver on 10.0.0.14
sudo wgh bootstrap --lan-cidr 10.0.0.0/16      # if your LAN is bigger than /24
sudo wgh bootstrap --endpoint-host vpn.example.com
```

---

## Part 3 — Add a team member

On the server:

```bash
sudo wgh add
```

It asks two questions:

```
? User name (e.g. 'alice'): alice
? Device label (e.g. 'laptop', 'iphone'): laptop
```

Then it:

1. Generates a keypair + preshared key for this device.
2. Assigns the next free IP in `10.8.0.0/24` (e.g. `10.8.0.2`).
3. Rewrites `/etc/wireguard/wg0.conf` and applies the change live with `wg syncconf` (existing connected peers **don't drop**).
4. Saves the client config to `/etc/wireguard-helper/clients/alice-laptop.conf`.
5. Prints a QR code in the terminal containing the entire client config.

Send each teammate their `.conf` file (for desktop) or let them scan the QR (for mobile). **Treat these configs as secrets** — anyone with the file can connect.

List, inspect, revoke:

```bash
sudo wgh list                          # show all peers, active + revoked
sudo wgh show alice-laptop             # reprint config + QR
sudo wgh revoke alice-laptop           # soft revoke: drop from wg0, keep audit row
sudo wgh remove alice-laptop           # hard delete: wipe DB row, free the IP
sudo wgh status                        # wg show — handshakes, transfer, last-seen
```

---

## Part 4 — Windows client setup

1. Download and install the official WireGuard client: <https://www.wireguard.com/install/>
2. Copy the `.conf` file from the server to the teammate's laptop.
   - SCP: `scp user@server:/etc/wireguard-helper/clients/alice-laptop.conf ./`
   - Or paste its contents into a new file locally.
3. Open the WireGuard app → **Import tunnel(s) from file** → pick the `.conf`.
4. Click **Activate**.

That's it. You should now be able to:

- `ping 10.0.0.14` — reach the server
- `\\10.0.0.14\share` in Explorer — mount SMB
- Open Forge in a browser at its internal address

If you want the tunnel to come up automatically on boot, right-click the tunnel in the WireGuard app and enable **"On-demand"** / **"Enable on boot"**.

---

## Part 5 — iOS / Android client setup

1. Install WireGuard from the App Store / Play Store.
2. In the app, tap **+** → **Create from QR code** → scan the QR printed by `wgh add` (or `wgh show <label>`).
3. Name the tunnel, tap **Save**, then flip the switch to connect.

The QR is the same `.conf` encoded as a QR code — nothing extra to configure.

---

## Part 6 — Verifying a connection

On the client, once connected:

```
curl https://api.ipify.org       # should still show the client's own IP (split tunnel)
ping 10.0.0.14                   # should succeed
ping 10.8.0.1                    # server's tunnel IP — should succeed
```

On the server:

```bash
sudo wgh status
```

Look for a line like:

```
peer: <client pubkey>
  latest handshake: 12 seconds ago
  transfer: 4.23 KiB received, 7.19 KiB sent
```

A recent handshake (within ~3 minutes) confirms the tunnel is alive.

---

## Troubleshooting

**Can't reach the server / handshake never happens**
- UDP 51820 not forwarded to the server. Check router/cloud firewall.
- `vpn.example.com` resolves to the wrong IP.
- `sudo systemctl status wg-quick@wg0` — should be `active (exited)`.

**Handshake works but can't reach 10.0.0.x services**
- IP forwarding not enabled on the server: `sysctl net.ipv4.ip_forward` should return `1`.
- NAT rule missing: `sudo iptables -t nat -L POSTROUTING -n` should show a `MASQUERADE` line.
- The service is firewalled from the NAT'd source IP `10.0.0.14` — check the service's own firewall rules.

**DNS doesn't work over the tunnel**
- Client `DNS = 10.0.0.14` assumes a resolver runs on the server. If not, edit `/etc/wireguard-helper/settings.toml`, set `client_dns = "1.1.1.1"`, re-run `sudo wgh bootstrap`, and re-issue client configs with `sudo wgh show <label>`.

**Peer is connected but I want to rotate their key**
- `sudo wgh revoke alice-laptop && sudo wgh add` — revokes the old key and issues a fresh one.

---

## File layout on the server

```
/etc/wireguard/
  wg0.conf                      # WireGuard config, managed by wgh
  server_private.key            # mode 600, do not share
  server_public.key

/etc/wireguard-helper/
  settings.toml                 # editable runtime config
  peers.db                      # SQLite — peer metadata + keys
  clients/
    alice-laptop.conf           # one per peer
    alice-iphone.conf

/opt/wireguard-helper/
  src/                          # rsynced project source
  venv/                         # Python venv with the wgh CLI

/usr/local/bin/wgh              # symlink → venv's wgh entrypoint
```

---

## Security notes

- Client `.conf` files contain the client's **private key**. Transfer them over a secure channel (SSH, encrypted chat) and delete them from intermediate locations after the teammate imports.
- Every peer has a unique **preshared key** layered on top of the asymmetric crypto — protects against future post-quantum attacks on Curve25519.
- `wgh revoke` disconnects a peer immediately and atomically; no server restart needed. `wgh remove` additionally wipes the DB row and frees the IP for reuse.
- The server's private key never leaves the server.
