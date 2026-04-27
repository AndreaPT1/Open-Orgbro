#!/usr/bin/env python3
"""Safely feed paper on ORGBRO X3 with the confirmed YK/Q2 command.

This sends:
- token/init command 0x80 with payload 01 by default
- feed command 0x02 with a 16-bit little-endian step count
"""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from q2_frame_probe import probe, resolve_address


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--address", default=None)
    parser.add_argument("--filter", default="x3")
    parser.add_argument("--timeout", type=float, default=8.0)
    parser.add_argument("--steps", type=int, default=24)
    parser.add_argument("--no-token", action="store_true")
    parser.add_argument("--seq-start", type=int, default=1)
    parser.add_argument("--initial-delay", type=float, default=0.5)
    parser.add_argument("--delay", type=float, default=0.6)
    parser.add_argument("--wait-after", type=float, default=3.0)
    parser.add_argument("--response", action="store_true")
    parser.add_argument("--out", default=None)
    args = parser.parse_args()

    if not 1 <= args.steps <= 0xFFFF:
        raise SystemExit("--steps must be between 1 and 65535")

    args.sequence = f"feed:{args.steps}" if args.no_token else f"token,feed:{args.steps}"

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
