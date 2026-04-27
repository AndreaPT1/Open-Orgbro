#!/usr/bin/env python3
"""Scan nearby BLE devices and optionally save advertisement metadata."""

from __future__ import annotations

import argparse
import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from bleak import BleakScanner


def _bytes_map_to_hex(data: dict[int, bytes] | None) -> dict[str, str]:
    if not data:
        return {}
    return {str(key): value.hex() for key, value in data.items()}


def _matches_filter(device: dict[str, Any], needle: str | None) -> bool:
    if not needle:
        return True
    haystack = " ".join(
        str(device.get(key) or "")
        for key in ("name", "address", "local_name", "service_uuids")
    ).lower()
    return needle.lower() in haystack


async def scan(timeout: float, filter_text: str | None) -> dict[str, Any]:
    seen: list[dict[str, Any]] = []

    try:
        result = await BleakScanner.discover(timeout=timeout, return_adv=True)
        iterable = result.values()
    except TypeError:
        devices = await BleakScanner.discover(timeout=timeout)
        iterable = [(device, None) for device in devices]

    for device, adv in iterable:
        item = {
            "name": device.name,
            "address": device.address,
            "rssi": getattr(adv, "rssi", None) if adv else getattr(device, "rssi", None),
            "local_name": getattr(adv, "local_name", None) if adv else None,
            "service_uuids": list(getattr(adv, "service_uuids", []) or []) if adv else [],
            "manufacturer_data": _bytes_map_to_hex(getattr(adv, "manufacturer_data", None) if adv else None),
            "service_data": {
                key: value.hex()
                for key, value in (getattr(adv, "service_data", {}) or {}).items()
            } if adv else {},
        }
        if _matches_filter(item, filter_text):
            seen.append(item)

    return {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "timeout_seconds": timeout,
        "filter": filter_text,
        "devices": seen,
    }


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--timeout", type=float, default=8.0)
    parser.add_argument("--filter", default=None, help="Filter by name/address/service substring, e.g. x3 or orgbro.")
    parser.add_argument("--out", default=None, help="Optional JSON output path.")
    args = parser.parse_args()

    payload = await scan(args.timeout, args.filter)
    text = json.dumps(payload, indent=2, ensure_ascii=False)
    print(text)

    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text + "\n", encoding="utf-8")


if __name__ == "__main__":
    asyncio.run(main())

