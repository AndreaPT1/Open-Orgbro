#!/usr/bin/env python3
"""Probe ORGBRO X3 with the YKPrinterKit/YZW Q2-style frame format.

Static analysis of Snap & Tag shows YKInstructTool generates BLE frames:

    64 <cmd> <seq> <len_lo> <len_hi> <payload...> 00 00 00 00 9b

Known codeMethod-0 commands:
- 0x80 payload 01: get/init BLE token bytes
- 0x10 no payload: printer status
- 0x11 no payload: firmware version
- 0x09 one byte: density
- 0x0a one byte: speed
- 0x02 two little-endian bytes: feed paper by step count
- 0x50 payload a1: observed codeMethod-1 start/setup wrapper
- 0x51 no payload: taskEnd/flush in the observed codeMethod-1 path
- 0x52 payload 00: observed codeMethod-1 end/aux wrapper
- 0x12 no payload: alternate taskEnd-like wrapper in another path

Sequence specs accept named commands, raw specs like ``0x02:1800``, and
dynamic feed specs like ``feed:24``.
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

COMMANDS: dict[str, tuple[int, bytes]] = {
    "token": (0x80, b"\x01"),
    "status": (0x10, b""),
    "firmware": (0x11, b""),
    "density_8": (0x09, b"\x08"),
    "speed_1": (0x0A, b"\x01"),
    "feed_24": (0x02, (24).to_bytes(2, "little")),
    "start_50_a1": (0x50, b"\xA1"),
    "task_end_51": (0x51, b""),
    "end_52_00": (0x52, b"\x00"),
    "flush_12": (0x12, b""),
}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def yk_frame(command: int, payload: bytes = b"", seq: int = 1) -> bytes:
    length = len(payload)
    return bytes(
        [
            0x64,
            command & 0xFF,
            seq & 0x3F,
            length & 0xFF,
            (length >> 8) & 0xFF,
        ]
    ) + payload + b"\x00\x00\x00\x00\x9b"


def parse_yk_frame(data: bytes) -> dict[str, Any] | None:
    if len(data) < 10 or data[0] != 0x64 or data[-1] != 0x9B:
        return None
    length = data[3] | (data[4] << 8)
    expected = 10 + length
    payload = data[5 : 5 + length]
    return {
        "command": data[1],
        "seq": data[2],
        "length": length,
        "payload_hex": payload.hex(),
        "payload_bytes": list(payload),
        "expected_total_length": expected,
        "length_matches": expected == len(data),
        "tail_hex": data[5 + length :].hex(),
    }


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


def parse_command_spec(spec: str) -> tuple[str, int, bytes]:
    if spec in COMMANDS:
        command, payload = COMMANDS[spec]
        return spec, command, payload
    if spec.startswith("feed:"):
        steps_text = spec.split(":", 1)[1]
        steps = int(steps_text, 0)
        if not 1 <= steps <= 0xFFFF:
            raise ValueError("feed steps must be between 1 and 65535")
        return f"feed_{steps}", 0x02, steps.to_bytes(2, "little")
    if ":" in spec:
        cmd_text, payload_hex = spec.split(":", 1)
        command = int(cmd_text, 0)
        payload = bytes.fromhex(payload_hex)
        return f"custom_{command:02x}", command, payload
    command = int(spec, 0)
    return f"custom_{command:02x}", command, b""


async def probe(address: str, name: str | None, args: argparse.Namespace) -> dict[str, Any]:
    notifications: list[dict[str, Any]] = []
    writes: list[dict[str, Any]] = []

    def on_notify(sender: Any, data: bytearray) -> None:
        raw = bytes(data)
        event: dict[str, Any] = {
            "timestamp": now_iso(),
            "sender": sender_id(sender),
            "hex": raw.hex(),
            "bytes": list(raw),
        }
        parsed = parse_yk_frame(raw)
        if parsed is not None:
            event["yk_frame"] = parsed
        notifications.append(event)

    async with BleakClient(address) as client:
        for char_uuid in NOTIFY_CHARS:
            try:
                await client.start_notify(char_uuid, on_notify)
                notifications.append({"timestamp": now_iso(), "subscribed": char_uuid})
            except Exception as exc:
                notifications.append({"timestamp": now_iso(), "subscribe_failed": char_uuid, "error": repr(exc)})

        await asyncio.sleep(args.initial_delay)

        seq = args.seq_start
        for spec in args.sequence.split(","):
            label, command, payload = parse_command_spec(spec.strip())
            frame = yk_frame(command, payload, seq)
            event: dict[str, Any] = {
                "timestamp": now_iso(),
                "label": label,
                "command": command,
                "seq": seq & 0x3F,
                "payload_hex": payload.hex(),
                "hex": frame.hex(),
                "response": args.response,
            }
            try:
                await client.write_gatt_char(WRITE_CHAR, frame, response=args.response)
                event["result"] = "ack"
            except Exception as exc:
                event["result"] = "write_failed"
                event["error"] = repr(exc)
            writes.append(event)
            seq += 1
            await asyncio.sleep(args.delay)

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
        "write_char": WRITE_CHAR,
        "notify_chars": NOTIFY_CHARS,
        "sequence": args.sequence,
        "writes": writes,
        "notifications": notifications,
    }


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--address", default=None)
    parser.add_argument("--filter", default="x3")
    parser.add_argument("--timeout", type=float, default=8.0)
    parser.add_argument("--sequence", default="token,status,firmware")
    parser.add_argument("--seq-start", type=int, default=1)
    parser.add_argument("--initial-delay", type=float, default=0.5)
    parser.add_argument("--delay", type=float, default=0.6)
    parser.add_argument("--wait-after", type=float, default=3.0)
    parser.add_argument("--response", action="store_true")
    parser.add_argument("--out", default=None)
    args = parser.parse_args()

    name = None
    address = args.address
    if not address:
        address, name = await resolve_address(args.filter, args.timeout)

    payload = await probe(address, name, args)
    text = json.dumps(payload, indent=2, ensure_ascii=False)
    print(text)

    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text + "\n", encoding="utf-8")


if __name__ == "__main__":
    asyncio.run(main())
