# GPU1 Power Management

This document describes the automatic Wake-on-LAN (WoL) system for gpu1, which powers on the GPU server when LLM processing is needed and optionally shuts it down after idle periods.

## Overview

When the backend needs to process items through the LLM and gpu1 is sleeping (Ollama unreachable), the system:

1. Sends a Wake-on-LAN magic packet to wake gpu1
2. Polls until Ollama becomes available (up to 2 minutes)
3. Proceeds with normal LLM processing
4. After processing completes and idle timeout is reached, optionally shuts down gpu1

This enables energy-efficient operation where gpu1 only runs when needed.

## Architecture

```
┌────────────────────────────────────────────────────────────────┐
│               docker-ai (production server)                     │
├────────────────────────────────────────────────────────────────┤
│  Backend Container (macvlan network: 192.168.0.200)            │
│  ┌──────────────────────────────────────────────────────────┐  │
│  │  LLM Worker                                               │  │
│  │    └── _get_processor()                                   │  │
│  │          └── GPU1PowerManager                             │  │
│  │                ├── is_available() → check Ollama          │  │
│  │                ├── wake() → send WoL packet               │  │
│  │                ├── wait_for_ready() → poll Ollama         │  │
│  │                └── shutdown() → SSH command               │  │
│  └──────────────────────────────────────────────────────────┘  │
└────────────────────────────────────────────────────────────────┘
                              │
                              │ UDP broadcast (WoL port 9)
                              │ SSH (shutdown)
                              ▼
                    ┌─────────────────┐
                    │      gpu1       │
                    │  192.168.0.141  │
                    │                 │
                    │  MAC: 58:47:ca  │
                    │  :7c:18:cc      │
                    └─────────────────┘
```

## Pre-Deployment Setup

### 1. Network Interface Discovery

On the docker-ai host, identify the LAN interface name:

```bash
ip link show
# Look for the interface connected to 192.168.0.x network
# Common names: eth0, ens18, enp0s3, etc.
```

Add to `.env`:
```bash
LAN_INTERFACE=eth0  # or whatever interface name you found
```

### 2. SSH Key Setup

The SSH key for the dedicated `ligahessen` user is already included in `ssh/id_ed25519`.

Verify the key works:
```bash
# Test connection from docker-ai host
ssh -i ssh/id_ed25519 ligahessen@192.168.0.141 "echo 'SSH works'"

# Test shutdown permission
ssh -i ssh/id_ed25519 ligahessen@192.168.0.141 "sudo shutdown --help"
```

The `ligahessen` user on gpu1 has:
- SSH key-only authentication (password locked)
- Passwordless sudo for: `shutdown`, `poweroff`, `reboot`, `halt`

### 3. IP Range Reservation

Ensure the IP range 192.168.0.200-203 is not used by DHCP on your router/DHCP server. These IPs are reserved for Docker macvlan containers.

## Configuration

Environment variables (set in `.env` or docker-compose.yml):

| Variable | Default | Description |
|----------|---------|-------------|
| `GPU1_WOL_ENABLED` | `true` | Enable/disable WoL feature |
| `GPU1_MAC_ADDRESS` | `58:47:ca:7c:18:cc` | gpu1 MAC address |
| `GPU1_BROADCAST` | `192.168.0.255` | LAN broadcast address |
| `GPU1_SSH_HOST` | `192.168.0.141` | gpu1 IP for SSH |
| `GPU1_SSH_USER` | `kamienc` | SSH user for shutdown |
| `GPU1_SSH_KEY_PATH` | `/app/ssh/id_ed25519` | SSH key path in container |
| `GPU1_AUTO_SHUTDOWN` | `true` | Shutdown after idle if we woke it |
| `GPU1_IDLE_TIMEOUT` | `300` | Seconds idle before shutdown (5 min) |
| `GPU1_WAKE_TIMEOUT` | `120` | Max wait for Ollama (2 min) |
| `LAN_INTERFACE` | `eth0` | Host network interface for macvlan |

