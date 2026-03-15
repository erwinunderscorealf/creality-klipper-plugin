# Creality Cloud - Klipper Plugin

Connects your Klipper-based printer directly to Creality Cloud,
without OctoPrint. Built for CR10S Pro, CR10S Pro V2, CR-X Pro, and CR-K1/K1C.

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
| LED control | ✅ Requires `[output_pin LED]` in printer.cfg |
| Video streaming (Fluidd) | ✅ Via go2rtc, see below |
| Video streaming (Creality Cloud app, K1C) | ✅ WebRTC via go2rtc, see below ⚠️ |

## Video streaming

Both Fluidd (local network) and the Creality Cloud app (WebRTC) are supported via [go2rtc](https://github.com/AlexxIT/go2rtc).

### 1. Download go2rtc

```bash
curl -L -o ~/go2rtc https://github.com/AlexxIT/go2rtc/releases/latest/download/go2rtc_linux_arm64
chmod +x ~/go2rtc
```

### 2. Create ~/go2rtc.yaml

```yaml
api:
  listen: :1984
  origin: "*"

ffmpeg:
  bin: ffmpeg
  # Required for Creality Cloud app WebRTC: encode H264 Baseline so the
  # app's decoder can handle it. Without this, go2rtc uses High Profile
  # and the app shows a frozen still instead of live video.
  h264: "-c:v libx264 -g:v 30 -profile:v baseline -level:v 3.1 -preset:v superfast -tune:v zerolatency -pix_fmt:v yuv420p -an"

streams:
  # IP camera (e.g. Tapo C100) — RTSP to MJPEG for Fluidd
  my_camera:
    - rtsp://user:password@192.168.x.x:554/stream1
    - "ffmpeg:my_camera#video=mjpeg"

  # K1C built-in USB camera — MJPEG to H264 for WebRTC
  camera_K1C:
    - http://<k1c-ip>:8080/?action=stream
    - "ffmpeg:camera_K1C#video=h264"

webrtc:
  listen: :8555
  candidates:
    - <pi-ip>
```

Add one stream entry per camera. The `ffmpeg` transcoding line converts the source to the required format.

### 3. Install as a systemd service

```bash
sudo tee /etc/systemd/system/go2rtc.service << 'EOF'
[Unit]
Description=go2rtc RTSP proxy
After=network.target

[Service]
User=pi
ExecStart=/home/pi/go2rtc -config /home/pi/go2rtc.yaml
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable go2rtc
sudo systemctl start go2rtc
```

### 4. Add to Fluidd

In Fluidd go to **Settings → Webcams → Add webcam**:
- Stream type: **MJPEG Stream**
- URL: `http://<pi-ip>:1984/api/stream.mjpeg?src=my_camera`

### 5. Creality Cloud app (K1C WebRTC)

For K1C, add `webrtc_stream` to your config.json:

```json
{
  "webrtc_stream": "camera_K1C",
  "camera_port": 8080
}
```

When you open the camera in the Creality Cloud app, the plugin handles the WebRTC signaling automatically — no extra setup needed. The plugin:
- Receives the per-session TURN credentials from Creality's signaling server
- Updates go2rtc with those credentials for NAT traversal
- Bridges the WebRTC offer/answer between the app and go2rtc

> Note: Also install `pyyaml` in the plugin virtualenv (`pip install pyyaml`) as it is required for dynamic TURN config updates.

## Known limitations

**WebRTC camera: close + reopen shows hourglass (Creality Cloud app bug)**

After watching the camera stream in the app, closing and reopening it in the same app session results in a perpetual hourglass — no video loads. This is a **Creality Cloud platform bug**, not a plugin issue. It affects all Creality printers including factory-stock devices (confirmed on an unmodified K2).

**Workaround:** fully close and reopen the Creality Cloud app. The first time you open the camera after that, video works.

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
