#!/usr/bin/env python3
# coding=utf-8
"""
Creality Cloud - Klipper/Moonraker Plugin
==========================================
Connects Creality Cloud to Klipper via Moonraker's local REST API.
Replaces the OctoPrint CrealityCloud plugin for Klipper-based setups.

Author: Built for Erwin's CR10S Pro / CR10S Pro V2 / CR-X Pro setup
"""

import gzip
import json
import logging
import os
import socket
import tempfile
import threading
import time
import uuid
import requests
from contextlib import closing

from tb_device_mqtt import TBDeviceMqttClient

# ─────────────────────────────────────────────
#  Logging
# ─────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("/tmp/creality_klipper.log"),
    ],
)
logger = logging.getLogger("creality_klipper")


# ─────────────────────────────────────────────
#  Config loader
# ─────────────────────────────────────────────
class Config:
    """
    Loads config from config.json.
    Expected fields:
      - deviceName   : ThingsBoard device ID (sub field from JWT)
      - deviceSecret : tbToken returned by importDevice API
      - region       : 0 = China, 1 = International
    """
    DEFAULT_PATH = os.path.join(os.path.dirname(__file__), "config.json")

    def __init__(self, path=None):
        self.path = path or self.DEFAULT_PATH
        self._data = {}
        self.load()

    def load(self):
        if not os.path.exists(self.path):
            raise FileNotFoundError(
                f"Config file not found: {self.path}\n"
                "Run setup.py first to generate it from your JWT key file."
            )
        with open(self.path, "r") as f:
            self._data = json.load(f)
        logger.info(f"Config loaded: deviceName={self._data.get('deviceName')}, region={self._data.get('region')}")

    def get(self, key, default=None):
        return self._data.get(key, default)


