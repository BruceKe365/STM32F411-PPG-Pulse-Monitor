#!/usr/bin/env python3
# 中文说明：
#   下载“压力/情绪趋势”第一版使用的公开数据集 WESAD。
#   WESAD 官方 zip 约 2.5 GB，包含 wrist BVP 和 chest ECG/EDA/Resp 等多模态数据。
#   本项目第一版只使用 wrist BVP 提取 PPI/HRV 特征，训练端侧可移植的压力指数模型。
"""Download the WESAD dataset for PPG-HRV stress-index training.

The official dataset page linked by UCI points to a University of Siegen
Sciebo public share. This helper downloads that zip into
``training_dataset/wesad`` and extracts it when requested.
"""

from __future__ import annotations

import argparse
import shutil
import sys
import time
import urllib.error
import urllib.request
import zlib
import zipfile
from pathlib import Path


DEFAULT_WESAD_URL = "https://uni-siegen.sciebo.de/s/HGdUkoNlW1Ub0Gx/download"
MIN_REASONABLE_WESAD_ZIP_BYTES = 500 * 1024 * 1024


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-dir", type=Path, default=Path("training_dataset"))
    parser.add_argument("--url", default=DEFAULT_WESAD_URL, help="Official WESAD zip download URL")
    parser.add_argument("--force", action="store_true", help="Re-download even when WESAD.zip exists")
    parser.add_argument("--no-extract", action="store_true", help="Only download the zip")
    parser.add_argument("--timeout", type=int, default=60, help="Network timeout in seconds")
    parser.add_argument("--retries", type=int, default=3, help="Retry count")
    return parser.parse_args()


def format_bytes(value: int) -> str:
    units = ["B", "KB", "MB", "GB", "TB"]
    size = float(value)
    for unit in units:
        if size < 1024.0 or unit == units[-1]:
            return f"{size:.1f}{unit}"
        size /= 1024.0
    return f"{value}B"


def should_skip_download(path: Path, force: bool) -> bool:
    if force or not path.exists():
        return False
    size = path.stat().st_size
    if size < MIN_REASONABLE_WESAD_ZIP_BYTES:
        part_path = path.with_suffix(path.suffix + ".part")
        if not part_path.exists() or part_path.stat().st_size < size:
            path.replace(part_path)
            print(f"existing {path} is only {format_bytes(size)}; moved to {part_path} for resume")
        else:
            path.unlink()
            print(f"existing {path} is only {format_bytes(size)}; keeping larger partial file")
        return False
    return True


def download_once(url: str, target: Path, timeout: int) -> None:
    part_path = target.with_suffix(target.suffix + ".part")
    headers = {"User-Agent": "STM32-F411-Stress-HRV/1.0"}
    resume_from = part_path.stat().st_size if part_path.exists() else 0
    mode = "ab" if resume_from > 0 else "wb"

    request = urllib.request.Request(url, headers=headers)
    if resume_from > 0:
        request.add_header("Range", f"bytes={resume_from}-")

    with urllib.request.urlopen(request, timeout=timeout) as response:
        status = getattr(response, "status", None)
        if resume_from > 0 and status == 200:
            # Server ignored Range; start over instead of corrupting the file.
            resume_from = 0
            mode = "wb"
        total_header = response.headers.get("Content-Length")
        total_size = int(total_header) + resume_from if total_header and total_header.isdigit() else 0
        copied = resume_from
        last_print = time.monotonic()

        target.parent.mkdir(parents=True, exist_ok=True)
        with part_path.open(mode + "") as f:
            while True:
                chunk = response.read(1024 * 1024)
                if not chunk:
                    break
                f.write(chunk)
                copied += len(chunk)
                now = time.monotonic()
                if now - last_print >= 5.0:
                    if total_size > 0:
                        pct = 100.0 * copied / total_size
                        print(f"downloaded {format_bytes(copied)} / {format_bytes(total_size)} ({pct:.1f}%)")
                    else:
                        print(f"downloaded {format_bytes(copied)}")
                    last_print = now

    if total_size > 0 and copied < total_size:
        raise RuntimeError(f"incomplete download: {format_bytes(copied)} / {format_bytes(total_size)}")

    part_path.replace(target)


