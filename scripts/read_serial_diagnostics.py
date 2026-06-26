#!/usr/bin/env python3
# 中文说明：
#   读取 STM32 诊断串口 CSV，并统计主循环、MAX30102 服务、OLED 刷新、USB CDC 等耗时/卡顿情况。
#   当前固件默认输出的是诊断流，不是原始 red/ir 波形流；确认串口实际输出内容时优先看这个脚本。
#   典型用途：烧录后检查采样是否在跑、I2C/OLED/CDC 是否拖慢循环。
"""Read STM32 diagnostic CSV from a USB CDC COM port and summarize stalls."""

from __future__ import annotations

import argparse
import csv
import sys
import time
from pathlib import Path


DIAG_HEADER = "diag_ms"
DEFAULT_HEADER = [
    "diag_ms",
    "loop_gap_max_ms",
    "loop_count",
    "max30102_calls",
    "max30102_samples",
    "max30102_fifo_max",
    "max30102_max_ms",
    "autocorr_calls",
    "autocorr_done",
    "autocorr_max_ms",
    "autocorr_active",
    "oled091_flushes",
    "oled091_max_ms",
    "oled64_flushes",
    "oled64_max_ms",
    "cdc_calls",
    "cdc_busy",
    "cdc_timeout",
    "cdc_max_wait_ms",
    "cdc_total_wait_ms",
    "irq_count",
    "sample_count",
    "finger",
    "valid",
    "hr",
    "auto",
    "spo2",
]


def positive_int(value: str) -> int:
    parsed = int(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be > 0")
    return parsed


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", default="COM7", help="Serial port, default: COM7")
    parser.add_argument("--baud", type=int, default=115200, help="CDC baud placeholder")
    parser.add_argument("--duration", type=positive_int, default=30, help="Seconds to read")
    parser.add_argument("--timeout", type=float, default=2.0, help="Serial read timeout")
    parser.add_argument("--csv-out", type=Path, default=None, help="Optional CSV output path")
    parser.add_argument("--quiet", action="store_true", help="Do not print live rows")
    return parser.parse_args()


def to_int(row: dict[str, str], key: str) -> int:
    try:
        return int(row.get(key, "0"))
    except ValueError:
        return 0


def max_field(rows: list[dict[str, str]], key: str) -> int:
    return max((to_int(row, key) for row in rows), default=0)


def sum_field(rows: list[dict[str, str]], key: str) -> int:
    return sum(to_int(row, key) for row in rows)


def analyze(rows: list[dict[str, str]]) -> list[str]:
    if not rows:
        return ["No diagnostic rows were parsed."]

    loop_max = max_field(rows, "loop_gap_max_ms")
    oled091_max = max_field(rows, "oled091_max_ms")
    oled64_max = max_field(rows, "oled64_max_ms")
    max30102_max = max_field(rows, "max30102_max_ms")
    autocorr_max = max_field(rows, "autocorr_max_ms")
    autocorr_done = sum_field(rows, "autocorr_done")
    autocorr_active = max_field(rows, "autocorr_active")
    cdc_wait_max = max_field(rows, "cdc_max_wait_ms")
    cdc_busy = sum_field(rows, "cdc_busy")
    cdc_timeout = sum_field(rows, "cdc_timeout")
    fifo_max = max_field(rows, "max30102_fifo_max")

    lines = [
        f"rows={len(rows)} loop_gap_max_ms={loop_max}",
        f"max30102_max_ms={max30102_max} fifo_max={fifo_max}",
        f"autocorr_max_ms={autocorr_max} done={autocorr_done} active={autocorr_active}",
        f"oled091_max_ms={oled091_max} oled64_max_ms={oled64_max}",
        f"cdc_max_wait_ms={cdc_wait_max} cdc_busy={cdc_busy} cdc_timeout={cdc_timeout}",
    ]

    suspects = [
        ("oled64_flush", oled64_max),
        ("oled091_flush", oled091_max),
        ("max30102_service", max30102_max),
        ("autocorr_slice", autocorr_max),
        ("cdc_write_wait", cdc_wait_max),
    ]
    suspects.sort(key=lambda item: item[1], reverse=True)
    lines.append("top_blockers=" + ", ".join(f"{name}:{value}ms" for name, value in suspects))

    if cdc_timeout > 0:
        lines.append("JUDGEMENT: CDC write timeout is severe; serial output is blocking the main loop.")
    elif loop_max >= 100:
        lines.append("JUDGEMENT: loop stalls are severe; inspect the largest top_blocker first.")
    elif oled64_max >= 40:
        lines.append("JUDGEMENT: 0.96 OLED full-screen refresh is a likely source of visible stutter.")
    elif oled091_max >= 20:
        lines.append("JUDGEMENT: 0.91 OLED full-screen refresh is a likely source of visible stutter.")
    elif autocorr_max >= 20:
        lines.append("JUDGEMENT: autocorr slices are still too large; reduce firmware slice budget.")
    elif max30102_max >= 20 or fifo_max >= 6:
        lines.append("JUDGEMENT: MAX30102 service is batching samples; FIFO timing may be causing uneven waveform steps.")
    elif cdc_busy > 0 or cdc_wait_max >= 10:
        lines.append("JUDGEMENT: CDC backpressure is present; serial writes may be adding jitter.")
    else:
        lines.append("JUDGEMENT: no single large blocker found; stutter may be OLED perceptual refresh limits.")

    return lines


def main() -> int:
    args = parse_args()

    try:
        import serial
    except ImportError:
        print("pyserial is required: pip install pyserial", file=sys.stderr)
        return 2

    rows: list[dict[str, str]] = []
    header: list[str] | None = None
    start = time.monotonic()

    try:
        ser = serial.Serial(args.port, args.baud, timeout=args.timeout)
    except serial.SerialException as exc:
        print(f"failed to open {args.port}: {exc}", file=sys.stderr)
        return 1

    with ser:
        ser.reset_input_buffer()
        while (time.monotonic() - start) < args.duration:
            raw = ser.readline()
            if not raw:
                continue
            line = raw.decode("ascii", errors="ignore").strip()
            if not line:
                continue
            parts = [part.strip() for part in line.split(",")]
            if parts[0] == DIAG_HEADER:
                header = parts
                continue
            if header is None and len(parts) == len(DEFAULT_HEADER):
                header = DEFAULT_HEADER
            if header is None or len(parts) != len(header):
                continue
            row = dict(zip(header, parts))
            rows.append(row)
            if not args.quiet:
                print(
                    "t={diag_ms} loop={loop_gap_max_ms}ms "
                    "m30102={max30102_max_ms}ms/{max30102_fifo_max}fifo "
                    "auto={autocorr_max_ms}ms/{autocorr_active} "
                    "o091={oled091_max_ms}ms o64={oled64_max_ms}ms "
                    "cdc={cdc_max_wait_ms}ms busy={cdc_busy} timeout={cdc_timeout}".format(**row)
                )

    if args.csv_out is not None and rows:
        with args.csv_out.open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
            writer.writeheader()
            writer.writerows(rows)

    print("\nSummary")
    for line in analyze(rows):
        print(line)
    return 0 if rows else 1


if __name__ == "__main__":
    raise SystemExit(main())
