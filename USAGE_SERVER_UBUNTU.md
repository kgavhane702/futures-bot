## Futures Bot - Ubuntu Server Usage Guide (Docker)

This guide shows how to deploy and run the bot on a fresh Ubuntu server using Docker and Google Secret Manager.

Assumptions:
- Ubuntu 22.04/24.04, user with sudo
- Project ID: futures-bot-469715
- Service account JSON available locally (created as in SETUP_GCP_SECRET_MANAGER.md)

### 1) Update server and install prerequisites
```bash
sudo apt-get update -y && sudo apt-get upgrade -y
sudo apt-get install -y ca-certificates curl gnupg git ufw
```

### 2) Install Docker Engine and Compose plugin
```bash
sudo install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg | sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg
echo \
  "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu \
  $(. /etc/os-release && echo $UBUNTU_CODENAME) stable" | \ 
  sudo tee /etc/apt/sources.list.d/docker.list > /dev/null
sudo apt-get update -y
sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
sudo usermod -aG docker $USER
newgrp docker
docker --version
docker compose version
```

### 3) Configure firewall (optional but recommended)
```bash
sudo ufw allow OpenSSH
sudo ufw allow 8000/tcp
sudo ufw enable
sudo ufw status
```

### 4) Clone the repository
```bash
cd ~
git clone https://github.com/YOUR_GITHUB_ACCOUNT/futures-bot.git
cd futures-bot
```

### 5) Provide service account key and environment
- Copy your service account JSON to the server and place it at the project root as `gcp-key.json`.
  - Example (from your local machine):
    - `scp gcp-key.json ubuntu@YOUR_SERVER_IP:~/futures-bot/gcp-key.json`
```bash
chmod 600 gcp-key.json
```

Create `.env` in the project root:
```bash
cat > .env << 'EOF'
USE_GCP_SECRETS=true
GCP_PROJECT=futures-bot-469715
GCP_SECRET_PREFIX=futures-bot

# Toggle to select secrets automatically
USE_TESTNET=true

# Exchange and runtime
EXCHANGE=binanceusdm
DRY_RUN=true
MARGIN_MODE=isolated

# Optional: persist trades CSV outside container
# TRADES_CSV=data/trades_futures.csv
EOF
```

Optional persistence (recommended): bind a data folder and set `TRADES_CSV` to `data/...` in `.env`:
```bash
mkdir -p data
# Then in docker-compose.yml, under services.bot add:
#   volumes:
#     - ./data:/app/data
```

### 6) Run with Docker
Create a `.dockerignore` to keep secrets and local files out of the build context:
```bash
cat > .dockerignore << 'EOF'
.gitattributes
.gitignore
.git/
.venv/
__pycache__/
*.pyc
*.log
.env
gcp-key.json
data/
trades_futures.csv
EOF
```

```bash
docker compose up --build -d
docker compose ps
docker compose logs -f
```

Open the UI at: http://YOUR_SERVER_IP:8000

### 7) Switch environments (testnet/mainnet)
- Edit `.env` and set `USE_TESTNET=false` for mainnet.
```bash
docker compose down
docker compose up --build -d
```

### 8) Updating the app
```bash
git pull
docker compose up --build -d
```

### 9) Troubleshooting
- Secrets access errors:
  - Ensure `gcp-key.json` is valid and mounted (compose sets `GOOGLE_APPLICATION_CREDENTIALS=/app/gcp-key.json`).
  - Service account must have role `roles/secretmanager.secretAccessor` on project `futures-bot-469715`.
  - Secret names required:
    - `futures-bot-api-key-testnet`, `futures-bot-api-secret-testnet`
    - `futures-bot-api-key-mainnet`, `futures-bot-api-secret-mainnet`
- Cannot create key file (Operation not permitted):
  - Fix folder ownership/permissions, then create key and move it:
  ```bash
  sudo mkdir -p /home/<USER>/futures-bot
  sudo chown -R <USER>:<USER> /home/<USER>/futures-bot
  chmod 700 /home/<USER>/futures-bot

  gcloud iam service-accounts keys create ~/gcp-key.json \
    --iam-account futures-bot-sa@futures-bot-469715.iam.gserviceaccount.com

  mv ~/gcp-key.json ~/futures-bot/gcp-key.json
  chmod 600 ~/futures-bot/gcp-key.json
  ```
  - If still blocked, create in `/tmp` then move with sudo:
  ```bash
  gcloud iam service-accounts keys create /tmp/gcp-key.json \
    --iam-account futures-bot-sa@futures-bot-469715.iam.gserviceaccount.com

  sudo mv /tmp/gcp-key.json /home/<USER>/futures-bot/gcp-key.json
  sudo chown <USER>:<USER> /home/<USER>/futures-bot/gcp-key.json
  chmod 600 /home/<USER>/futures-bot/gcp-key.json
  ```
- Time sync: ensure server time is accurate (`timedatectl`), exchanges can reject skewed requests.
- UI exposure: if public, consider putting a reverse proxy with basic auth and TLS in front of port 8000.

### 10) Optional: Nginx reverse proxy (quick sketch)
```bash
sudo apt-get install -y nginx
sudo tee /etc/nginx/sites-available/futures-bot << 'EOF'
server {
    listen 80;
    server_name _;
    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
EOF
sudo ln -s /etc/nginx/sites-available/futures-bot /etc/nginx/sites-enabled/
sudo nginx -t && sudo systemctl reload nginx
```


