#!/usr/bin/env python3
"""Extract and optionally replay a PacketLogger capture against ORGBRO X3.

This script works with the `.pklg` file saved by Apple's PacketLogger. It
extracts the raw writes sent to the printer's write characteristic (`ff02`) and
can replay the longest contiguous print-job cluster exactly as captured.

The replay path intentionally uses the raw write chunks seen in PacketLogger,
not reconstructed whole YK frames, because the macOS app split large print data
across multiple BLE writes. That makes this the closest thing to "play it back
exactly" without the original app.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from bleak import BleakClient

from q2_frame_probe import NOTIFY_CHARS, WRITE_CHAR, parse_yk_frame, resolve_address, sender_id


TYPE_SEND = 0x02
TYPE_RECV = 0x03
ATT_WRITE_COMMAND = 0x52
ATT_HANDLE_VALUE_NOTIFICATION = 0x1B
READY_NOTIFY = b"\x01\x05"


@dataclass
class PacketLoggerRecord:
    index: int
    timestamp_raw: int
    record_type: int
    payload: bytes


@dataclass
class RawWrite:
    index: int
    timestamp_raw: int
    value: bytes


def iter_pklg_records(blob: bytes) -> list[PacketLoggerRecord]:
    records: list[PacketLoggerRecord] = []
    offset = 0
    index = 0
    while offset + 4 <= len(blob):
        body_len = int.from_bytes(blob[offset : offset + 4], "little")
        end = offset + 4 + body_len
        if body_len <= 0 or end > len(blob):
            raise ValueError(f"invalid record at offset {offset}: body_len={body_len}")
        body = blob[offset + 4 : end]
        if len(body) < 9:
            raise ValueError(f"short record at offset {offset}: body_len={body_len}")
        timestamp_raw = int.from_bytes(body[:8], "little")
        record_type = body[8]
        payload = body[9:]
        records.append(PacketLoggerRecord(index, timestamp_raw, record_type, payload))
        offset = end
        index += 1
    return records


def extract_raw_writes(records: list[PacketLoggerRecord]) -> list[RawWrite]:
    writes: list[RawWrite] = []
    for record in records:
        payload = record.payload
        if record.record_type != TYPE_SEND or len(payload) < 11:
            continue
        opcode = payload[8]
        handle_le = payload[9:11]
        if opcode != ATT_WRITE_COMMAND or handle_le != b"\x0d\x00":
            continue
        writes.append(RawWrite(record.index, record.timestamp_raw, payload[11:]))
    return writes


def cluster_writes(writes: list[RawWrite], max_index_gap: int = 10) -> list[list[RawWrite]]:
    if not writes:
        return []
    clusters: list[list[RawWrite]] = [[writes[0]]]
    for write in writes[1:]:
        prev = clusters[-1][-1]
        if write.index - prev.index <= max_index_gap:
            clusters[-1].append(write)
        else:
            clusters.append([write])
    return clusters


def choose_job_cluster(writes: list[RawWrite]) -> list[RawWrite]:
    clusters = cluster_writes(writes)
    if not clusters:
        return []
    return max(clusters, key=len)


def split_groups_by_timestamp(writes: list[RawWrite]) -> list[list[RawWrite]]:
    if not writes:
        return []
    groups: list[list[RawWrite]] = [[writes[0]]]
    for write in writes[1:]:
        if write.timestamp_raw == groups[-1][-1].timestamp_raw:
            groups[-1].append(write)
        else:
            groups.append([write])
    return groups


def parse_assembled_frames(writes: list[RawWrite]) -> list[dict[str, Any]]:
    stream = b"".join(write.value for write in writes)
    frames: list[dict[str, Any]] = []
    offset = 0
    while offset < len(stream):
        if stream[offset] != 0x64:
            offset += 1
            continue
        if offset + 10 > len(stream):
            break
        length = stream[offset + 3] | (stream[offset + 4] << 8)
        end = offset + 5 + length + 4
        if end >= len(stream) or stream[end] != 0x9B:
            offset += 1
            continue
        raw = stream[offset : end + 1]
        parsed = parse_yk_frame(raw)
        if parsed is None:
            offset += 1
            continue
        payload = bytes.fromhex(parsed["payload_hex"])
        frames.append(
            {
                "offset": offset,
                "command": parsed["command"],
                "seq": parsed["seq"],
                "length": parsed["length"],
                "payload_hex": parsed["payload_hex"],
                "nonzero_bytes": sum(1 for b in payload if b),
            }
        )
        offset = end + 1
    return frames


def summarize_capture(path: Path) -> dict[str, Any]:
    records = iter_pklg_records(path.read_bytes())
    writes = extract_raw_writes(records)
    job = choose_job_cluster(writes)
    frames = parse_assembled_frames(job)
    groups = split_groups_by_timestamp(job)
    return {
        "file": str(path),
        "record_count": len(records),
        "raw_write_count": len(writes),
        "job_write_count": len(job),
        "job_group_count": len(groups),
        "job_first_record_index": job[0].index if job else None,
        "job_last_record_index": job[-1].index if job else None,
        "job_stream_bytes": sum(len(write.value) for write in job),
        "frames": frames,
    }


async def replay_job(
    *,
    writes: list[RawWrite],
    address: str,
    ready_timeout: float,
    group_delay: float,
    wait_for_ready: bool,
    initial_delay: float,
    wait_after: float,
) -> dict[str, Any]:
    notifications: list[dict[str, Any]] = []
    sent: list[dict[str, Any]] = []
    ready_event = asyncio.Event()

    def on_notify(sender: Any, data: bytearray) -> None:
        raw = bytes(data)
        event: dict[str, Any] = {
            "sender": sender_id(sender),
            "hex": raw.hex(),
        }
        parsed = parse_yk_frame(raw)
        if parsed is not None:
            event["yk_frame"] = parsed
        notifications.append(event)
        if raw == READY_NOTIFY:
            ready_event.set()

    groups = split_groups_by_timestamp(writes)

    async with BleakClient(address) as client:
        for char_uuid in NOTIFY_CHARS:
            await client.start_notify(char_uuid, on_notify)

        await asyncio.sleep(initial_delay)

        for group_index, group in enumerate(groups):
            for write in group:
                await client.write_gatt_char(WRITE_CHAR, write.value, response=False)
                sent.append(
                    {
                        "record_index": write.index,
                        "timestamp_raw": write.timestamp_raw,
                        "len": len(write.value),
                        "hex_prefix": write.value[:24].hex(),
                    }
                )

            if group_index == len(groups) - 1:
                continue

            if wait_for_ready:
                ready_event.clear()
                try:
                    await asyncio.wait_for(ready_event.wait(), timeout=ready_timeout)
                except TimeoutError:
                    await asyncio.sleep(group_delay)
            else:
                await asyncio.sleep(group_delay)

        await asyncio.sleep(wait_after)

        for char_uuid in NOTIFY_CHARS:
            try:
                await client.stop_notify(char_uuid)
            except Exception:
                pass

    return {
        "address": address,
        "group_count": len(groups),
        "write_count": len(writes),
        "sent": sent,
        "notifications": notifications,
    }


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("capture", help="Path to the PacketLogger .pklg file")
    parser.add_argument("--address", default=None)
    parser.add_argument("--filter", default="x3")
    parser.add_argument("--timeout", type=float, default=8.0)
    parser.add_argument("--summary-only", action="store_true")
    parser.add_argument("--replay", action="store_true")
    parser.add_argument("--ready-timeout", type=float, default=0.35)
    parser.add_argument("--group-delay", type=float, default=0.08)
    parser.add_argument("--no-wait-ready", action="store_true")
    parser.add_argument("--initial-delay", type=float, default=0.4)
    parser.add_argument("--wait-after", type=float, default=4.0)
    parser.add_argument("--out", default=None)
    args = parser.parse_args()

    capture = Path(args.capture)
    summary = summarize_capture(capture)

    result: dict[str, Any] = {"summary": summary}
    if args.replay:
        writes = choose_job_cluster(extract_raw_writes(iter_pklg_records(capture.read_bytes())))
        if not writes:
            raise SystemExit("no job writes found in capture")
        address = args.address
        if not address:
            address, _ = await resolve_address(args.filter, args.timeout)
        result["replay"] = await replay_job(
            writes=writes,
            address=address,
            ready_timeout=args.ready_timeout,
            group_delay=args.group_delay,
            wait_for_ready=not args.no_wait_ready,
            initial_delay=args.initial_delay,
            wait_after=args.wait_after,
        )
    elif not args.summary_only:
        result["hint"] = "Use --replay to send the extracted job back to the printer."

    text = json.dumps(result, indent=2, ensure_ascii=False)
    print(text)

    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text + "\n", encoding="utf-8")


if __name__ == "__main__":
    asyncio.run(main())