## Verification

### Test Wake-on-LAN Manually

```bash
# Put gpu1 to sleep
ssh ligahessen@192.168.0.141 "sudo systemctl suspend"

# Wait 30 seconds, then trigger a fetch
curl -X POST http://localhost:8000/api/channels/1/fetch

# Watch backend logs for WoL activity
docker compose logs -f backend | grep -iE 'wol|wake|gpu1|ollama'
```

### Verify Ollama Availability Check

```bash
# Check if Ollama is reachable
curl -s http://192.168.0.141:11434/api/tags

# Should return list of models if gpu1 is awake
```

### Test Auto-Shutdown

```bash
# With GPU1_AUTO_SHUTDOWN=true and short timeout for testing:
# Set GPU1_IDLE_TIMEOUT=60 in .env

# Wake gpu1, process something, then wait 60+ seconds
# Verify gpu1 shuts down:
ping 192.168.0.141  # Should stop responding after timeout
```

### Check Container Network

```bash
# Verify backend has macvlan IP
docker exec liga-news-backend ip addr show
# Should show 192.168.0.200 on eth0 or similar

# Test broadcast from container
docker exec liga-news-backend ping -c1 192.168.0.255
```

## Troubleshooting

### WoL Packet Not Reaching gpu1

1. Check that macvlan network is configured:
   ```bash
   docker network ls | grep lan
   docker network inspect news-aggregator_lan
   ```

2. Verify container has LAN IP:
   ```bash
   docker exec liga-news-backend ip route
   ```

3. Check that gpu1 has WoL enabled in BIOS and network settings:
   ```bash
   # On gpu1
   sudo ethtool enp4s0 | grep Wake-on
   ```

### SSH Shutdown Fails

1. Test SSH from container:
   ```bash
   docker exec -it liga-news-backend ssh -i /app/ssh/id_ed25519 \
     -o StrictHostKeyChecking=no ligahessen@192.168.0.141 "whoami"
   ```

2. Check SSH key permissions:
   ```bash
   ls -la ssh/
   # Should be: -rw------- id_ed25519
   ```

3. Verify sudoers on gpu1:
   ```bash
   ssh ligahessen@192.168.0.141 "sudo -l" | grep shutdown
   ```

### Container Cannot Reach gpu1

1. From inside container:
   ```bash
   docker exec liga-news-backend ping -c3 192.168.0.141
   ```

2. Check that parent interface is correct:
   ```bash
   # On host
   ip link show
   # Verify LAN_INTERFACE matches the interface with 192.168.0.x
   ```

3. Recreate network if needed:
   ```bash
   docker compose down
   docker network rm news-aggregator_lan
   docker compose up -d
   ```

## Disabling WoL

To disable WoL and fall back to normal behavior (items queued for retry when Ollama unavailable):

```bash
# In .env
GPU1_WOL_ENABLED=false

# Restart backend
docker compose restart backend
```

## Log Messages

Key log messages to watch for:

```
INFO  - gpu1 not available, attempting Wake-on-LAN...
INFO  - Sent WoL packet to 58:47:ca:7c:18:cc via 192.168.0.255:9
INFO  - Waiting up to 120s for Ollama to become available...
INFO  - Ollama available after 45.3s
INFO  - gpu1 woken and ready for LLM processing
INFO  - gpu1 idle for 305s (>300s), shutting down...
INFO  - Shutting down gpu1 via SSH (ligahessen@192.168.0.141)
INFO  - gpu1 shutdown due to idle timeout, processor cleared
```

## Energy Savings

With default settings (5 min idle timeout, 30 min fetch interval), gpu1 will:
- Wake when items need LLM processing
- Stay awake while processing backlog
- Shutdown after 5 minutes of no new items
- Remain off until the next fetch cycle produces items needing LLM

Typical daily pattern:
- Fetch every 30 minutes
- gpu1 wakes 2-3 times per day for batch processing
- Total runtime: ~30 minutes per day (vs 24 hours always-on)
