## Futures Bot - Windows Docker Setup (End-to-End)

This guide walks you through running the bot on Windows using Docker Desktop (WSL 2 engine) and Google Secret Manager.

Assumptions:
- Windows 10/11 with admin rights
- Google account: kirancryptopython@gmail.com
- GCP Project ID: futures-bot-469715
- You already created secrets and a service account as per `SETUP_GCP_SECRET_MANAGER.md`

### 1) Install WSL 2 (once)
Open PowerShell as Administrator:
```powershell
dism.exe /online /enable-feature /featurename:Microsoft-Windows-Subsystem-Linux /all /norestart
dism.exe /online /enable-feature /featurename:VirtualMachinePlatform /all /norestart
wsl --install -d Ubuntu
wsl --set-default-version 2
```
Reboot when prompted.

### 2) Install Docker Desktop (WSL 2 engine)
- Download and install Docker Desktop for Windows.
- Open Docker Desktop → Settings → General → ensure “Use the WSL 2 based engine” is checked.
- In Settings → Resources → WSL Integration → ensure your default distro (e.g., Ubuntu) is enabled.

### 3) Get the project locally
Choose a workspace folder (e.g., `D:\futures-bot`) and clone/copy the repo into it.

### 4) Provide service account key
Place your service account JSON key in the project root as `gcp-key.json`:
- Path: `D:\futures-bot\gcp-key.json`
Ensure permissions are restricted (optional):
```powershell
icacls gcp-key.json /inheritance:r
icacls gcp-key.json /grant:r "$($env:USERNAME):(R)"
```

### 5) Create `.env` in project root
Create or update `D:\futures-bot\.env` with:
```ini
USE_GCP_SECRETS=true
GCP_PROJECT=futures-bot-469715
GCP_SECRET_PREFIX=futures-bot

# Toggle testnet/mainnet
USE_TESTNET=true

# Exchange and runtime
EXCHANGE=binanceusdm
DRY_RUN=true
MARGIN_MODE=isolated
LEVERAGE=3

# Universe / timing
UNIVERSE_SIZE=12
MAX_POSITIONS=1
TIMEFRAME=15m
HTF_TIMEFRAME=1h
POLL_SECONDS=30
MONITOR_SECONDS=10
PNL_MONITOR_SECONDS=2
ORPHAN_MONITOR_SECONDS=2
ORPHAN_PROTECT_SECONDS=45
ORPHAN_MIN_AGE_SECONDS=60
UNIVERSE_MONITOR_SECONDS=2
SCAN_WHEN_FLAT_SECONDS=10

# Risk
ACCOUNT_EQUITY_USDT=100
RISK_PER_TRADE=0.01
ABS_RISK_USDT=0
MAX_NOTIONAL_FRACTION=0.30
MIN_NOTIONAL_USDT=10
MARGIN_BUFFER_FRAC=0.90

# Strategies
STRATEGIES=mtf_ema_rsi_adx
TARGET_SPLITS=0.5,0.3,0.2

# Logging / timezone
LOG_TRADES_CSV=trades_futures.csv
TIMEZONE=indian
```

Notes:
- Do not put `API_KEY`/`API_SECRET` here. They are read from GCP automatically based on `USE_TESTNET`.
- Secret names expected in GCP: `futures-bot-api-key-testnet`, `futures-bot-api-secret-testnet`, `futures-bot-api-key-mainnet`, `futures-bot-api-secret-mainnet`.

### 6) Docker Compose (already prepared)
`docker-compose.yml` is configured to:
- Load `.env`
- Mount `gcp-key.json` into the container at `/app/gcp-key.json`
- Set `GOOGLE_APPLICATION_CREDENTIALS=/app/gcp-key.json`
- Expose the UI on port 8000

Create a `.dockerignore` file in the project root to exclude secrets and local files from the build context:
```powershell
@"
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
"@ | Set-Content .dockerignore
```

### 7) Build and run
Open PowerShell, cd to the project directory (e.g., `D:\futures-bot`) and run:
```powershell
docker compose up --build -d
docker compose ps
docker compose logs -f
```

Open the UI: http://localhost:8000

### 8) Switch environments (testnet/mainnet)
Edit `.env` and toggle `USE_TESTNET` to `false` for mainnet. Then restart:
```powershell
docker compose down
docker compose up --build -d
```

### 9) Updating the app
Pull latest changes and rebuild:
```powershell
git pull
docker compose up --build -d
```

### 10) Optional: Windows firewall
If accessing from another device on your LAN, allow inbound port 8000:
```powershell
netsh advfirewall firewall add rule name="FuturesBotUI" dir=in action=allow protocol=TCP localport=8000
```

### 11) Troubleshooting
- Secrets access fails:
  - Ensure `gcp-key.json` exists at the project root and is readable.
  - The service account has `roles/secretmanager.secretAccessor` on project `futures-bot-469715`.
  - Secret names exist for testnet/mainnet as noted above.
- Docker Desktop WSL integration:
  - Ensure WSL 2 engine is enabled and your distro is integrated in Docker Desktop settings.
- UI not reachable:
  - Check `docker compose logs -f` for errors.
  - Confirm port 8000 not blocked by firewall.


