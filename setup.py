#!/usr/bin/env python3
# coding=utf-8
"""
Creality Klipper Plugin - Setup
================================
Run this once to exchange your Creality Cloud JWT key file
for ThingsBoard MQTT credentials.

Usage:
    python3 setup.py --keyfile /path/to/keyfile.txt
    python3 setup.py --token eyJhbGci...
"""

import argparse
import json
import os
import random
import sys
import time
import uuid
import requests


CHINA_URL    = "https://api.crealitycloud.cn"
OVERSEAS_URL = "https://api.crealitycloud.com"
CONFIG_PATH  = os.path.join(os.path.dirname(__file__), "config.json")


def get_mac():
    return uuid.UUID(int=uuid.getnode()).hex[-12:].upper()


def import_device(token):
    """
    POST to Creality importDevice endpoint with JWT token.
    Returns the API response dict.
    """
    mac = get_mac()
    data = json.dumps({"mac": mac, "iotType": 2})
    headers = {
        "Content-Type": "application/json",
        "__CXY_JWTOKEN_": token,
    }

    print(f"Using MAC address: {mac}")

    for label, base_url in [("China", CHINA_URL), ("International", OVERSEAS_URL)]:
        url = base_url + "/api/cxy/v2/device/user/importDevice"
        print(f"Trying {label} endpoint: {url}")
        try:
            response = requests.post(url, data=data, headers=headers, timeout=10)
            res = response.json()
            if res.get("result"):
                print(f"Success via {label} endpoint!")
                return res, (0 if label == "China" else 1)
        except Exception as e:
            print(f"  {label} endpoint failed: {e}")

    return None, None


def save_config(result, region):
    config = {
        "deviceName":   result["deviceName"],
        "deviceSecret": result["tbToken"],
        "iotType":      result.get("iotType", 2),
        "region":       region,
        "moonraker_url": "http://localhost:7125",
    }
    with open(CONFIG_PATH, "w") as f:
        json.dump(config, f, indent=2)
    print(f"\nConfig saved to: {CONFIG_PATH}")
    print(f"  deviceName : {config['deviceName']}")
    print(f"  region     : {'China' if region == 0 else 'International'}")
    print(f"  iotType    : {config['iotType']}")
    print(f"\nMoonraker URL is set to: {config['moonraker_url']}")
    print("Edit config.json if your Moonraker runs on a different address.")


def main():
    parser = argparse.ArgumentParser(description="Creality Klipper Plugin Setup")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--keyfile", help="Path to the Creality key file downloaded from the app")
    group.add_argument("--token",   help="JWT token string directly")
    parser.add_argument("--output", default=None, help="Path to save config (default: config.json)")
    args = parser.parse_args()

    if args.keyfile:
        if not os.path.exists(args.keyfile):
            print(f"Error: Key file not found: {args.keyfile}")
            sys.exit(1)
        with open(args.keyfile, "r") as f:
            token = f.read().strip()
    else:
        token = args.token.strip()

    print("\nCreality Klipper Plugin - Setup")
    print("=" * 40)
    print(f"Token starts with: {token[:30]}...")

    if args.output:
        global CONFIG_PATH
        CONFIG_PATH = args.output

    res, region = import_device(token)
    if res is None:
        print("\nError: Could not get credentials from Creality Cloud.")
        print("Check that your token is valid and not expired.")
        sys.exit(1)

    save_config(res["result"], region)
    print("\nSetup complete! You can now run the plugin:")
    print("  python3 creality_klipper.py")


if __name__ == "__main__":
    main()