# ─────────────────────────────────────────────
#  Moonraker API client
# ─────────────────────────────────────────────
class MoonrakerClient:
    """
    Talks to Moonraker's local REST API.
    Default base URL assumes plugin runs on same Pi as Moonraker.
    """
    def __init__(self, base_url="http://localhost:7125"):
        self.base_url = base_url
        self.session = requests.Session()

    def _get(self, path, params=None):
        try:
            r = self.session.get(f"{self.base_url}{path}", params=params, timeout=5)
            return r.json()
        except Exception as e:
            logger.error(f"Moonraker GET {path} failed: {e}")
            return {}

    def _post(self, path, data=None):
        try:
            r = self.session.post(f"{self.base_url}{path}", json=data or {}, timeout=5)
            return r.json()
        except Exception as e:
            logger.error(f"Moonraker POST {path} failed: {e}")
            return {}

    # ── Printer state ──────────────────────────
    def get_printer_info(self):
        return self._get("/printer/info")

    def get_print_stats(self):
        result = self._get("/printer/objects/query", params={"print_stats": ""})
        return result.get("result", {}).get("status", {}).get("print_stats", {})

    def get_temperatures(self):
        result = self._get("/printer/objects/query", params={
            "extruder": "temperature,target",
            "heater_bed": "temperature,target"
        })
        status = result.get("result", {}).get("status", {})
        return {
            "nozzle": status.get("extruder", {}).get("temperature", 0),
            "nozzle_target": status.get("extruder", {}).get("target", 0),
            "bed": status.get("heater_bed", {}).get("temperature", 0),
            "bed_target": status.get("heater_bed", {}).get("target", 0),
        }

    def get_virtual_sdcard(self):
        result = self._get("/printer/objects/query", params={"virtual_sdcard": ""})
        return result.get("result", {}).get("status", {}).get("virtual_sdcard", {})

    def get_toolhead(self):
        result = self._get("/printer/objects/query", params={"toolhead": ""})
        return result.get("result", {}).get("status", {}).get("toolhead", {})

    def get_fan(self):
        result = self._get("/printer/objects/query", params={"fan": ""})
        return result.get("result", {}).get("status", {}).get("fan", {})

    def get_ip_address(self):
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            return s.getsockname()[0]
        except Exception:
            return ""

    # ── Print control ──────────────────────────
    def pause_print(self):
        return self._post("/printer/print/pause")

    def resume_print(self):
        return self._post("/printer/print/resume")

    def cancel_print(self):
        return self._post("/printer/print/cancel")

    def start_print(self, filename):
        try:
            r = self.session.post(f"{self.base_url}/printer/print/start",
                json={"filename": filename}, timeout=30)
            return r.json()
        except Exception as e:
            logger.warning(f"Moonraker POST /printer/print/start failed: {e}")
            return {}

    # ── GCode ──────────────────────────────────
    def send_gcode(self, gcode):
        logger.info(f"Sending gcode: {gcode}")
        return self._post("/printer/gcode/script", {"script": gcode})

    def set_nozzle_temp(self, temp):
        return self.send_gcode(f"SET_HEATER_TEMPERATURE HEATER=extruder TARGET={temp}")

    def set_bed_temp(self, temp):
        return self.send_gcode(f"SET_HEATER_TEMPERATURE HEATER=heater_bed TARGET={temp}")

    def set_fan_speed(self, speed):
        # speed: 0-255 in Marlin, convert to 0.0-1.0 for Klipper
        klipper_speed = min(1.0, int(speed) / 255.0) if int(speed) > 1 else float(speed)
        return self.send_gcode(f"SET_FAN_SPEED FAN=fan SPEED={klipper_speed}")

    def get_gcodes_path(self):
        """Get the gcodes directory path from Moonraker."""
        try:
            r = requests.get(f"{self.base_url}/server/files/roots", timeout=5)
            roots = r.json().get("result", [])
            for root in roots:
                if root.get("name") == "gcodes":
                    return root.get("path")
        except Exception as e:
            logger.warning(f"Could not get gcodes path: {e}")
        return None

    def get_gcode_header(self, filename):
        """Read gcode header to extract layer height, max Z and total layers."""
        try:
            # Fetch first 3KB of gcode via Moonraker HTTP
            r = requests.get(
                f"{self.base_url}/server/files/gcodes/{filename}",
                headers={"Range": "bytes=0-3000"},
                timeout=10
            )
            layer_height = None
            max_z = None
            total_layers = None
            for line in r.text.splitlines()[:100]:
                # Creality Print V6 format
                if "total layer number:" in line.lower():
                    try:
                        total_layers = int(line.split(":")[-1].strip())
                    except:
                        pass
                if "max_z_height:" in line.lower() or ";MAXZ:" in line:
                    try:
                        max_z = float(line.split(":")[-1].strip())
                    except:
                        pass
                # CR series format
                if ";Layer height:" in line or ";Layer Height:" in line:
                    try:
                        layer_height = float(line.split(":")[-1].strip())
                    except:
                        pass
            return layer_height, max_z, total_layers
        except Exception as e:
            logger.warning(f"Could not read gcode header: {e}")
            return None, None, None

    def get_feedrate(self):
        """Get current feedrate percentage from Moonraker."""
        try:
            r = requests.get(f"{self.base_url}/printer/objects/query?gcode_move", timeout=5)
            data = r.json()
            factor = data["result"]["status"]["gcode_move"].get("speed_factor", 1.0)
            return int(factor * 100)
        except Exception:
            return 100

    def run_bed_level(self):
        """Home all axes and run full bed mesh calibration."""
        logger.info("Running bed leveling: G28 + BED_MESH_CALIBRATE")
        try:
            self.run_gcode("G28")
            time.sleep(2)
            self.run_gcode("BED_MESH_CALIBRATE")
            # Wait for calibration to complete by polling state
            for _ in range(300):  # max 5 minutes
                time.sleep(2)
                stats = self.get_print_stats()
                if stats.get("state") not in ("printing",):
                    break
            logger.info("Bed leveling complete")
        except Exception as e:
            logger.warning(f"Bed leveling failed: {e}")

    def run_gcode(self, script):
        """Send a gcode command to Moonraker."""
        requests.post(f"{self.base_url}/printer/gcode/script",
            json={"script": script}, timeout=30)

    def reset_print_state(self):
        """Clear Klipper complete state back to standby."""
        try:
            requests.post(f"{self.base_url}/printer/gcode/script",
                json={"script": "SDCARD_RESET_FILE"}, timeout=5)
        except Exception as e:
            logger.warning(f"Could not reset print state: {e}")

    def set_feedrate(self, pct):
        return self.send_gcode(f"M220 S{pct}")

    def home_axes(self, axes=None):
        if axes:
            cmd = "G28 " + " ".join(axes.upper())
        else:
            cmd = "G28"
        return self.send_gcode(cmd)

    # ── File management ────────────────────────
    def upload_file(self, file_path, filename):
        try:
            with open(file_path, "rb") as f:
                r = self.session.post(
                    f"{self.base_url}/server/files/upload",
                    files={"file": (filename, f)},
                    timeout=120
                )
            return r.json()
        except Exception as e:
            logger.error(f"File upload failed: {e}")
            return {}

    def is_printing(self):
        stats = self.get_print_stats()
        return stats.get("state") in ("printing", "paused")

    def is_paused(self):
        stats = self.get_print_stats()
        return stats.get("state") == "paused"

    def is_ready(self):
        info = self.get_printer_info()
        return info.get("result", {}).get("state") == "ready"


