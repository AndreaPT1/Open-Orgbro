#!/usr/bin/env python3
"""Protocol 0x1F zlib print test for ORGBRO X3.

Implements the PrintMaster BLE payload shape used by print_master_ble:
- config commands: 1F 80 / 1F 70 / 1F 60
- print session: 1F C0 01 00 ... 1F C0 01 01
- image command: 1F 10 widthBytesHi widthBytesLo heightHi heightLo len32 zlibData
- zlib stream uses header 28 15, raw deflate wbits=10, and Adler-32 footer.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import zlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from bleak import BleakClient, BleakScanner


WRITE_CHAR = "0000ff02-0000-1000-8000-00805f9b34fb"
FLOW_CHAR = "0000ff03-0000-1000-8000-00805f9b34fb"
NOTIFY_CHARS = [
    "0000ff01-0000-1000-8000-00805f9b34fb",
    FLOW_CHAR,
]

CHUNK_SIZE = 200


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


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


def adler32(data: bytes) -> int:
    return zlib.adler32(data) & 0xFFFFFFFF


def zlib_1kb_level0(raw: bytes) -> bytes:
    compressor = zlib.compressobj(level=0, method=zlib.DEFLATED, wbits=-10)
    deflate = compressor.compress(raw) + compressor.flush()
    checksum = adler32(raw)
    return bytes([0x28, 0x15]) + deflate + checksum.to_bytes(4, "big")


def build_bitmap(width: int, height: int, pattern: str) -> bytes:
    bytes_per_row = (width + 7) // 8
    rows = []
    for y in range(height):
        row = bytearray([0x00] * bytes_per_row)
        if pattern == "full":
            row[:] = bytes([0xFF] * bytes_per_row)
        elif pattern == "center":
            center = bytes_per_row // 2
            for i in range(max(0, center - 3), min(bytes_per_row, center + 3)):
                row[i] = 0xFF
        elif pattern == "bars":
            if y % 8 < 4:
                row[:] = bytes([0xFF] * bytes_per_row)
        elif pattern == "frame":
            if y in (0, height - 1):
                row[:] = bytes([0xFF] * bytes_per_row)
            else:
                row[0] = 0xFF
                row[-1] = 0xFF
        rows.append(bytes(row))
    return b"".join(rows)


def image_command(width: int, height: int, pattern: str) -> tuple[bytes, dict[str, Any]]:
    bytes_per_row = (width + 7) // 8
    bitmap = build_bitmap(width, height, pattern)
    compressed = zlib_1kb_level0(bitmap)
    header = bytes(
        [
            0x1F,
            0x10,
            (bytes_per_row >> 8) & 0xFF,
            bytes_per_row & 0xFF,
            (height >> 8) & 0xFF,
            height & 0xFF,
        ]
    ) + len(compressed).to_bytes(4, "big")
    return header + compressed, {
        "bytes_per_row": bytes_per_row,
        "bitmap_bytes": len(bitmap),
        "compressed_bytes": len(compressed),
        "image_command_bytes": len(header) + len(compressed),
        "zlib_prefix": compressed[:16].hex(),
        "adler32": f"{adler32(bitmap):08x}",
    }


def build_payload(width: int, height: int, pattern: str, paper_type: int, density: int, speed: int, align_start: bool, feed_end: bool) -> tuple[bytes, dict[str, Any]]:
    image, stats = image_command(width, height, pattern)
    parts = [
        ("set_paper_type", bytes([0x1F, 0x80, 0x01, paper_type])),
        ("set_density", bytes([0x1F, 0x70, 0x01, max(1, min(density, 15))])),
        ("set_speed", bytes([0x1F, 0x60, 0x01, max(0, min(speed, 2))])),
        ("start_print", bytes([0x1F, 0xC0, 0x01, 0x00])),
    ]
    if align_start:
        parts.append(("align_start", bytes([0x1F, 0x11, 0x51])))
    parts.append(("image_1f10", image))
    parts.append(("stop_print", bytes([0x1F, 0xC0, 0x01, 0x01])))
    if feed_end:
        parts.append(("feed_end", bytes([0x1F, 0x11, 0x50])))

    payload = b"".join(part for _, part in parts)
    stats["parts"] = [
        {"label": label, "bytes": len(part), "hex_prefix": part[:32].hex()}
        for label, part in parts
    ]
    stats["payload_bytes"] = len(payload)
    return payload, stats


class CreditState:
    def __init__(self) -> None:
        self.credits = 0
        self.events: list[dict[str, Any]] = []

    def on_notify(self, sender: Any, data: bytearray) -> None:
        raw = bytes(data)
        event = {
            "timestamp": now_iso(),
            "sender": sender_id(sender),
            "hex": raw.hex(),
            "bytes": list(raw),
        }
        if sender_id(sender).lower() == FLOW_CHAR and len(raw) >= 2 and raw[0] == 0x01:
            self.credits += raw[1]
            event["credits_added"] = raw[1]
            event["credits_total"] = self.credits
        self.events.append(event)


async def send_with_flow(client: BleakClient, data: bytes, state: CreditState, chunk_size: int, fallback_delay: float, response: bool) -> list[dict[str, Any]]:
    chunks = []
    for offset in range(0, len(data), chunk_size):
        chunk = data[offset:offset + chunk_size]
        waited = 0.0
        while state.credits <= 0 and waited < 2.0:
            await asyncio.sleep(0.05)
            waited += 0.05
        used_fallback = state.credits <= 0
        if not used_fallback:
            state.credits -= 1
        await client.write_gatt_char(WRITE_CHAR, chunk, response=response)
        chunks.append(
            {
                "offset": offset,
                "bytes": len(chunk),
                "hex_prefix": chunk[:16].hex(),
                "response": response,
                "used_fallback": used_fallback,
                "credits_after": state.credits,
            }
        )
        await asyncio.sleep(fallback_delay if used_fallback else 0.01)
    return chunks


async def run(address: str, name: str | None, args: argparse.Namespace) -> dict[str, Any]:
    payload, stats = build_payload(
        width=args.width,
        height=args.height,
        pattern=args.pattern,
        paper_type=int(args.paper_type, 0),
        density=args.density,
        speed=args.speed,
        align_start=not args.no_align_start,
        feed_end=not args.no_feed_end,
    )
    state = CreditState()

    async with BleakClient(address) as client:
        for char_uuid in NOTIFY_CHARS:
            try:
                await client.start_notify(char_uuid, state.on_notify)
                state.events.append({"timestamp": now_iso(), "subscribed": char_uuid})
            except Exception as exc:
                state.events.append({"timestamp": now_iso(), "subscribe_failed": char_uuid, "error": repr(exc)})

        await asyncio.sleep(0.5)
        chunks = await send_with_flow(client, payload, state, CHUNK_SIZE, args.fallback_delay, args.response)
        await asyncio.sleep(args.wait_after)

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
            "width": args.width,
            "height": args.height,
            "pattern": args.pattern,
            "paper_type": args.paper_type,
            "density": args.density,
            "speed": args.speed,
            "align_start": not args.no_align_start,
            "feed_end": not args.no_feed_end,
            "response": args.response,
        },
        "stats": stats,
        "chunks": chunks,
        "notifications": state.events,
    }


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--address", default=None)
    parser.add_argument("--filter", default="x3")
    parser.add_argument("--timeout", type=float, default=8.0)
    parser.add_argument("--width", type=int, default=384)
    parser.add_argument("--height", type=int, default=32)
    parser.add_argument("--pattern", choices=["center", "full", "bars", "frame"], default="bars")
    parser.add_argument("--paper-type", default="0x10", help="0x10 continuous, 0x20 gap labels, 0x30 black mark.")
    parser.add_argument("--density", type=int, default=8)
    parser.add_argument("--speed", type=int, default=1)
    parser.add_argument("--no-align-start", action="store_true")
    parser.add_argument("--no-feed-end", action="store_true")
    parser.add_argument("--response", action="store_true")
    parser.add_argument("--fallback-delay", type=float, default=0.05)
    parser.add_argument("--wait-after", type=float, default=5.0)
    parser.add_argument("--out", default=None)
    args = parser.parse_args()

    name = None
    address = args.address
    if not address:
        address, name = await resolve_address(args.filter, args.timeout)

    payload = await run(address, name, args)
    text = json.dumps(payload, indent=2, ensure_ascii=False)
    print(text)

    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text + "\n", encoding="utf-8")


if __name__ == "__main__":
    asyncio.run(main())

