#!/usr/bin/env python3
"""PrintMaster-like setup sequence followed by paper feed for ORGBRO X3."""

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
CMD_GET_DEV_STATE = 0xA3
CMD_SET_QUALITY = 0xA4
CMD_GET_DEV_INFO = 0xA8
CMD_SET_ENERGY = 0xAF
CMD_FEED_SPEED = 0xBD
CMD_DRAWING_MODE = 0xBE
CMD_PRINT_MODE = 0xBB


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


def checksum_sum(data: bytes) -> int:
    return sum(data) & 0xFF


def frame(command: int, data: bytes = b"", *, prefix_12: bool = False, checksum: str = "crc8") -> bytes:
    length = len(data)
    packet = bytearray([0x51, 0x78, command, 0x00, length & 0xFF, (length >> 8) & 0xFF])
    packet.extend(data)
    packet.append(crc8(data) if checksum == "crc8" else checksum_sum(data))
    packet.append(0xFF)
    if prefix_12:
        return bytes([0x12]) + bytes(packet)
    return bytes(packet)


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


def build_sequence(steps: int, *, prefix_12: bool, checksum: str, feed_bytes: int) -> list[tuple[str, bytes]]:
    feed_payload = bytes([steps & 0xFF]) if feed_bytes == 1 else bytes([steps & 0xFF, (steps >> 8) & 0xFF])
    return [
        ("wake_get_info_a8", frame(CMD_GET_DEV_INFO, b"\x00", prefix_12=prefix_12, checksum=checksum)),
        ("get_state_a3", frame(CMD_GET_DEV_STATE, b"\x00", prefix_12=prefix_12, checksum=checksum)),
        ("print_mode_bb", frame(CMD_PRINT_MODE, bytes([0x01, 0x07]), prefix_12=prefix_12, checksum=checksum)),
        ("quality_a4", frame(CMD_SET_QUALITY, bytes([0x33]), prefix_12=prefix_12, checksum=checksum)),
        ("energy_af_12000", frame(CMD_SET_ENERGY, bytes([0xE0, 0x2E]), prefix_12=prefix_12, checksum=checksum)),
        ("drawing_mode_be_image", frame(CMD_DRAWING_MODE, b"\x00", prefix_12=prefix_12, checksum=checksum)),
        ("feed_speed_bd", frame(CMD_FEED_SPEED, bytes([0x19]), prefix_12=prefix_12, checksum=checksum)),
        ("feed_a1", frame(CMD_FEED_PAPER, feed_payload, prefix_12=prefix_12, checksum=checksum)),
    ]


async def run(address: str, name: str | None, steps: int, prefix_12: bool, checksum: str, feed_bytes: int, delay: float, wait_after: float) -> dict[str, Any]:
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

    sequence = build_sequence(steps, prefix_12=prefix_12, checksum=checksum, feed_bytes=feed_bytes)

    async with BleakClient(address) as client:
        for char_uuid in NOTIFY_CHARS:
            try:
                await client.start_notify(char_uuid, on_notify)
                events.append({"timestamp": now_iso(), "subscribed": char_uuid})
            except Exception as exc:
                events.append({"timestamp": now_iso(), "subscribe_failed": char_uuid, "error": repr(exc)})

        await asyncio.sleep(0.5)

        for label, payload in sequence:
            write_event = {
                "timestamp": now_iso(),
                "label": label,
                "hex": payload.hex(),
                "response": False,
            }
            try:
                await client.write_gatt_char(WRITE_CHAR, payload, response=False)
                write_event["result"] = "sent"
            except Exception as exc:
                write_event["result"] = "failed"
                write_event["error"] = repr(exc)
            writes.append(write_event)
            await asyncio.sleep(delay)

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
        "settings": {
            "steps": steps,
            "prefix_12": prefix_12,
            "checksum": checksum,
            "feed_bytes": feed_bytes,
            "delay": delay,
        },
        "writes": writes,
        "notifications": events,
    }


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--address", default=None)
    parser.add_argument("--filter", default="x3")
    parser.add_argument("--timeout", type=float, default=8.0)
    parser.add_argument("--steps", type=int, default=800)
    parser.add_argument("--prefix-12", action="store_true")
    parser.add_argument("--checksum", choices=["crc8", "sum"], default="crc8")
    parser.add_argument("--feed-bytes", choices=[1, 2], type=int, default=2)
    parser.add_argument("--delay", type=float, default=0.12)
    parser.add_argument("--wait-after", type=float, default=3.0)
    parser.add_argument("--out", default=None)
    args = parser.parse_args()

    name = None
    address = args.address
    if not address:
        address, name = await resolve_address(args.filter, args.timeout)

    payload = await run(
        address=address,
        name=name,
        steps=args.steps,
        prefix_12=args.prefix_12,
        checksum=args.checksum,
        feed_bytes=args.feed_bytes,
        delay=args.delay,
        wait_after=args.wait_after,
    )
    text = json.dumps(payload, indent=2, ensure_ascii=False)
    print(text)

    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text + "\n", encoding="utf-8")


if __name__ == "__main__":
    asyncio.run(main())