# ─────────────────────────────────────────────
#  State mapper
# ─────────────────────────────────────────────
# Creality state values:
# 0=idle, 1=printing, 2=complete, 3=failed, 4=cancelled, 5=paused
KLIPPER_TO_CREALITY_STATE = {
    "standby":   0,
    "printing":  1,
    "complete":  2,
    "error":     3,
    "cancelled": 4,
    "paused":    5,
}


# ─────────────────────────────────────────────
#  Main plugin class
# ─────────────────────────────────────────────
class CrealityKlipperPlugin:
    BOX_VERSION = "creality_klipper_v1.0.0"

    def __init__(self, config: Config, moonraker: MoonrakerClient):
        self.config = config
        self.moonraker = moonraker

        self.device_name = config.get("deviceName")
        self.device_secret = config.get("deviceSecret")
        self.region = config.get("region", 1)

        self._tb_host = "mqtt.crealitycloud.cn" if self.region == 0 else "mqtt.crealitycloud.com"
        self.client = None
        self._connected = False

        # Internal state cache
        self._state = -1          # -1 = not yet read from Moonraker
        self._state_known = False # True after first collect tick
        self._pause = 0
        self._stop = 0
        self._print_progress = 0
        self._nozzle_temp = -1
        self._bed_temp = -1
        self._filename = ""
        self._print_id = ""
        self._layer = 0
        self._feedrate_pct = 100
        self._auto_bed_level = bool(config.get("auto_bed_level", False))
        self._layer_height = None
        self._total_layers = None
        self._current_layer = 0
        self._last_z = 0.0
        self._dProgress = 0
        self._model = config.get("model", "Klipper Printer")
        self._is_cloud_print = False

        # Pending MQTT messages
        self._telemetry_msg = {}
        self._attributes_msg = {}
        self._lock = threading.Lock()

        # Timers
        self._running = False
        self._upload_timer = None
        self._iot_timer = None

        # Session file — persists printId across restarts
        self._session_path = config.path.replace(".json", "_session.json")

    # ── Session persistence ────────────────────
    def _save_session(self):
        """Persist current printId and state so we can close the job on restart."""
        try:
            with open(self._session_path, "w") as f:
                json.dump({"printId": self._print_id, "state": self._state}, f)
        except Exception as e:
            logger.warning(f"Could not save session: {e}")

    def _close_previous_session(self):
        """On startup, if last session had an active job, either restore or close it."""
        if not os.path.exists(self._session_path):
            return
        try:
            with open(self._session_path) as f:
                session = json.load(f)
            print_id = session.get("printId", "")
            last_state = session.get("state", 0)
            if not print_id or last_state not in (1, 5):
                return
            # If Moonraker is still printing, restore session instead of closing it
            moonraker_state = self.moonraker.get_print_stats().get("state", "standby")
            if moonraker_state in ("printing", "paused"):
                logger.info(f"Resuming previous session: Moonraker is {moonraker_state}, printId={print_id}")
                self._print_id = print_id
                self._state = 1 if moonraker_state == "printing" else 5
                return
            # Moonraker is idle — close the cloud job
            logger.info(f"Closing previous session job printId={print_id}")
            self._send_attributes({"state": 2, "printId": print_id, "mcu_is_print": 0})
            self._send_telemetry({"printProgress": 100, "printLeftTime": 0, "dProgress": 100})
            time.sleep(1)
        except Exception as e:
            logger.warning(f"Could not close previous session: {e}")
        finally:
            try:
                os.remove(self._session_path)
            except Exception:
                pass

    # ── ThingsBoard connection ─────────────────
    def connect(self):
        logger.info(f"Connecting to ThingsBoard at {self._tb_host} as {self.device_name}")
        self.client = TBDeviceMqttClient(host=self._tb_host, port=1883, username=self.device_secret)
        self.client.set_server_side_rpc_request_handler(self._on_rpc_request)
        try:
            self.client.connect(timeout=90, keepalive=60)
            self._connected = True
            logger.info("Connected to Creality Cloud (ThingsBoard)")
            time.sleep(2)
            self._close_previous_session()
            self._send_initial_attributes()
            time.sleep(1)
            self._start_timers()
        except Exception as e:
            logger.error(f"Connection failed: {e}")
            self._connected = False

    def disconnect(self):
        self._running = False
        if self._upload_timer:
            self._upload_timer.cancel()
        if self._iot_timer:
            self._iot_timer.cancel()
        if self.client:
            self.client.disconnect()
        logger.info("Disconnected from Creality Cloud")

    def _send_initial_attributes(self):
        """Send initial state so the app sees the printer immediately."""
        ip = self.moonraker.get_ip_address()
        attrs = {
            "printStartTime": " ",
            "layer": 0,
            "printedTimes": 0,
            "timesLeftToPrint": 0,
            "err": 0,
            "curPosition": " ",
            "printId": " ",
            "filename": " ",
            "video": 0,
            "netIP": ip,
            "state": 0,
            "tfCard": 1,
            "model": self._model,
            "mcu_is_print": 0,
            "boxVersion": config.get("box_version", "rasp_v2.01b99"),
            "InitString": " ",
            "APILicense": " ",
            "DIDString": " ",
            "retGcodeFileInfo": " ",
            "autohome": 0,
            "fan": 0,
            "stop": 0,
            "print": " ",
            "nozzleTemp2": 0,
            "bedTemp2": 0,
            "pause": 0,
            "opGcodeFile": " ",
            "gcodeCmd": " ",
            "setPosition": " ",
            "tag": "1.0.8",
            "led_state": 0,
            "connect": 1,
            "pid": self.config.get("pid", ""),
            "productId": self.config.get("pid", ""),
        }
        self._send_attributes(attrs)
        time.sleep(0.5)
        self._send_telemetry({
            "nozzleTemp": 0,
            "bedTemp": 0,
            "curFeedratePct": 100,
            "dProgress": 100,
            "printProgress": 0,
            "printJobTime": 0,
            "printLeftTime": 0,
        })

    # ── Timers ─────────────────────────────────
    def _start_timers(self):
        self._running = True
        self._schedule_upload()
        self._schedule_iot()

    def _schedule_upload(self):
        if not self._running:
            return
        self._upload_timer = threading.Timer(2.0, self._upload_tick)
        self._upload_timer.daemon = True
        self._upload_timer.start()

    def _schedule_iot(self):
        if not self._running:
            return
        self._iot_timer = threading.Timer(3.0, self._iot_tick)
        self._iot_timer.daemon = True
        self._iot_timer.start()

    def _upload_tick(self):
        """Collect printer data every 2 seconds."""
        try:
            self._collect_printer_data()
        except Exception as e:
            logger.error(f"Upload tick error: {e}")
        finally:
            self._schedule_upload()

    def _iot_tick(self):
        """Flush pending MQTT messages every 3 seconds."""
        try:
            self._flush_messages()
        except Exception as e:
            logger.error(f"IoT tick error: {e}")
        finally:
            self._schedule_iot()

    # ── Data collection ────────────────────────
    def _collect_printer_data(self):
        if not self._connected:
            return

        # Temperatures
        temps = self.moonraker.get_temperatures()
        nozzle = int(temps.get("nozzle", 0))
        bed = int(temps.get("bed", 0))
        self._nozzle_temp = nozzle
        self._telemetry_msg["nozzleTemp"] = nozzle
        self._bed_temp = bed
        self._telemetry_msg["bedTemp"] = bed

        # Sync actual feedrate from Moonraker
        actual_feedrate = self.moonraker.get_feedrate()
        if actual_feedrate != self._feedrate_pct:
            self._feedrate_pct = actual_feedrate
            self._telemetry_msg["curFeedratePct"] = actual_feedrate

        # Print stats
        stats = self.moonraker.get_print_stats()
        klipper_state = stats.get("state", "standby")
        creality_state = KLIPPER_TO_CREALITY_STATE.get(klipper_state, 0)

        if creality_state != self._state:
            prev_state = self._state
            self._state = creality_state
            self._attributes_msg["state"] = creality_state
            # If we were printing and now we're idle/standby, treat as complete
            grace = getattr(self, "_ignore_complete_until", 0)
            if prev_state == 1 and creality_state == 0 and time.time() > grace:
                self._state = 2
                self._attributes_msg["state"] = 2
                self._telemetry_msg["printProgress"] = 100
                self._telemetry_msg["printLeftTime"] = 0
                self._attributes_msg["mcu_is_print"] = 0
                self._attributes_msg["filename"] = " "
            self._save_session()
        # Also catch complete state directly (e.g. after service restart)
        # But respect grace period after new print job received
        grace = getattr(self, "_ignore_complete_until", 0)
        if klipper_state == "complete" and self._state != 2 and time.time() > grace:
            self._state = 2
            self._attributes_msg["state"] = 2
            self._telemetry_msg["printProgress"] = 100
            self._telemetry_msg["printLeftTime"] = 0
            self._attributes_msg["mcu_is_print"] = 0
            self._save_session()
            # Clear Klipper complete state so next job starts clean
            threading.Timer(15.0, self.moonraker.reset_print_state).start()

        # Layer tracking from Z position
        # If printing but no layer info yet, try to read from current file
        if klipper_state == "printing" and not self._layer_height:
            fname = stats.get("filename", "")
            if fname:
                layer_height, max_z, total_layers = self.moonraker.get_gcode_header(fname)
                if total_layers:
                    self._layer_height = layer_height or (max_z / total_layers if max_z else None)
                    self._total_layers = total_layers
                    self._current_layer = 0
                    self._last_z = 0.0
                    logger.info(f"Layer info (late): total={self._total_layers} layers")
                elif layer_height and max_z:
                    self._layer_height = layer_height
                    self._total_layers = int(max_z / layer_height)
                    self._current_layer = 0
                    self._last_z = 0.0
                    logger.info(f"Layer info (late): height={layer_height}mm total={self._total_layers} layers")

        if klipper_state == "printing" and self._layer_height:
            toolhead = self.moonraker.get_toolhead()
            current_z = toolhead.get("position", [0,0,0,0])[2]
            # Reset if Z dropped significantly — head came down from parking/leveling height
            if self._last_z > 5.0 and current_z < self._last_z * 0.5:
                logger.info(f"Z reset: dropped from {self._last_z:.2f} to {current_z:.2f}, resetting layer tracking")
                self._last_z = 0.0
                self._current_layer = 0
            logger.info(f"Z check: current_z={current_z} last_z={self._last_z} layer_height={self._layer_height} current_layer={self._current_layer}")
            if current_z > self._last_z + (self._layer_height * 0.5):
                self._current_layer = min(
                    self._total_layers or 9999,
                    max(1, int(current_z / self._layer_height))
                )
                self._layer = self._current_layer
                self._last_z = current_z
                self._attributes_msg["layer"] = self._current_layer
                self._attributes_msg["curLayer"] = self._current_layer
                self._telemetry_msg["layer"] = self._current_layer
                self._telemetry_msg["curLayer"] = self._current_layer
                if self._total_layers:
                    self._attributes_msg["totalLayer"] = self._total_layers
                    self._telemetry_msg["totalLayer"] = self._total_layers
                # Push printObjects on every layer change
                self._attributes_msg["printObjects"] = {
                    "layer":       self._current_layer,
                    "totalLayer":  self._total_layers or 0,
                    "printProgress": self._print_progress,
                    "printLeftTime": self._telemetry_msg.get("printLeftTime", 0),
                    "filename":    self._filename,
                    "state":       self._state,
                }

        # Progress
        if klipper_state == "printing":
            vsd = self.moonraker.get_virtual_sdcard()
            progress = int(vsd.get("progress", 0) * 100)
            if progress != self._print_progress:
                self._print_progress = progress
                self._telemetry_msg["printProgress"] = progress

            # Print times
            print_duration = int(stats.get("print_duration", 0))
            if print_duration > 0:
                self._telemetry_msg["printJobTime"] = print_duration
            # Calculate remaining time from progress
            vsd = self.moonraker.get_virtual_sdcard()
            progress = vsd.get("progress", 0)
            if progress > 0 and print_duration > 0:
                total_estimated = print_duration / progress
                time_left = int(total_estimated - print_duration)
                self._telemetry_msg["printLeftTime"] = max(0, time_left)

            # Filename
            fname = stats.get("filename", "")
            if fname and fname != self._filename:
                self._filename = fname
                self._attributes_msg["filename"] = fname
                self._attributes_msg["print"] = fname

    # ── MQTT send helpers ──────────────────────
    def _flush_messages(self):
        with self._lock:
            if self._telemetry_msg:
                self._send_telemetry(self._telemetry_msg.copy())
                self._telemetry_msg.clear()
            if self._attributes_msg:
                self._send_attributes(self._attributes_msg.copy())
                self._attributes_msg.clear()

    def _send_telemetry(self, payload):
        try:
            self.client.send_telemetry(payload)
        except Exception as e:
            logger.error(f"send_telemetry failed: {e}")

    def _send_attributes(self, payload):
        try:
            logger.debug(f"send_attributes: {payload}")
            self.client.send_attributes(payload)
        except Exception as e:
            logger.error(f"send_attributes failed: {e}")

    # ── RPC handler ────────────────────────────
    def _on_rpc_request(self, request_id, request_body):
        """
        Handle server-side RPC calls from Creality Cloud.
        Methods contain 'set' or 'get', params contain property name/value.
        """
        logger.info(f"RPC request id={request_id} body={request_body}")
        method = request_body.get("method", "")
        params = request_body.get("params", {})
        response = {"code": 0}

        try:
            if "set" in method:
                for prop, value in params.items():
                    self._handle_set(prop, value)
            elif "get" in method:
                for prop in params:
                    response.update(self._handle_get(prop))
            else:
                logger.warning(f"Unhandled RPC method: {method} params={params}")
        except Exception as e:
            logger.error(f"RPC handler error: {e}")
            response = {"code": -1}

        self.client.send_rpc_reply(request_id, json.dumps(response))

    def _handle_set(self, prop, value):
        """Translate Creality property sets into Klipper/Moonraker actions."""
        logger.info(f"SET {prop} = {value}")

        if prop == "print":
            # Download gcode from URL and print
            self._is_cloud_print = True
            self._layer = 0
            self._print_id = ""  # Clear old printId so new one from cloud is used
            # Reset Klipper complete state immediately to avoid premature dialog
            self.moonraker.reset_print_state()
            # Reset state immediately so app doesn't show premature completion dialog
            self._state = 1
            self._attributes_msg["state"] = 1
            self._attributes_msg["mcu_is_print"] = 1
            self._telemetry_msg["printProgress"] = 0
            self._telemetry_msg["printLeftTime"] = 0
            # Record print start time and set grace period
            self._print_start_time = int(time.time())
            self._attributes_msg["printStartTime"] = self._print_start_time
            self._ignore_complete_until = time.time() + 30  # 30 second grace period
            t = threading.Thread(target=self._process_file_request, args=(str(value),))
            t.daemon = True
            t.start()

        elif prop == "pause":
            v = int(value)
            self._pause = v
            if v == 1:
                self.moonraker.pause_print()
                self._set_state(5)
            else:
                self.moonraker.resume_print()
                self._set_state(1)
            self._attributes_msg["pause"] = v

        elif prop == "stop":
            v = int(value)
            if v == 1:
                self.moonraker.cancel_print()
            self._set_state(4)
            self._attributes_msg["stop"] = 1
            self._attributes_msg["printProgress"] = 0

        elif prop == "gcodeCmd":
            self.moonraker.send_gcode(str(value))
            self._attributes_msg["gcodeCmd"] = str(value)

        elif prop == "nozzleTemp2":
            self.moonraker.set_nozzle_temp(int(value))
            self._attributes_msg["nozzleTemp2"] = int(value)

        elif prop == "bedTemp2":
            self.moonraker.set_bed_temp(int(value))
            self._attributes_msg["bedTemp2"] = int(value)

        elif prop == "curFeedratePct":
            pct = max(10, min(500, int(value)))  # Allow 10-500%
            self._feedrate_pct = pct
            self.moonraker.set_feedrate(pct)
            self._telemetry_msg["curFeedratePct"] = pct

        elif prop == "fan":
            v = int(value)
            if v == 1:
                self.moonraker.set_fan_speed(255)
            else:
                self.moonraker.set_fan_speed(0)
            self._attributes_msg["fan"] = v

        elif prop == "led":
            # CR10S Pro / CR-X Pro don't have LED control via Klipper by default
            # Add your LED gcode here if you have one configured
            v = int(value)
            logger.info(f"LED set to {v} - configure LED gcode in your printer.cfg if needed")
            self._attributes_msg["led_state"] = v

        elif prop == "autohome":
            self.moonraker.home_axes()
            self._attributes_msg["autohome"] = 1

        elif prop == "opGcodeFile":
            self._handle_op_gcode_file(str(value))

        elif prop == "reqGcodeFile":
            # File listing - not yet implemented, return empty
            self._send_attributes({"retGcodeFileInfo": "[]"})

        elif prop in ("jwtToken", "token"):
            # Creality Cloud session token refresh - acknowledge and ignore
            logger.info("jwtToken refresh received, acknowledging")

        elif prop == "printId":
            self._print_id = value
            self._send_attributes({"printId": value})
            self._save_session()

        elif prop == "ReqPrinterPara":
            v = int(value)
            if v == 1:
                # Request position
                toolhead = self.moonraker.get_toolhead()
                pos = toolhead.get("position", [0,0,0,0])
                position = f"X:{pos[0]:.2f} Y:{pos[1]:.2f} Z:{pos[2]:.2f}"
                self._send_attributes({"curPosition": position, "autohome": 0})
            elif v == 0:
                # Request feedrate + layer info
                self._send_telemetry({"curFeedratePct": self._feedrate_pct})
                current_state = max(0, self._state)
                attrs = {
                    "state": current_state,
                    "layer": self._current_layer,
                    "totalLayer": self._total_layers or 0,
                    "printObjects": {
                        "layer": self._current_layer,
                        "totalLayer": self._total_layers or 0,
                        "printProgress": self._print_progress,
                        "printLeftTime": self._telemetry_msg.get("printLeftTime", 0),
                        "filename": self._filename,
                        "state": current_state,
                    }
                }
                # If idle, explicitly clear download/print state so the app
                # drops any stale dialog it cached from a previous session
                if current_state not in (1, 5):
                    attrs["mcu_is_print"] = 0
                    self._send_telemetry({"dProgress": 100})
                self._send_attributes(attrs)

        elif prop in ("enableAutoLevel", "autoLevel", "bedLevel", "autoLeveling",
                      "enableBedLevel", "bedCalibration", "levelMode", "enableCfs"):
            v = int(value) if str(value).isdigit() else value
            if prop == "enableCfs":
                logger.info("CFS not supported on CR series, ignoring")
            else:
                logger.info(f"Bed leveling request: {prop} = {v}")
                self._auto_bed_level = bool(v)
        else:
            logger.warning(f"Unhandled SET property: {prop} = {value}")

    def _handle_get(self, prop):
        """Return current value for requested property."""
        logger.info(f"GET {prop}")
        temps = self.moonraker.get_temperatures()
        stats = self.moonraker.get_print_stats()
        mapping = {
            "nozzleTemp":    int(temps.get("nozzle", 0)),
            "bedTemp":       int(temps.get("bed", 0)),
            "state":         self._state,
            "printProgress": self._print_progress,
            "pause":         self._pause,
            "stop":          self._stop,
            "fan":           0,
            "model":         self._model,
            "netIP":         self.moonraker.get_ip_address(),
            "layer":         self._layer,
            "filename":      self._filename,
            "printId":       self._print_id,
            "printObjects":  {
                "layer":       self._current_layer,
                "totalLayer":  self._total_layers or 0,
                "printProgress": self._print_progress,
                "printLeftTime": self._telemetry_msg.get("printLeftTime", 0),
                "filename":    self._filename,
                "state":       self._state,
            },
        }
        return {prop: mapping.get(prop, "")}

    def _set_state(self, state):
        if state != self._state:
            self._state = state
            self._attributes_msg["state"] = state
            self._save_session()

    # ── File handling ──────────────────────────
    def _handle_op_gcode_file(self, value):
        """Handle local file print requests."""
        if "print" in value and "local" in value:
            filename = value.replace("printbox:/local/", "").strip()
            logger.info(f"Starting local file: {filename}")
            self.moonraker.start_print(filename)
        self._attributes_msg["opGcodeFile"] = value

    def _process_file_request(self, download_url):
        """Download a gcode file from Creality Cloud and start printing."""
        logger.info(f"Downloading file from: {download_url}")
        self._dProgress = 0
        self._attributes_msg["dProgress"] = 0

        try:
            filename = os.path.basename(download_url.split("?")[0])
            if filename.endswith(".gz"):
                local_filename = filename[:-3]
            else:
                local_filename = filename

            temp_dir = tempfile.gettempdir()
            temp_path = os.path.join(temp_dir, filename)
            final_path = os.path.join(temp_dir, local_filename)

            # Download
            self._download_file(download_url, temp_path)

            # Decompress if needed
            if temp_path.endswith(".gz"):
                logger.info("Decompressing gzip file")
                with gzip.open(temp_path, "rb") as gz:
                    with open(final_path, "wb") as out:
                        out.write(gz.read())
                os.remove(temp_path)
            else:
                final_path = temp_path

            # Upload to Moonraker
            logger.info(f"Uploading {local_filename} to Moonraker")
            result = self.moonraker.upload_file(final_path, local_filename)
            logger.info(f"Upload result: {result}")

            # Read gcode header for layer info
            layer_height, max_z, total_layers = self.moonraker.get_gcode_header(local_filename)
            if total_layers:
                self._layer_height = layer_height or (max_z / total_layers if max_z else None)
                self._total_layers = total_layers
                self._current_layer = 0
                self._last_z = 0.0
                logger.info(f"Layer info: total={self._total_layers} layers")
            elif layer_height and max_z:
                self._layer_height = layer_height
                self._total_layers = int(max_z / layer_height)
                self._current_layer = 0
                self._last_z = 0.0
                logger.info(f"Layer info: height={layer_height}mm, total={self._total_layers} layers")
            else:
                self._layer_height = None
                self._total_layers = None
                self._current_layer = 0
                self._last_z = 0.0

            # Run bed leveling if requested
            if self._auto_bed_level:
                logger.info("Auto bed leveling enabled — running before print")
                self.moonraker.run_bed_level()
                # Re-read from config file so manage.sh toggles take effect
                try:
                    import json as _json
                    with open(self.config.path) as _f:
                        _fresh = _json.load(_f)
                    self._auto_bed_level = bool(_fresh.get("auto_bed_level", False))
                except Exception:
                    self._auto_bed_level = bool(self.config.get("auto_bed_level", False))

            # Start printing — reset grace period now that print is actually starting
            self._ignore_complete_until = time.time() + 60
            time.sleep(1)
            self.moonraker.start_print(local_filename)

            # Update state
            self._is_cloud_print = True
            # Keep printId from cloud if we have one, otherwise generate
            if not self._print_id:
                self._print_id = str(uuid.uuid1()).replace("-", "")
            self._set_state(1)
            self._attributes_msg["printId"] = self._print_id
            self._attributes_msg["mcu_is_print"] = 1
            self._attributes_msg["printStartTime"] = str(int(time.time()))

            # Cleanup
            try:
                os.remove(final_path)
            except Exception:
                pass

        except Exception as e:
            logger.error(f"File download/print failed: {e}")
            self._set_state(3)
            self._attributes_msg["err"] = 2  # DOWNLOAD_FAIL

    def _download_file(self, url, file_path):
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 6.1; WOW64) AppleWebKit/537.36"
        }
        with closing(requests.get(url, headers=headers, stream=True, timeout=30)) as response:
            content_size = int(response.headers.get("content-length", 1))
            data_count = 0
            last_report = time.time()
            with open(file_path, "wb") as f:
                for chunk in response.iter_content(chunk_size=1024):
                    f.write(chunk)
                    data_count += len(chunk)
                    if time.time() - last_report > 2:
                        last_report = time.time()
                        progress = int((data_count / content_size) * 100)
                        self._dProgress = progress
                        self._telemetry_msg["dProgress"] = progress
        self._dProgress = 100
        self._telemetry_msg["dProgress"] = 100

    # ── Main loop ──────────────────────────────
    def run(self):
        self.connect()
        if not self._connected:
            logger.error("Could not connect. Check your config.json and network.")
            return
        logger.info("Plugin running. Press Ctrl+C to stop.")
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            logger.info("Shutting down...")
        finally:
            self.disconnect()


# ─────────────────────────────────────────────
#  Entry point
# ─────────────────────────────────────────────
if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default=None, help="Path to config file")
    args = parser.parse_args()
    config = Config(args.config)
    moonraker = MoonrakerClient(base_url=config.get("moonraker_url", "http://localhost:7125"))
    plugin = CrealityKlipperPlugin(config, moonraker)
    plugin.run()
