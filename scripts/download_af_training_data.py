#!/usr/bin/env python3
# 中文说明：
#   从 PhysioNet 下载 AF 训练/验证需要的最小公开数据文件。
#   这里只下载头文件和注释文件（如 .hea/.atr/.qrs/.ecg），不下载大体积 ECG 原始波形 .dat。
#   这些注释文件提供 RR/心搏时间和节律标签，用来训练或验证 PPG 可移植的 PPI 间期模型。
#   数据会放到 training_dataset/physionet/ 对应数据库目录下。
"""Download minimal public RR/AF training files from PhysioNet.

This downloader intentionally fetches headers and annotation files only. The
first AF screening model uses beat timing and rhythm labels, so raw ECG .dat
waveforms are not needed for training and would waste hundreds of MB.
"""

from __future__ import annotations

import argparse
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path


DATASETS = {
    "afdb": {
        "base_url": "https://physionet.org/files/afdb/1.0.0",
        "version": "1.0.0",
        "extensions": [".hea", ".atr", ".qrs"],
    },
    "ltafdb": {
        "base_url": "https://physionet.org/files/ltafdb/1.0.0",
        "version": "1.0.0",
        "extensions": [".hea", ".atr"],
    },
    "nsr2db": {
        "base_url": "https://physionet.org/files/nsr2db/1.0.0",
        "version": "1.0.0",
        "extensions": [".hea", ".ecg"],
    },
    "mitdb": {
        "base_url": "https://physionet.org/files/mitdb/1.0.0",
        "version": "1.0.0",
        "extensions": [".hea", ".atr"],
    },
    "nsrdb": {
        "base_url": "https://physionet.org/files/nsrdb/1.0.0",
        "version": "1.0.0",
        "extensions": [".hea", ".atr"],
    },
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--base-dir",
        type=Path,
        default=Path("training_dataset"),
        help="Output directory, default: training_dataset",
    )
    parser.add_argument(
        "--datasets",
        nargs="+",
        choices=sorted(DATASETS),
        default=["afdb", "nsr2db"],
        help="Datasets to download",
    )
    parser.add_argument("--force", action="store_true", help="Re-download existing files")
    parser.add_argument("--retries", type=int, default=3, help="Download retries per file")
    return parser.parse_args()


def fetch(url: str, target: Path, force: bool, retries: int) -> None:
    if target.exists() and target.stat().st_size > 0 and not force:
        return

    target.parent.mkdir(parents=True, exist_ok=True)
    request = urllib.request.Request(url, headers={"User-Agent": "STM32-F411-AF-training/1.0"})
    last_error: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            with urllib.request.urlopen(request, timeout=45) as response:
                data = response.read()
            target.write_bytes(data)
            return
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            last_error = exc
            if attempt < retries:
                time.sleep(1.5 * attempt)
    raise RuntimeError(f"failed to download {url}: {last_error}")


def read_records(records_path: Path) -> list[str]:
    text = records_path.read_text(encoding="utf-8").strip()
    return [item.strip() for item in text.split() if item.strip()]


def download_dataset(name: str, base_dir: Path, force: bool, retries: int) -> None:
    cfg = DATASETS[name]
    dataset_dir = base_dir / "physionet" / name / cfg["version"]
    base_url = cfg["base_url"].rstrip("/")

    print(f"[{name}] downloading RECORDS")
    records_path = dataset_dir / "RECORDS"
    fetch(f"{base_url}/RECORDS", records_path, force=force, retries=retries)
    records = read_records(records_path)
    if not records:
        raise RuntimeError(f"{records_path} contains no records")

    total = len(records) * len(cfg["extensions"])
    done = 0
    for record in records:
        for ext in cfg["extensions"]:
            done += 1
            target = dataset_dir / f"{record}{ext}"
            fetch(f"{base_url}/{record}{ext}", target, force=force, retries=retries)
            if done % 20 == 0 or done == total:
                print(f"[{name}] {done}/{total} files")

    print(f"[{name}] records={len(records)} files={total} dir={dataset_dir}")


def main() -> int:
    args = parse_args()
    try:
        for dataset in args.datasets:
            download_dataset(dataset, args.base_dir, args.force, args.retries)
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
