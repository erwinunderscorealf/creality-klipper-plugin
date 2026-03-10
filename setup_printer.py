#!/usr/bin/env python3
"""
Creality Cloud Klipper Plugin - Printer Setup
Exchanges a Creality Cloud JWT token for ThingsBoard MQTT credentials.
"""

import argparse
import json
import sys
import uuid
import requests


ENDPOINTS = [
    ("International", "https://api.crealitycloud.com/api/cxy/v2/device/user/importDevice"),
    ("China",         "https://api.crealitycloud.cn/api/cxy/v2/device/user/importDevice"),
]


def get_mac():
    return uuid.UUID(int=uuid.getnode()).hex[-12:].upper()


def exchange_token(jwt_token):
    mac = get_mac()
    data = json.dumps({"mac": mac, "iotType": 2})
    headers = {
        "Content-Type": "application/json",
        "__CXY_JWTOKEN_": jwt_token,
    }

    for label, url in ENDPOINTS:
        try:
            r = requests.post(url, data=data, headers=headers, timeout=10)
            result = r.json()
            if result.get("code") == 0 and result.get("result", {}).get("tbToken"):
                return result["result"]
        except Exception:
            continue

    return None


def main():
    parser = argparse.ArgumentParser(description="Set up a Creality Cloud printer")
    parser.add_argument("--token", required=True, help="JWT token from Creality Cloud key file")
    parser.add_argument("--moonraker-port", type=int, default=7125, help="Moonraker port")
    parser.add_argument("--output", required=True, help="Output config file path")
    args = parser.parse_args()

    result = exchange_token(args.token)

    if not result:
        print("ERROR: Could not get credentials from Creality Cloud.", file=sys.stderr)
        print("Check that your token is valid and not expired.", file=sys.stderr)
        sys.exit(1)

    config = {
        "deviceName":    result["deviceName"],
        "deviceSecret":  result["tbToken"],
        "iotType":       result.get("iotType", 2),
        "region":        1,
        "moonraker_url": f"http://localhost:{args.moonraker_port}",
    }

    with open(args.output, "w") as f:
        json.dump(config, f, indent=2)

    print(f"Saved config: deviceName={config['deviceName']}, moonraker_url={config['moonraker_url']}")


if __name__ == "__main__":
    main()
