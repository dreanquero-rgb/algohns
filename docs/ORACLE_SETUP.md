# Run Algohns 24/7 on Oracle Always-Free (app + trader, one VM)

Now that you have an Oracle account, the clean setup is: **one Always-Free VM
runs both the dashboard and the 24/7 trader.** They share the same disk, so the
strategies you save in the app are exactly the ones the trader executes — no
external database, no sleeping, 24 GB RAM.

Everything stays **paper-only**, and the trader places orders **only** when you
set `ALGO_WORKER_ENABLED=true` (otherwise it logs what it would do).

---

## 1. Create the VM
1. Oracle Cloud → **Compute → Instances → Create instance**.
2. Shape: **Ampere A1 (Always Free)** — e.g. 2 OCPU / 12 GB (up to 4/24 free).
3. Image: **Ubuntu 22.04**. Download/keep the SSH key.
4. Create. Note the **public IP**.

## 2. Open the dashboard port
- **Networking → Virtual Cloud Network → Security List → Add Ingress Rule**:
  Source `0.0.0.0/0`, TCP, destination port **8501**.
- Also open it in the VM firewall:
  ```bash
  sudo iptables -I INPUT -p tcp --dport 8501 -j ACCEPT
  sudo netfilter-persistent save   # if installed
  ```

## 3. Install Docker
```bash
sudo apt-get update && sudo apt-get install -y docker.io docker-compose-plugin git
sudo usermod -aG docker $USER && newgrp docker
```

## 4. Get the code
```bash
git clone https://github.com/dreanquero-rgb/algohns.git
cd algohns
```

## 5. Configure `.env` (never committed)
```bash
cp .env.example .env
nano .env
```
Set at least:
```
ALPACA_PAPER=true

# HFT account (section ②)
ALPACA_API_KEY=...
ALPACA_SECRET_KEY=...

# Portfolio Management account (section ①)
ALPACA_PM_API_KEY=...
ALPACA_PM_SECRET_KEY=...

# Private login for the dashboard
ALGO_APP_PASSCODE=0407

# Let the trader actually place orders (leave false to run DRY first)
ALGO_WORKER_ENABLED=true
ALGO_WORKER_INTERVAL=60     # seconds between HFT cycles
```

## 6. Start both services
```bash
docker compose -f docker-compose.oracle.yml up -d --build
```
- Dashboard: `http://<public-ip>:8501` (enter the passcode).
- Trader logs: `docker compose -f docker-compose.oracle.yml logs -f trader`

That's it — it now runs 24/7 and survives reboots (`restart: unless-stopped`).

---

## What the trader does each cycle
- **HFT** (every `ALGO_WORKER_INTERVAL` s, market open only): for each *enabled*
  HFT strategy, pull 1-minute bars, compute the signal, and go long on +1 /
  close on -1 on the **main** account.
- **PM** (once a day at the open): re-screen each *enabled* PM strategy and
  rebalance the **PM** account to the fresh weights.

Enable a strategy from the app (the toggle in the HFT library / saving a PM
strategy), and the trader picks it up on its next cycle.

## Safety
- Paper-only is enforced in code; the engine refuses a live client.
- `ALGO_WORKER_ENABLED=false` → DRY mode, no orders.
- The dashboard is behind the passcode login; for real privacy also put it
  behind HTTPS (e.g. a Caddy reverse proxy) and set a strong `ALGO_APP_PASSCODE`.

## Updating
```bash
cd algohns && git pull
docker compose -f docker-compose.oracle.yml up -d --build
```

## First run tip
Start with `ALGO_WORKER_ENABLED=false`, watch the trader logs for a few cycles
to confirm the signals look sane, then flip it to `true` and restart:
```bash
docker compose -f docker-compose.oracle.yml up -d
```
