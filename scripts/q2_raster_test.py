#!/usr/bin/env python3
"""Print a tiny raster test on ORGBRO X3 with the candidate YK/Q2 command 0x05.

Static analysis shows command 0x05 carries raw 1-bit raster bytes. Bits are
packed MSB-first; set bits are black. The X3 print width observed in Snap & Tag
defaults to 384 dots, i.e. 48 bytes per raster row.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from q2_frame_probe import probe, resolve_address


def set_black_pixel(row: bytearray, x: int) -> None:
    row[x // 8] |= 0x80 >> (x % 8)


def make_row(width_dots: int, pattern: str) -> bytes:
    width_bytes = (width_dots + 7) // 8
    row = bytearray(width_bytes)

    if pattern == "black":
        for i in range(width_bytes):
            row[i] = 0xFF
    elif pattern == "center":
        start = max(0, (width_dots // 2) - 16)
        end = min(width_dots, (width_dots // 2) + 16)
        for x in range(start, end):
            set_black_pixel(row, x)
    elif pattern == "edges":
        for x in range(min(32, width_dots)):
            set_black_pixel(row, x)
        for x in range(max(0, width_dots - 32), width_dots):
            set_black_pixel(row, x)
    else:
        raise ValueError(f"unsupported pattern: {pattern}")

    return bytes(row)


def make_raster(width_dots: int, rows: int, pattern: str) -> bytes:
    if width_dots < 1:
        raise ValueError("width_dots must be positive")
    if rows < 1:
        raise ValueError("rows must be positive")
    return make_row(width_dots, pattern) * rows

def chunks(data: bytes, chunk_size: int) -> list[bytes]:
    if chunk_size < 1:
        raise ValueError("chunk_size must be positive")
    return [data[i : i + chunk_size] for i in range(0, len(data), chunk_size)]


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--address", default=None)
    parser.add_argument("--filter", default="x3")
    parser.add_argument("--timeout", type=float, default=8.0)
    parser.add_argument("--width-dots", type=int, default=384)
    parser.add_argument("--rows", type=int, default=8)
    parser.add_argument("--chunk-rows", type=int, default=4)
    parser.add_argument("--pattern", choices=["black", "center", "edges"], default="center")
    parser.add_argument("--start", choices=["none", "0x50:a1"], default="none")
    parser.add_argument("--task-end", choices=["none", "0x51", "0x12"], default="none")
    parser.add_argument("--end", choices=["none", "0x52:00"], default="none")
    parser.add_argument("--post-feed", type=int, default=24)
    parser.add_argument("--seq-start", type=int, default=1)
    parser.add_argument("--initial-delay", type=float, default=0.5)
    parser.add_argument("--delay", type=float, default=0.6)
    parser.add_argument("--wait-after", type=float, default=4.0)
    parser.add_argument("--response", action="store_true")
    parser.add_argument("--out", default=None)
    args = parser.parse_args()

    raster = make_raster(args.width_dots, args.rows, args.pattern)
    width_bytes = (args.width_dots + 7) // 8
    raster_chunks = chunks(raster, width_bytes * args.chunk_rows)
    sequence_parts = ["token"]
    if args.start != "none":
        sequence_parts.append(args.start)
    sequence_parts.extend(f"0x05:{chunk.hex()}" for chunk in raster_chunks)
    if args.task_end != "none":
        sequence_parts.append(args.task_end)
    if args.end != "none":
        sequence_parts.append(args.end)
    if args.post_feed:
        if not 1 <= args.post_feed <= 0xFFFF:
            raise SystemExit("--post-feed must be between 1 and 65535, or 0 to disable")
        sequence_parts.append(f"feed:{args.post_feed}")
    args.sequence = ",".join(sequence_parts)

    name = None
    address = args.address
    if not address:
        address, name = await resolve_address(args.filter, args.timeout)

    payload = await probe(address, name, args)
    payload["raster_test"] = {
        "command": 0x05,
        "width_dots": args.width_dots,
        "width_bytes": width_bytes,
        "rows": args.rows,
        "chunk_rows": args.chunk_rows,
        "chunk_payload_lengths": [len(chunk) for chunk in raster_chunks],
        "pattern": args.pattern,
        "start": args.start,
        "task_end": args.task_end,
        "end": args.end,
        "payload_len": len(raster),
        "post_feed": args.post_feed,
    }
    text = json.dumps(payload, indent=2, ensure_ascii=False)
    print(text)

    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text + "\n", encoding="utf-8")


if __name__ == "__main__":
    asyncio.run(main())
