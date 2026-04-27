#!/usr/bin/env python3
"""Controlled micro-feed test for ORGBRO X3.

Sends one PrintMaster-like feed command (0xA1) with a small step count.
This can move paper, but does not send image/raster data.
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
CMD_FEED_PAPER = 0xA1


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


def feed_frame(steps: int) -> bytes:
    if steps < 0 or steps > 65535:
        raise ValueError("steps must be between 0 and 65535")
    return printmaster_frame(CMD_FEED_PAPER, bytes([steps & 0xFF, (steps >> 8) & 0xFF]))


def _match_device(name: str | None, address: str, needle: str) -> bool:
    haystack = f"{name or ''} {address}".lower()
    return needle.lower() in haystack


async def resolve_address(filter_text: str, timeout: float) -> tuple[str, str | None]:
    devices = await BleakScanner.discover(timeout=timeout)
    for device in devices:
        if _match_device(device.name, device.address, filter_text):
            return device.address, device.name
    seen = [f"{device.name or '<no name>'} {device.address}" for device in devices]
    raise SystemExit(
        f"No BLE device matching {filter_text!r} found.\n"
        "Seen devices:\n- " + "\n- ".join(seen)
    )


def sender_id(sender: Any) -> str:
    return getattr(sender, "uuid", None) or str(sender)


async def run_micro_feed(address: str, name: str | None, steps: int, wait_after: float, with_response: bool) -> dict[str, Any]:
    events: list[dict[str, Any]] = []
    payload = feed_frame(steps)

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

        await asyncio.sleep(0.5)

        write_event = {
            "timestamp": now_iso(),
            "label": "printmaster_feed_micro",
            "steps": steps,
            "hex": payload.hex(),
            "response": with_response,
        }
        try:
            await client.write_gatt_char(WRITE_CHAR, payload, response=with_response)
            write_event["result"] = "sent"
        except Exception as exc:
            write_event["result"] = "failed"
            write_event["error"] = repr(exc)

        await asyncio.sleep(wait_after)

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
        },
        "write_char": WRITE_CHAR,
        "notify_chars": NOTIFY_CHARS,
        "write": write_event,
        "notifications": events,
    }


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--address", default=None)
    parser.add_argument("--filter", default="x3")
    parser.add_argument("--timeout", type=float, default=8.0)
    parser.add_argument("--steps", type=int, default=8)
    parser.add_argument("--wait-after", type=float, default=2.0)
    parser.add_argument("--without-response", action="store_true")
    parser.add_argument("--out", default=None)
    args = parser.parse_args()

    name = None
    address = args.address
    if not address:
        address, name = await resolve_address(args.filter, args.timeout)

    payload = await run_micro_feed(
        address=address,
        name=name,
        steps=args.steps,
        wait_after=args.wait_after,
        with_response=not args.without_response,
    )
    text = json.dumps(payload, indent=2, ensure_ascii=False)
    print(text)

    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text + "\n", encoding="utf-8")


if __name__ == "__main__":
    asyncio.run(main())

