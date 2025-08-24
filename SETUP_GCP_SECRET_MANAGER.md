## Google Cloud Secret Manager setup (Windows, Google Cloud SDK Shell)

This guide configures Google Secret Manager so the bot reads API keys directly from GCP and auto-switches between testnet/mainnet using `USE_TESTNET`.

Assumptions:
- Google account: kirancryptopython@gmail.com
- Project ID: futures-bot-469715
- You’re using Google Cloud SDK Shell (PowerShell) on Windows

### 0) Sign in and select the project
```powershell
gcloud auth list
gcloud config set account kirancryptopython@gmail.com
gcloud auth login
gcloud projects list --format="table(projectId,name)"
gcloud config set project futures-bot-469715
```

### 1) Fix Application Default Credentials (ADC) and quota project
```powershell
gcloud auth application-default login
gcloud auth application-default set-quota-project futures-bot-469715
```

### 2) Enable Secret Manager API
```powershell
gcloud services enable secretmanager.googleapis.com
```

### 3) Create secrets and upload values (testnet + mainnet)
```powershell
# Create secrets
gcloud secrets create futures-bot-api-key-testnet --replication-policy="automatic"
gcloud secrets create futures-bot-api-secret-testnet --replication-policy="automatic"
gcloud secrets create futures-bot-api-key-mainnet --replication-policy="automatic"
gcloud secrets create futures-bot-api-secret-mainnet --replication-policy="automatic"

# Prompt for secret values (no newline)
$TEST_API_KEY    = Read-Host -Prompt "Enter TESTNET API KEY"
$TEST_API_SECRET = Read-Host -Prompt "Enter TESTNET API SECRET"
$MAIN_API_KEY    = Read-Host -Prompt "Enter MAINNET API KEY"
$MAIN_API_SECRET = Read-Host -Prompt "Enter MAINNET API SECRET"

# Write temp files, upload, then delete
[System.IO.File]::WriteAllText("key_testnet.txt", $TEST_API_KEY)
[System.IO.File]::WriteAllText("sec_testnet.txt", $TEST_API_SECRET)
[System.IO.File]::WriteAllText("key_mainnet.txt", $MAIN_API_KEY)
[System.IO.File]::WriteAllText("sec_mainnet.txt", $MAIN_API_SECRET)

gcloud secrets versions add futures-bot-api-key-testnet --data-file=key_testnet.txt
gcloud secrets versions add futures-bot-api-secret-testnet --data-file=sec_testnet.txt
gcloud secrets versions add futures-bot-api-key-mainnet --data-file=key_mainnet.txt
gcloud secrets versions add futures-bot-api-secret-mainnet --data-file=sec_mainnet.txt

Remove-Item key_testnet.txt, sec_testnet.txt, key_mainnet.txt, sec_mainnet.txt
```

### 4) Create a service account and grant access
```powershell
gcloud iam service-accounts create futures-bot-sa --display-name "Futures Bot SA"

gcloud projects add-iam-policy-binding futures-bot-469715 `
  --member="serviceAccount:futures-bot-sa@futures-bot-469715.iam.gserviceaccount.com" `
  --role="roles/secretmanager.secretAccessor"
```

### 5) Create a key for local use and set env var
```powershell
$KEY_PATH = "$env:USERPROFILE\futures-bot-sa.json"
gcloud iam service-accounts keys create $KEY_PATH `
  --iam-account "futures-bot-sa@futures-bot-469715.iam.gserviceaccount.com"

setx GOOGLE_APPLICATION_CREDENTIALS "$KEY_PATH"
$env:GOOGLE_APPLICATION_CREDENTIALS = "$KEY_PATH"
```

Troubleshooting key creation on Linux (permission denied):
```bash
# Ensure the target folder is owned by your user
sudo mkdir -p /home/<USER>/futures-bot
sudo chown -R <USER>:<USER> /home/<USER>/futures-bot
chmod 700 /home/<USER>/futures-bot

# Create the key in your home or /tmp, then move and set perms
gcloud iam service-accounts keys create ~/gcp-key.json \
  --iam-account futures-bot-sa@futures-bot-469715.iam.gserviceaccount.com
mv ~/gcp-key.json ~/futures-bot/gcp-key.json
chmod 600 ~/futures-bot/gcp-key.json
```

### 6) Verify secret access
```powershell
gcloud secrets versions access latest --secret=futures-bot-api-key-testnet | echo "OK testnet key"
gcloud secrets versions access latest --secret=futures-bot-api-key-mainnet | echo "OK mainnet key"
```

Service account on GCE VM (no key file):
```bash
gcloud config set project futures-bot-469715
gcloud config set compute/zone asia-south1-c
gcloud compute instances stop future-bot --zone asia-south1-c
gcloud compute instances set-service-account future-bot \
  --zone asia-south1-c \
  --service-account futures-bot-sa@futures-bot-469715.iam.gserviceaccount.com \
  --scopes=https://www.googleapis.com/auth/cloud-platform
gcloud compute instances start future-bot --zone asia-south1-c
```

### 7) Bot configuration (.env)
```bash
USE_GCP_SECRETS=true
GCP_PROJECT=futures-bot-469715
GCP_SECRET_PREFIX=futures-bot

# Auto-selects secrets by this flag
USE_TESTNET=true      # false for mainnet; secrets auto-switch

# Exchange + runtime
EXCHANGE=binanceusdm
DRY_RUN=true
MARGIN_MODE=isolated
```

### 8) Docker usage (optional)
- Place your service account key at project root: `gcp-key.json`
- The provided `docker-compose.yml` already mounts `gcp-key.json` and sets `GOOGLE_APPLICATION_CREDENTIALS=/app/gcp-key.json`.

Run:
```powershell
docker compose up --build
```

Switch testnet/mainnet by toggling `USE_TESTNET` in `.env`, then:
```powershell
docker compose down
docker compose up --build
```

If you see permission or project warnings:
- Ensure active account is `kirancryptopython@gmail.com` and ADC quota project is `futures-bot-469715` (steps 0–1).
- Confirm secrets exist and the service account has role `Secret Manager Secret Accessor`.


