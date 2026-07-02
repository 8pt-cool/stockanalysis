# Docker Deployment

This is the recommended cloud VPS deployment path.

## VPS Baseline

Use a non-mainland-China region so Telegram can connect reliably.

Recommended minimum:

```text
Ubuntu 22.04 or 24.04
2 vCPU
2 GB RAM
40 GB disk
```

## Install Docker

On a fresh Ubuntu VPS:

```sh
sudo apt-get update
sudo apt-get install -y ca-certificates curl git
sudo install -m 0755 -d /etc/apt/keyrings
sudo curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
sudo chmod a+r /etc/apt/keyrings/docker.asc
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo "$VERSION_CODENAME") stable" | sudo tee /etc/apt/sources.list.d/docker.list >/dev/null
sudo apt-get update
sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
```

Optional, let the current user run Docker without `sudo`:

```sh
sudo usermod -aG docker "$USER"
newgrp docker
```

## Deploy App

```sh
git clone https://github.com/8pt-cool/stockanalysis.git
cd stockanalysis
cp .env.docker.example .env
```

Edit `.env` and fill in private values:

```text
APP_SECRET
DEEPSEEK_API_KEY
TELEGRAM_BOT_TOKEN
TELEGRAM_ALLOWED_USER_ID
TELEGRAM_REPORT_CHAT_ID
TUSHARE_TOKEN
```

Start:

```sh
docker compose up -d --build
```

Check:

```sh
docker compose ps
docker compose logs -f stockanalysis
curl -s http://127.0.0.1:8765/api/health
```

If you expose port `8765` to the public internet, keep `APP_SECRET` strong and
restrict the VPS firewall/security group to trusted IPs when possible. Telegram
polling does not require inbound ports, so you can also keep the web UI private
and access it through SSH port forwarding.

## Data Migration From Mac

Stop the Mac service first so the same Telegram bot is not running in two
places:

```sh
launchctl bootout gui/501 ~/Library/LaunchAgents/com.charleszhang.stockanalysis.plist
```

Copy these runtime files from Mac to the VPS project directory:

```text
~/Library/Application Support/StockAnalysis/.env
~/Library/Application Support/StockAnalysis/data/trade_review.sqlite3
~/Library/Application Support/StockAnalysis/data/uploads/
```

On the VPS, put the database and uploads under:

```text
./data/trade_review.sqlite3
./data/uploads/
```

Then start Docker:

```sh
docker compose up -d --build
```

## Updates

```sh
git pull
docker compose up -d --build
```

## Backup

SQLite is stored in:

```text
./data/trade_review.sqlite3
```

Simple backup:

```sh
mkdir -p backups
cp data/trade_review.sqlite3 "backups/trade_review-$(date +%F-%H%M%S).sqlite3"
```

## Notes

- Do not commit `.env`, SQLite databases, uploads, logs, or backups.
- Do not run the same Telegram bot token on Mac and VPS at the same time.
- The cloud Docker version disables macOS local OCR by default.
- LM Studio/local vision is not expected to work on a small VPS. Use cloud
  vision later if screenshot recognition is needed.
