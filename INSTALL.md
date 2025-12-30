# Installation Guide

## Prerequisites

- Python 3.10 or higher
- Poetry (Python package manager)
- NATS server (running and accessible)
- Git

## Quick Start

### 1. Clone the Repository

```bash
git clone https://github.com/grobertson/kryten-moderator.git
cd kryten-moderator
```

### 2. Install Dependencies

```bash
uv sync
```

### 3. Configure the Service

Copy the example configuration:

```bash
cp config.example.json config.json
```

Edit `config.json` with your settings:

```json
{
  "nats_url": "nats://localhost:4222",
  "nats_subject_prefix": "cytube",
  "service_name": "kryten-moderator"
}
```

### 4. Run the Service

**Development mode:**

```bash
uv run kryten-moderator --config config.json --log-level DEBUG
```

**Using startup script (PowerShell):**

```powershell
.\start-moderator.ps1
```

**Using startup script (Bash):**

```bash
./start-moderator.sh
```

## Production Installation

### System User Setup

Create a dedicated system user:

```bash
sudo useradd -r -s /bin/false -d /opt/kryten-moderator kryten
```

### Installation Directory

```bash
sudo mkdir -p /opt/kryten-moderator
sudo chown kryten:kryten /opt/kryten-moderator
```

### Configuration Directory

```bash
sudo mkdir -p /etc/kryten/moderator
sudo chown kryten:kryten /etc/kryten/moderator
sudo cp config.example.json /etc/kryten/moderator/config.json
sudo chown kryten:kryten /etc/kryten/moderator/config.json
sudo chmod 600 /etc/kryten/moderator/config.json
```

Edit the configuration:

```bash
sudo nano /etc/kryten/moderator/config.json
```

### Log Directory

```bash
sudo mkdir -p /var/log/kryten-moderator
sudo chown kryten:kryten /var/log/kryten-moderator
```

### Install Application

```bash
cd /opt/kryten-moderator
sudo -u kryten git clone https://github.com/grobertson/kryten-moderator.git .
sudo -u kryten uv sync --no-dev
```

### Systemd Service

Install the systemd service:

```bash
sudo cp systemd/kryten-moderator.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable kryten-moderator
sudo systemctl start kryten-moderator
```

Check service status:

```bash
sudo systemctl status kryten-moderator
sudo journalctl -u kryten-moderator -f
```

## Updating

### Development

```bash
git pull
uv sync
```

### Production

```bash
cd /opt/kryten-moderator
sudo systemctl stop kryten-moderator
sudo -u kryten git pull
sudo -u kryten uv sync --no-dev
sudo systemctl start kryten-moderator
```

## Troubleshooting

### Check NATS Connection

Ensure NATS server is running:

```bash
# Check if NATS is listening
netstat -tuln | grep 4222
```

### View Logs

```bash
# Systemd service logs
sudo journalctl -u kryten-moderator -f

# Check for errors
sudo journalctl -u kryten-moderator --since "1 hour ago" | grep -i error
```

### Test Configuration

```bash
# Validate JSON syntax
python -m json.tool config.json

# Test connection manually
uv run kryten-moderator --config config.json --log-level DEBUG
```

### Permissions Issues

Ensure correct ownership:

```bash
sudo chown -R kryten:kryten /opt/kryten-moderator
sudo chown -R kryten:kryten /etc/kryten/moderator
sudo chown -R kryten:kryten /var/log/kryten-moderator
```

## Uninstalling

### Stop and Disable Service

```bash
sudo systemctl stop kryten-moderator
sudo systemctl disable kryten-moderator
sudo rm /etc/systemd/system/kryten-moderator.service
sudo systemctl daemon-reload
```

### Remove Files

```bash
sudo rm -rf /opt/kryten-moderator
sudo rm -rf /etc/kryten/moderator
sudo rm -rf /var/log/kryten-moderator
```

### Remove User

```bash
sudo userdel kryten
```
