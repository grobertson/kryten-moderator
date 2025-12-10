# Systemd Service Files

This directory contains systemd service unit files for running kryten-moderator as a system service.

## Installation

1. Copy the service file to systemd directory:
```bash
sudo cp systemd/kryten-moderator.service /etc/systemd/system/
```

2. Reload systemd daemon:
```bash
sudo systemctl daemon-reload
```

3. Enable the service to start on boot:
```bash
sudo systemctl enable kryten-moderator
```

4. Start the service:
```bash
sudo systemctl start kryten-moderator
```

## Service Management

Check service status:
```bash
sudo systemctl status kryten-moderator
```

View logs:
```bash
sudo journalctl -u kryten-moderator -f
```

Stop the service:
```bash
sudo systemctl stop kryten-moderator
```

Restart the service:
```bash
sudo systemctl restart kryten-moderator
```

## Configuration

The service expects:
- Installation directory: `/opt/kryten-moderator`
- Configuration file: `/etc/kryten/moderator/config.json`
- User/Group: `kryten`
- Log directory: `/var/log/kryten-moderator`

Make sure to create these directories and the kryten user before starting the service.
