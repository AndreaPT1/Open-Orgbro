#!/usr/bin/env python3
"""Minimal PrintMaster-like raster experiment for ORGBRO X3.

This can print a tiny black stripe. It is intended to confirm whether paper
movement only happens during a lattice/bitmap print session.
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
CMD_DRAW_BITMAP = 0xA2
CMD_GET_DEV_STATE = 0xA3
CMD_SET_QUALITY = 0xA4
CMD_LATTICE = 0xA6
CMD_GET_DEV_INFO = 0xA8
CMD_SET_ENERGY = 0xAF
CMD_FEED_SPEED = 0xBD
CMD_DRAWING_MODE = 0xBE
CMD_PRINT_MODE = 0xBB

PRINT_LATTICE = bytes([0xAA, 0x55, 0x17, 0x38, 0x44, 0x5F, 0x5F, 0x5F, 0x44, 0x38, 0x2C])
FINISH_LATTICE = bytes([0xAA, 0x55, 0x17, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x17])


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


def frame(command: int, data: bytes = b"", *, prefix_12: bool = False) -> bytes:
    length = len(data)
    packet = bytearray([0x51, 0x78, command, 0x00, length & 0xFF, (length >> 8) & 0xFF])
    packet.extend(data)
    packet.append(crc8(data) if data else 0x00)
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


def stripe_row(bytes_per_row: int, pattern: str) -> bytes:
    if pattern == "full":
        return bytes([0xFF] * bytes_per_row)
    if pattern == "center":
        row = bytearray([0x00] * bytes_per_row)
        center = bytes_per_row // 2
        for i in range(max(0, center - 2), min(bytes_per_row, center + 2)):
            row[i] = 0xFF
        return bytes(row)
    if pattern == "edges":
        row = bytearray([0x00] * bytes_per_row)
        row[0] = 0xFF
        row[-1] = 0xFF
        return bytes(row)
    return bytes([0x00] * bytes_per_row)


def build_sequence(width: int, rows: int, pattern: str, feed_steps: int, prefix_12: bool) -> list[tuple[str, bytes]]:
    bytes_per_row = (width + 7) // 8
    row = stripe_row(bytes_per_row, pattern)
    sequence: list[tuple[str, bytes]] = [
        ("wake_get_info_a8", frame(CMD_GET_DEV_INFO, b"\x00", prefix_12=prefix_12)),
        ("get_state_a3", frame(CMD_GET_DEV_STATE, b"\x00", prefix_12=prefix_12)),
        ("print_mode_bb", frame(CMD_PRINT_MODE, bytes([0x01, 0x07]), prefix_12=prefix_12)),
        ("quality_a4", frame(CMD_SET_QUALITY, bytes([0x33]), prefix_12=prefix_12)),
        ("energy_af_9000", frame(CMD_SET_ENERGY, bytes([0x28, 0x23]), prefix_12=prefix_12)),
        ("drawing_mode_be_image", frame(CMD_DRAWING_MODE, b"\x00", prefix_12=prefix_12)),
        ("feed_speed_bd", frame(CMD_FEED_SPEED, bytes([0x19]), prefix_12=prefix_12)),
        ("start_lattice_a6", frame(CMD_LATTICE, PRINT_LATTICE, prefix_12=prefix_12)),
    ]
    for idx in range(rows):
        sequence.append((f"bitmap_row_{idx:03d}", frame(CMD_DRAW_BITMAP, row, prefix_12=prefix_12)))
    sequence.append(("finish_lattice_a6", frame(CMD_LATTICE, FINISH_LATTICE, prefix_12=prefix_12)))
    if feed_steps > 0:
        sequence.append(("feed_after_print_a1", frame(CMD_FEED_PAPER, bytes([feed_steps & 0xFF, (feed_steps >> 8) & 0xFF]), prefix_12=prefix_12)))
    return sequence


async def run(address: str, name: str | None, width: int, rows: int, pattern: str, feed_steps: int, prefix_12: bool, delay: float, wait_after: float) -> dict[str, Any]:
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

    sequence = build_sequence(width, rows, pattern, feed_steps, prefix_12)

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
                "bytes": len(payload),
                "hex_prefix": payload[:32].hex(),
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
            "width": width,
            "rows": rows,
            "pattern": pattern,
            "feed_steps": feed_steps,
            "prefix_12": prefix_12,
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
    parser.add_argument("--width", type=int, default=384)
    parser.add_argument("--rows", type=int, default=8)
    parser.add_argument("--pattern", choices=["blank", "center", "edges", "full"], default="center")
    parser.add_argument("--feed-steps", type=int, default=0)
    parser.add_argument("--prefix-12", action="store_true")
    parser.add_argument("--delay", type=float, default=0.04)
    parser.add_argument("--wait-after", type=float, default=4.0)
    parser.add_argument("--out", default=None)
    args = parser.parse_args()

    name = None
    address = args.address
    if not address:
        address, name = await resolve_address(args.filter, args.timeout)

    payload = await run(
        address=address,
        name=name,
        width=args.width,
        rows=args.rows,
        pattern=args.pattern,
        feed_steps=args.feed_steps,
        prefix_12=args.prefix_12,
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

