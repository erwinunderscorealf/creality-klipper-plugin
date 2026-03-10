# Creality Cloud - Klipper Plugin

Connects your Klipper-based printer directly to Creality Cloud,
without OctoPrint. Built for CR10S Pro, CR10S Pro V2, and CR-X Pro.

## How it works

```
Creality Cloud App
       ↓  (MQTT / ThingsBoard)
mqtt.crealitycloud.com
       ↓
[This plugin on your Pi]
       ↓  (REST API)
Moonraker → Klipper → Printer
```

## Installation

### 1. Clone / copy to your Pi

```bash
cd ~
git clone <this repo> creality-klipper-plugin
cd creality-klipper-plugin
```

### 2. Create a virtual environment and install dependencies

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### 3. Run setup with your Creality key file

In the Creality Cloud app:
- Go to **Printing** → **+** → **Raspberry Pi** → **Create Raspberry Pi**
- Download the key file and copy it to your Pi

Then run setup:

```bash
python3 setup.py --keyfile /path/to/your/keyfile.txt
```

This will:
- Exchange your JWT token for ThingsBoard MQTT credentials
- Save them to `config.json`

### 4. Edit config.json if needed

```json
{
  "deviceName": "...",
  "deviceSecret": "...",
  "iotType": 2,
  "region": 1,
  "moonraker_url": "http://localhost:7125"
}
```

Change `moonraker_url` if Moonraker runs on a different address.
Change `region` to `0` for China, `1` for International.

### 5. Test it manually first

```bash
source venv/bin/activate
python3 creality_klipper.py
```

Open the Creality Cloud app — your printer should appear online!

### 6. Install as a systemd service (auto-start on boot)

```bash
sudo cp creality-klipper.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable creality-klipper
sudo systemctl start creality-klipper
```

Check status:
```bash
sudo systemctl status creality-klipper
journalctl -u creality-klipper -f
```

## Supported features

| Feature | Status |
|---|---|
| Printer online/offline status | ✅ |
| Temperature monitoring | ✅ |
| Print from cloud (URL) | ✅ |
| Print local file | ✅ |
| Pause / Resume | ✅ |
| Cancel print | ✅ |
| Send raw GCode | ✅ |
| Set nozzle temperature | ✅ |
| Set bed temperature | ✅ |
| Fan control | ✅ |
| Feed rate control | ✅ |
| Auto home | ✅ |
| Print progress reporting | ✅ |
| Print time remaining | ✅ |
| Layer reporting | ⚠️ Requires DisplayLayerProgress equivalent in Klipper |
| LED control | ⚠️ Configure gcode macros in printer.cfg |
| Video streaming | ❌ Not supported |

## Logs

```bash
tail -f /tmp/creality_klipper.log
```

## Troubleshooting

**Plugin connects but printer shows offline in app:**
- Make sure Moonraker is running: `sudo systemctl status moonraker`
- Check the Moonraker URL in config.json

**Setup fails with auth error:**
- Your JWT token may have expired — generate a new key file from the app

**Print doesn't start after download:**
- Check Moonraker logs: `journalctl -u moonraker -f`
- Make sure the uploads folder is writable
