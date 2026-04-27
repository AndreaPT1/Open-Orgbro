#!/usr/bin/env python3
"""Status-only protocol probe for ORGBRO X3.

This script subscribes to notification characteristics and sends only candidate
status/info commands. It intentionally does not send feed or raster data.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from bleak import BleakClient, BleakScanner


WRITE_CHAR = "0000ff02-0000-1000-8000-00805f9b34fb"
NOTIFY_CHARS = [
    "0000ff01-0000-1000-8000-00805f9b34fb",
    "0000ff03-0000-1000-8000-00805f9b34fb",
]


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def crc8(data: bytes) -> int:
    crc = 0
    for byte in data:
        crc ^= byte
        for _ in range(8):
            if crc & 0x80:
                crc = ((crc << 1) ^ 0x07) & 0xFF
            else:
                crc = (crc << 1) & 0xFF
    return crc


def printmaster_frame(command: int, data: bytes = b"") -> bytes:
    length = len(data)
    frame = bytearray([0x51, 0x78, command, 0x00, length & 0xFF, (length >> 8) & 0xFF])
    frame.extend(data)
    frame.append(crc8(data) if data else 0x00)
    frame.append(0xFF)
    return bytes(frame)


STATUS_COMMANDS: list[tuple[str, bytes]] = [
    ("escpos_dle_eot_printer_status", bytes([0x10, 0x04, 0x01])),
    ("escpos_dle_eot_offline_status", bytes([0x10, 0x04, 0x02])),
    ("escpos_dle_eot_error_status", bytes([0x10, 0x04, 0x03])),
    ("escpos_dle_eot_paper_status", bytes([0x10, 0x04, 0x04])),
    ("printmaster_get_dev_state", printmaster_frame(0xA3, bytes([0x00]))),
    ("printmaster_get_dev_info", printmaster_frame(0xA8, bytes([0x00]))),
]


def _match_device(name: str | None, address: str, needle: str) -> bool:
    haystack = f"{name or ''} {address}".lower()
    return needle.lower() in haystack


async def resolve_address(filter_text: str, timeout: float) -> tuple[str, str | None]:
    devices = await BleakScanner.discover(timeout=timeout)
    for device in devices:
        if _match_device(device.name, device.address, filter_text):
            return device.address, device.name
    raise SystemExit(f"No BLE device matching {filter_text!r} found.")


def sender_id(sender: Any) -> str:
    return getattr(sender, "uuid", None) or str(sender)


async def probe(address: str, name: str | None, delay: float) -> dict[str, Any]:
    events: list[dict[str, Any]] = []
    writes: list[dict[str, Any]] = []

    def on_notify(sender: Any, data: bytearray) -> None:
        events.append(
            {
                "timestamp": now_iso(),
                "sender": sender_id(sender),
                "hex": bytes(data).hex(),
                "bytes": list(data),
            }
        )

    async with BleakClient(address) as client:
        for char_uuid in NOTIFY_CHARS:
            try:
                await client.start_notify(char_uuid, on_notify)
                events.append({"timestamp": now_iso(), "subscribed": char_uuid})
            except Exception as exc:
                events.append({"timestamp": now_iso(), "subscribe_failed": char_uuid, "error": repr(exc)})

        await asyncio.sleep(delay)

        for label, payload in STATUS_COMMANDS:
            write_event = {
                "timestamp": now_iso(),
                "label": label,
                "hex": payload.hex(),
                "response_mode": "write",
            }
            try:
                await client.write_gatt_char(WRITE_CHAR, payload, response=True)
                write_event["result"] = "ack"
            except Exception as exc:
                write_event["result"] = "write_failed"
                write_event["error"] = repr(exc)
            writes.append(write_event)
            await asyncio.sleep(delay)

        await asyncio.sleep(delay * 2)

        for char_uuid in NOTIFY_CHARS:
            try:
                await client.stop_notify(char_uuid)
            except Exception:
                pass

        return {
            "timestamp": now_iso(),
            "device": {
                "name": name,
                "address": address,
                "connected": client.is_connected,
            },
            "write_char": WRITE_CHAR,
            "notify_chars": NOTIFY_CHARS,
            "writes": writes,
            "notifications": events,
        }


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--address", default=None)
    parser.add_argument("--filter", default="x3")
    parser.add_argument("--timeout", type=float, default=8.0)
    parser.add_argument("--delay", type=float, default=0.5)
    parser.add_argument("--out", default=None)
    args = parser.parse_args()

    name = None
    address = args.address
    if not address:
        address, name = await resolve_address(args.filter, args.timeout)

    payload = await probe(address, name, args.delay)
    text = json.dumps(payload, indent=2, ensure_ascii=False)
    print(text)

    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text + "\n", encoding="utf-8")


if __name__ == "__main__":
    asyncio.run(main())

