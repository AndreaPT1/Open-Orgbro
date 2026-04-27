#!/usr/bin/env python3
"""Connect to a BLE device and dump its GATT services/characteristics."""

from __future__ import annotations

import argparse
import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from bleak import BleakClient, BleakScanner


def _match_device(name: str | None, address: str, needle: str) -> bool:
    haystack = f"{name or ''} {address}".lower()
    return needle.lower() in haystack


async def resolve_address(filter_text: str, timeout: float) -> tuple[str, str | None]:
    devices = await BleakScanner.discover(timeout=timeout)
    for device in devices:
        if _match_device(device.name, device.address, filter_text):
            return device.address, device.name
    names = [f"{device.name or '<no name>'} {device.address}" for device in devices]
    raise SystemExit(
        "No matching BLE device found.\n"
        f"Filter: {filter_text}\n"
        "Seen devices:\n- " + "\n- ".join(names)
    )


async def dump_gatt(address: str, name: str | None) -> dict[str, Any]:
    async with BleakClient(address) as client:
        try:
            services = await client.get_services()
        except AttributeError:
            services = client.services

        service_items: list[dict[str, Any]] = []
        for service in services:
            characteristics: list[dict[str, Any]] = []
            for char in service.characteristics:
                characteristics.append(
                    {
                        "uuid": char.uuid,
                        "description": char.description,
                        "handle": char.handle,
                        "properties": list(char.properties),
                        "descriptors": [
                            {
                                "uuid": descriptor.uuid,
                                "handle": descriptor.handle,
                            }
                            for descriptor in char.descriptors
                        ],
                    }
                )
            service_items.append(
                {
                    "uuid": service.uuid,
                    "description": service.description,
                    "handle": service.handle,
                    "characteristics": characteristics,
                }
            )

        return {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "device": {
                "name": name,
                "address": address,
                "connected": client.is_connected,
            },
            "services": service_items,
        }


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--address", default=None, help="BLE address/UUID to connect to directly.")
    parser.add_argument("--filter", default="x3", help="Device name/address substring used when --address is omitted.")
    parser.add_argument("--timeout", type=float, default=8.0)
    parser.add_argument("--out", default=None, help="Optional JSON output path.")
    args = parser.parse_args()

    name = None
    address = args.address
    if not address:
        address, name = await resolve_address(args.filter, args.timeout)

    payload = await dump_gatt(address, name)
    text = json.dumps(payload, indent=2, ensure_ascii=False)
    print(text)

    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text + "\n", encoding="utf-8")


if __name__ == "__main__":
    asyncio.run(main())