def download_with_retries(url: str, target: Path, timeout: int, retries: int, force: bool) -> None:
    if should_skip_download(target, force):
        print(f"using existing {target} ({format_bytes(target.stat().st_size)})")
        return

    if force:
        for path in (target, target.with_suffix(target.suffix + ".part")):
            if path.exists():
                path.unlink()

    last_error: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            print(f"downloading WESAD attempt {attempt}/{retries}: {url}")
            download_once(url, target, timeout)
            size = target.stat().st_size
            if size < MIN_REASONABLE_WESAD_ZIP_BYTES:
                raise RuntimeError(f"downloaded file is unexpectedly small: {format_bytes(size)}")
            print(f"download complete: {target} ({format_bytes(size)})")
            return
        except (urllib.error.URLError, TimeoutError, OSError, RuntimeError) as exc:
            last_error = exc
            print(f"WARN: download attempt failed: {exc}", file=sys.stderr)
            if attempt < retries:
                time.sleep(2.0 * attempt)
    raise RuntimeError(f"failed to download WESAD: {last_error}")


def extract_zip(zip_path: Path, out_dir: Path) -> None:
    expected = out_dir / "WESAD" / "S2" / "S2.pkl"
    if expected.exists():
        print(f"using existing extracted WESAD tree: {out_dir / 'WESAD'}")
        return

    print(f"extracting {zip_path} to {out_dir}")
    out_dir.mkdir(parents=True, exist_ok=True)
    try:
        with zipfile.ZipFile(zip_path) as zf:
            bad = zf.testzip()
            if bad is not None:
                print(f"WARN: zip integrity check failed at {bad}; extracting core .pkl files only")
                extract_core_pickles(zf, out_dir)
                return
            zf.extractall(out_dir)
    except zipfile.BadZipFile as exc:
        raise RuntimeError(f"bad zip file: {zip_path}") from exc
    except zlib.error:
        print("WARN: zip stream decompression failed; extracting core .pkl files only")
        with zipfile.ZipFile(zip_path) as zf:
            extract_core_pickles(zf, out_dir)
    print("extract complete")


def extract_core_pickles(zf: zipfile.ZipFile, out_dir: Path) -> None:
    ok = 0
    failed: list[str] = []
    for info in zf.infolist():
        if not info.filename.endswith(".pkl"):
            continue
        target = out_dir / info.filename
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            with zf.open(info) as src, target.open("wb") as dst:
                shutil.copyfileobj(src, dst, length=1024 * 1024)
            if target.stat().st_size != info.file_size:
                raise RuntimeError(f"size mismatch {target.stat().st_size} != {info.file_size}")
            ok += 1
        except Exception as exc:
            failed.append(f"{info.filename}: {exc}")
            if target.exists():
                target.unlink()
    if ok == 0:
        details = "; ".join(failed[:3])
        raise RuntimeError(f"failed to extract any WESAD pickle files: {details}")
    print(f"extracted {ok} WESAD pickle files")
    if failed:
        print("WARN: failed pickle entries:")
        for item in failed:
            print(f"  {item}")


def main() -> int:
    args = parse_args()
    wesad_dir = args.base_dir / "wesad"
    zip_path = wesad_dir / "WESAD.zip"

    try:
        download_with_retries(args.url, zip_path, args.timeout, args.retries, args.force)
        if not args.no_extract:
            extract_zip(zip_path, wesad_dir)
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    print("")
    print(f"wrote {zip_path}")
    if not args.no_extract:
        print(f"extracted under {wesad_dir}")
    print("next: python scripts/train_stress_hrv_model.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
