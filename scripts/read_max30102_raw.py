#!/usr/bin/env python3
# 中文说明：
#   通过 USB CDC 串口读取 STM32 输出的 MAX30102 原始 CSV 采样数据。
#   主要用于采集本地 PPG 样本，并在电脑端做第一版心率/血氧/波形质量估计。
#   典型用途：接上单片机后采集 raw/processed CSV，给后续 AF 风险验证脚本使用。
#   注意：只有固件打开 RAW_STREAM_USB_ENABLE 时才会持续输出 red/ir 原始采样。
"""
Read raw MAX30102 CSV samples from the STM32 USB CDC port and run a first-pass
PC-side PPG estimator.

Expected device CSV:
    t_ms,red,ir,irq_count,sample_count
    12345,57582,66443,13477,16923
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import math
import statistics
import sys
import time
from collections import deque
from pathlib import Path

try:
    import serial
    from serial.tools import list_ports
except ImportError as exc:
    print("Missing dependency: pyserial. Install with: python -m pip install pyserial", file=sys.stderr)
    raise SystemExit(2) from exc


RAW_CSV_FIELDS = ["session_id", "label", "pc_time", "t_ms", "red", "ir", "irq_count", "sample_count"]
SAMPLE_INTERVAL_MS = 10
PROCESSED_CSV_FIELDS = [
    "session_id",
    "label",
    "pc_time",
    "t_ms",
    "sample_time_ms",
    "sample_count",
    "red",
    "ir",
    "fs_hz",
    "red_dc",
    "ir_dc",
    "red_ac",
    "ir_ac",
    "red_filt",
    "ir_filt",
    "red_rms",
    "ir_rms",
    "finger_score",
    "finger_present",
    "hr_confirmed",
    "hr_bpm",
    "hr_peak_bpm",
    "hr_autocorr_bpm",
    "spo2",
    "ratio",
    "peak",
    "peak_interval_ms",
]
EVENT_CSV_FIELDS = ["session_id", "pc_time", "event_s", "event_label"]


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def draw_line(pixels: list[list[bool]], x0: int, y0: int, x1: int, y1: int) -> None:
    dx = abs(x1 - x0)
    sx = 1 if x0 < x1 else -1
    dy = -abs(y1 - y0)
    sy = 1 if y0 < y1 else -1
    err = dx + dy

    while True:
        if 0 <= y0 < len(pixels) and 0 <= x0 < len(pixels[0]):
            pixels[y0][x0] = True
        if x0 == x1 and y0 == y1:
            break
        e2 = 2 * err
        if e2 >= dy:
            err += dy
            x0 += sx
        if e2 <= dx:
            err += dx
            y0 += sy


def write_oled_bmp(path: Path, pixels: list[list[bool]], scale: int) -> None:
    scale = max(1, scale)
    src_h = len(pixels)
    src_w = len(pixels[0]) if src_h else 0
    width = src_w * scale
    height = src_h * scale
    row_size = ((width * 3 + 3) // 4) * 4
    pixel_data_size = row_size * height
    file_size = 54 + pixel_data_size

    with path.open("wb") as f:
        f.write(b"BM")
        f.write(file_size.to_bytes(4, "little"))
        f.write((0).to_bytes(4, "little"))
        f.write((54).to_bytes(4, "little"))
        f.write((40).to_bytes(4, "little"))
        f.write(width.to_bytes(4, "little", signed=True))
        f.write(height.to_bytes(4, "little", signed=True))
        f.write((1).to_bytes(2, "little"))
        f.write((24).to_bytes(2, "little"))
        f.write((0).to_bytes(4, "little"))
        f.write(pixel_data_size.to_bytes(4, "little"))
        f.write((2835).to_bytes(4, "little", signed=True))
        f.write((2835).to_bytes(4, "little", signed=True))
        f.write((0).to_bytes(4, "little"))
        f.write((0).to_bytes(4, "little"))

        pad = b"\x00" * (row_size - width * 3)
        for y in range(src_h - 1, -1, -1):
            for _ in range(scale):
                row = bytearray()
                for x in range(src_w):
                    color = b"\xff\xff\xff" if pixels[y][x] else b"\x00\x00\x00"
                    row.extend(color * scale)
                row.extend(pad)
                f.write(row)


def find_stm32_port(requested: str | None) -> str:
    if requested:
        return requested

    for port in list_ports.comports():
        hwid = (port.hwid or "").upper()
        desc = (port.description or "").upper()
        if "0483:5740" in hwid or "VID_0483&PID_5740" in hwid:
            return port.device
        if "STM" in desc and "COM" in port.device.upper():
            return port.device

    return "COM7" if sys.platform.startswith("win") else "/dev/ttyACM0"


def parse_device_line(raw_line: bytes):
    text = raw_line.decode("ascii", errors="ignore").replace("\x00", "").strip()
    if not text:
        return None
    if text == "t_ms,red,ir,irq_count,sample_count":
        return "header"

    parts = text.split(",")
    if len(parts) < 5:
        return None

    try:
        return {
            "t_ms": int(parts[0]),
            "red": int(parts[1]),
            "ir": int(parts[2]),
            "irq_count": int(parts[3]),
            "sample_count": int(parts[4]),
        }
    except ValueError:
        return None


def rms(values) -> float:
    if not values:
        return 0.0
    return math.sqrt(sum(v * v for v in values) / len(values))


def round_or_none(value, digits: int = 3):
    if value is None:
        return None
    return round(float(value), digits)


class RunningStats:
    def __init__(self) -> None:
        self.count = 0
        self.mean = 0.0
        self.m2 = 0.0
        self.min_value = None
        self.max_value = None

    def update(self, value) -> None:
        if value is None:
            return

        x = float(value)
        self.count += 1
        delta = x - self.mean
        self.mean += delta / self.count
        delta2 = x - self.mean
        self.m2 += delta * delta2

        if self.min_value is None or x < self.min_value:
            self.min_value = x
        if self.max_value is None or x > self.max_value:
            self.max_value = x

    def as_dict(self, digits: int = 3) -> dict:
        if self.count == 0:
            return {"count": 0, "min": None, "max": None, "mean": None, "std": None}

        variance = self.m2 / (self.count - 1) if self.count > 1 else 0.0
        return {
            "count": self.count,
            "min": round(self.min_value, digits),
            "max": round(self.max_value, digits),
            "mean": round(self.mean, digits),
            "std": round(math.sqrt(max(0.0, variance)), digits),
        }


def parse_event_arg(text: str) -> dict:
    if ":" not in text:
        return {"event_s": None, "event_label": text}

    offset, label = text.split(":", 1)
    try:
        event_s = float(offset)
    except ValueError:
        event_s = None
        label = text

    return {"event_s": event_s, "event_label": label.strip()}


class PPGEstimator:
    def __init__(
        self,
        window_s: float,
        finger_ir_min: float,
        min_ac_rms: float,
        min_peak_ms: float,
        max_peak_ms: float,
        peak_detector: str,
        peak_base_threshold_ratio: float,
        peak_dynamic_min_hr_ratio: float,
        peak_recovery_start_hr_ratio: float,
        peak_recovery_threshold_ratio: float,
        peak_candidate_hold_ms: float,
        dc_alpha: float,
        filt_alpha: float,
        warmup_s: float,
        spo2_warmup_s: float,
        min_spo2_score: float,
        min_valid_hr_bpm: float,
        max_valid_hr_bpm: float,
        hr_confirm_samples: int,
        hr_confirm_tolerance_bpm: float,
        hr_confirm_interval_ms: int,
    ) -> None:
        self.window_ms = int(window_s * 1000.0)
        self.finger_ir_min = finger_ir_min
        self.min_ac_rms = min_ac_rms
        self.min_peak_ms = min_peak_ms
        self.max_peak_ms = max_peak_ms
        self.peak_detector = peak_detector
        self.peak_base_threshold_ratio = peak_base_threshold_ratio
        self.peak_dynamic_min_hr_ratio = peak_dynamic_min_hr_ratio
        self.peak_recovery_start_hr_ratio = peak_recovery_start_hr_ratio
        self.peak_recovery_threshold_ratio = peak_recovery_threshold_ratio
        self.peak_candidate_hold_ms = peak_candidate_hold_ms
        self.dc_alpha = dc_alpha
        self.filt_alpha = filt_alpha
        self.hr_warmup_ms = int(warmup_s * 1000.0)
        self.spo2_warmup_ms = int(spo2_warmup_s * 1000.0)
        self.min_spo2_score = min_spo2_score
        self.min_valid_hr_bpm = min_valid_hr_bpm
        self.max_valid_hr_bpm = max_valid_hr_bpm
        self.hr_confirm_samples = max(1, hr_confirm_samples)
        self.hr_confirm_tolerance_bpm = hr_confirm_tolerance_bpm
        self.hr_confirm_interval_ms = max(1, hr_confirm_interval_ms)
        self.start_ms = None
        self.finger_start_ms = None
        self.samples = deque()
        self.peaks_ms = deque(maxlen=12)
        self.hr_history = deque(maxlen=5)
        self.hr_confirm_candidates = deque(maxlen=self.hr_confirm_samples)
        self.hr_confirmed = False
        self.last_hr_confirm_candidate_ms = None
        self.hr_bpm = None
        self.hr_peak_bpm = None
        self.hr_autocorr_bpm = None
        self.spo2 = None
        self.ratio = None
        self.finger_present = False
        self.fs_hz = None
        self.red_dc = None
        self.ir_dc = None
        self.red_ac = None
        self.ir_ac = None
        self.red_filt = 0.0
        self.ir_filt = 0.0
        self.red_rms = None
        self.ir_rms = None
        self.last_peak = False
        self.last_peak_interval_ms = None
        self.pending_peak = None
        self.finger_score = None

    def update(self, sample: dict) -> None:
        # MAX30102 FIFO samples are emitted at 100 Hz. The device sample counter
        # is monotonic, while USB service timestamps may jitter within a FIFO burst.
        t_ms = sample["sample_count"] * SAMPLE_INTERVAL_MS
        if self.start_ms is None:
            self.start_ms = t_ms
        self.last_peak = False
        self.last_peak_interval_ms = None

        raw_finger_candidate = sample["ir"] >= self.finger_ir_min
        if not raw_finger_candidate:
            self.finger_start_ms = None
            self._clear_peak_tracking()
            self.samples.clear()
            self.red_dc = float(sample["red"])
            self.ir_dc = float(sample["ir"])
            self.red_filt = 0.0
            self.ir_filt = 0.0
            self.red_ac = 0.0
            self.ir_ac = 0.0
            self.red_rms = None
            self.ir_rms = None
            self.finger_score = None
            self.finger_present = False
            self.hr_bpm = None
            self.hr_peak_bpm = None
            self.hr_autocorr_bpm = None
            self.hr_history.clear()
            self.hr_confirm_candidates.clear()
            self.hr_confirmed = False
            self.last_hr_confirm_candidate_ms = None
            self.spo2 = None
            self.ratio = None
            self.samples.append((t_ms, sample["red"], sample["ir"], self.red_filt, self.ir_filt))
            return

        if self.finger_start_ms is None:
            self.finger_start_ms = t_ms
            self._clear_peak_tracking()
            self.samples.clear()
            self.red_dc = float(sample["red"])
            self.ir_dc = float(sample["ir"])
            self.red_filt = 0.0
            self.ir_filt = 0.0

        if self.red_dc is None or self.ir_dc is None:
            self.red_dc = float(sample["red"])
            self.ir_dc = float(sample["ir"])

        self.red_dc += self.dc_alpha * (float(sample["red"]) - self.red_dc)
        self.ir_dc += self.dc_alpha * (float(sample["ir"]) - self.ir_dc)
        self.red_ac = sample["red"] - self.red_dc
        self.ir_ac = sample["ir"] - self.ir_dc
        self.red_filt += self.filt_alpha * (self.red_ac - self.red_filt)
        self.ir_filt += self.filt_alpha * (self.ir_ac - self.ir_filt)

        self.samples.append((t_ms, sample["red"], sample["ir"], self.red_filt, self.ir_filt))

        cutoff = t_ms - self.window_ms
        while self.samples and self.samples[0][0] < cutoff:
            self.samples.popleft()

        if len(self.samples) < 20:
            return

        times = [s[0] for s in self.samples]
        red_filt_values = [s[3] for s in self.samples]
        ir_filt_values = [s[4] for s in self.samples]

        duration_ms = max(1, times[-1] - times[0])
        self.fs_hz = 1000.0 * (len(times) - 1) / duration_ms

        self.red_rms = rms(red_filt_values)
        self.ir_rms = rms(ir_filt_values)
        dc_score = self.ir_dc / self.finger_ir_min if self.finger_ir_min > 0.0 else 0.0
        ac_score = self.ir_rms / self.min_ac_rms if self.min_ac_rms > 0.0 else 0.0
        self.finger_score = min(dc_score, ac_score)

        self.finger_present = (self.ir_dc >= self.finger_ir_min) and (self.ir_rms >= self.min_ac_rms)
        if not self.finger_present:
            self._clear_peak_tracking()
            self.hr_bpm = None
            self.hr_peak_bpm = None
            self.hr_autocorr_bpm = None
            self.hr_history.clear()
            self.hr_confirm_candidates.clear()
            self.hr_confirmed = False
            self.last_hr_confirm_candidate_ms = None
            self.spo2 = None
            self.ratio = None
            return

        self.last_peak = self._detect_peak(times, ir_filt_values, self.ir_rms)

        if (t_ms - self.finger_start_ms) < self.hr_warmup_ms:
            self.hr_bpm = None
            self.hr_autocorr_bpm = None
            self.hr_history.clear()
            self.hr_confirm_candidates.clear()
            self.hr_confirmed = False
            self.last_hr_confirm_candidate_ms = None
            self.spo2 = None
            self.ratio = None
            return

        self.hr_autocorr_bpm = self._estimate_hr_autocorr(ir_filt_values)
        if not self.hr_confirmed:
            self.hr_bpm = None
            if self.hr_autocorr_bpm is not None:
                if (
                    self.last_hr_confirm_candidate_ms is None
                    or (t_ms - self.last_hr_confirm_candidate_ms) >= self.hr_confirm_interval_ms
                ):
                    self.last_hr_confirm_candidate_ms = t_ms
                    self.hr_confirm_candidates.append(self.hr_autocorr_bpm)

                    if len(self.hr_confirm_candidates) >= self.hr_confirm_samples:
                        spread = max(self.hr_confirm_candidates) - min(self.hr_confirm_candidates)
                        if spread <= self.hr_confirm_tolerance_bpm:
                            confirmed_hr = statistics.median(self.hr_confirm_candidates)
                            self.hr_confirmed = True
                            self.hr_history.clear()
                            self.hr_history.append(confirmed_hr)
                            self.hr_bpm = confirmed_hr
            else:
                self.hr_confirm_candidates.clear()
                self.last_hr_confirm_candidate_ms = None
        elif self.hr_autocorr_bpm is not None:
            self.hr_history.append(self.hr_autocorr_bpm)
            self.hr_bpm = statistics.median(self.hr_history)
        elif self.hr_history:
            self.hr_bpm = statistics.median(self.hr_history)
        else:
            self.hr_bpm = self.hr_peak_bpm

        if (t_ms - self.finger_start_ms) < self.spo2_warmup_ms:
            self.spo2 = None
            self.ratio = None
            return

        self._estimate_spo2(self.red_dc, self.ir_dc, self.red_rms, self.ir_rms)

    def _clear_peak_tracking(self) -> None:
        self.peaks_ms.clear()
        self.pending_peak = None
        self.last_peak_interval_ms = None

    def _detect_peak(self, times, ir_ac_values, ir_rms: float) -> bool:
        if self.peak_detector == "robust":
            return self._detect_peak_robust(times, ir_ac_values, ir_rms)
        return self._detect_peak_legacy(times, ir_ac_values, ir_rms)

    def _detect_peak_legacy(self, times, ir_ac_values, ir_rms: float) -> bool:
        if len(ir_ac_values) < 3:
            return False

        y0, y1, y2 = ir_ac_values[-3], ir_ac_values[-2], ir_ac_values[-1]
        t_peak = times[-2]
        threshold = max(self.min_ac_rms, self.peak_base_threshold_ratio * ir_rms)

        if not (y1 > y0 and y1 >= y2 and y1 > threshold):
            return False

        if self.peaks_ms:
            dt_ms = t_peak - self.peaks_ms[-1]
            if dt_ms < self.min_peak_ms:
                return False
            self.last_peak_interval_ms = dt_ms

        self._append_peak(t_peak)
        return True

    def _detect_peak_robust(self, times, ir_ac_values, ir_rms: float) -> bool:
        if len(ir_ac_values) < 3:
            return False

        accepted = self._flush_pending_peak(times[-1])
        y0, y1, y2 = ir_ac_values[-3], ir_ac_values[-2], ir_ac_values[-1]
        t_peak = times[-2]
        threshold = self._peak_threshold(t_peak, ir_rms)

        if not (y1 > y0 and y1 >= y2 and y1 > threshold):
            return accepted

        if self.peaks_ms:
            dt_ms = t_peak - self.peaks_ms[-1]
            if dt_ms < self._dynamic_min_peak_ms():
                return accepted

        self._store_pending_peak(t_peak, y1)
        return accepted

    def _append_peak(self, t_peak: float) -> None:
        if self.peaks_ms:
            self.last_peak_interval_ms = t_peak - self.peaks_ms[-1]
        self.peaks_ms.append(t_peak)

        intervals = [
            self.peaks_ms[i] - self.peaks_ms[i - 1]
            for i in range(1, len(self.peaks_ms))
            if self.min_peak_ms <= (self.peaks_ms[i] - self.peaks_ms[i - 1]) <= self.max_peak_ms
        ]
        if len(intervals) >= 3:
            rr_ms = statistics.median(intervals[-5:])
            hr_bpm = 60000.0 / rr_ms
            self.hr_peak_bpm = hr_bpm if self.min_valid_hr_bpm <= hr_bpm <= self.max_valid_hr_bpm else None

    def _store_pending_peak(self, t_peak: float, amplitude: float) -> None:
        if self.pending_peak is None:
            self.pending_peak = (t_peak, amplitude)
            return

        pending_t, pending_amplitude = self.pending_peak
        if (t_peak - pending_t) <= self.peak_candidate_hold_ms:
            if amplitude > pending_amplitude:
                self.pending_peak = (t_peak, amplitude)
        else:
            self.pending_peak = (t_peak, amplitude)

    def _flush_pending_peak(self, t_now: float) -> bool:
        if self.pending_peak is None:
            return False
        pending_t, _amplitude = self.pending_peak
        if (t_now - pending_t) < self.peak_candidate_hold_ms:
            return False
        self.pending_peak = None
        self._append_peak(pending_t)
        return True

    def _hr_reference_ppi_ms(self) -> float | None:
        for hr_bpm in (self.hr_bpm, self.hr_autocorr_bpm, self.hr_peak_bpm):
            if hr_bpm is not None and self.min_valid_hr_bpm <= hr_bpm <= self.max_valid_hr_bpm:
                return 60000.0 / hr_bpm
        return None

    def _dynamic_min_peak_ms(self) -> float:
        reference_ppi_ms = self._hr_reference_ppi_ms()
        if reference_ppi_ms is None:
            return self.min_peak_ms
        return max(self.min_peak_ms, reference_ppi_ms * self.peak_dynamic_min_hr_ratio)

    def _peak_threshold(self, t_peak: float, ir_rms: float) -> float:
        threshold_ratio = self.peak_base_threshold_ratio
        reference_ppi_ms = self._hr_reference_ppi_ms()
        if reference_ppi_ms is not None and self.peaks_ms:
            dt_ms = t_peak - self.peaks_ms[-1]
            if dt_ms >= reference_ppi_ms * self.peak_recovery_start_hr_ratio:
                threshold_ratio = self.peak_recovery_threshold_ratio
        return max(self.min_ac_rms, threshold_ratio * ir_rms)

    def _estimate_hr_autocorr(self, ir_filt_values) -> float | None:
        if len(ir_filt_values) < 600:
            return None

        values = list(ir_filt_values[-1000:])
        mean = statistics.fmean(values)
        centered = [v - mean for v in values]
        energy = sum(v * v for v in centered)
        if energy <= 1.0:
            return None

        min_lag = max(1, int(self.min_peak_ms / 10.0))
        max_lag = max(min_lag, int(self.max_peak_ms / 10.0))
        best_lag = None
        best_corr = None

        for lag in range(min_lag, min(max_lag, len(centered) - 2) + 1):
            corr = 0.0
            for i in range(lag, len(centered)):
                corr += centered[i] * centered[i - lag]
            corr /= energy
            if best_corr is None or corr > best_corr:
                best_corr = corr
                best_lag = lag

        if best_lag is None or best_corr is None or best_corr < 0.20:
            return None

        hr_bpm = 6000.0 / best_lag
        if not (self.min_valid_hr_bpm <= hr_bpm <= self.max_valid_hr_bpm):
            return None
        return hr_bpm

    def _estimate_spo2(self, red_dc: float, ir_dc: float, red_rms: float, ir_rms: float) -> None:
        if red_dc <= 0.0 or ir_dc <= 0.0 or red_rms <= 1.0 or ir_rms <= 1.0:
            self.spo2 = None
            self.ratio = None
            return

        self.ratio = (red_rms / red_dc) / (ir_rms / ir_dc)
        if self.finger_score is None or self.finger_score < self.min_spo2_score:
            self.spo2 = None
        elif 0.40 <= self.ratio <= 0.85:
            spo2 = 101.0 - (7.0 * self.ratio)
            self.spo2 = max(70.0, min(100.0, spo2))
        else:
            self.spo2 = None

    def processed_row(self, session_id: str, label: str, pc_time: str, sample: dict) -> dict:
        return {
            "session_id": session_id,
            "label": label,
            "pc_time": pc_time,
            "t_ms": sample["t_ms"],
            "sample_time_ms": sample["sample_count"] * SAMPLE_INTERVAL_MS,
            "sample_count": sample["sample_count"],
            "red": sample["red"],
            "ir": sample["ir"],
            "fs_hz": round_or_none(self.fs_hz),
            "red_dc": round_or_none(self.red_dc),
            "ir_dc": round_or_none(self.ir_dc),
            "red_ac": round_or_none(self.red_ac),
            "ir_ac": round_or_none(self.ir_ac),
            "red_filt": round_or_none(self.red_filt),
            "ir_filt": round_or_none(self.ir_filt),
            "red_rms": round_or_none(self.red_rms),
            "ir_rms": round_or_none(self.ir_rms),
            "finger_score": round_or_none(self.finger_score),
            "finger_present": 1 if self.finger_present else 0,
            "hr_confirmed": 1 if self.hr_confirmed else 0,
            "hr_bpm": round_or_none(self.hr_bpm),
            "hr_peak_bpm": round_or_none(self.hr_peak_bpm),
            "hr_autocorr_bpm": round_or_none(self.hr_autocorr_bpm),
            "spo2": round_or_none(self.spo2),
            "ratio": round_or_none(self.ratio, 5),
            "peak": 1 if self.last_peak else 0,
            "peak_interval_ms": round_or_none(self.last_peak_interval_ms),
        }

    def status_text(self, sample: dict) -> str:
        fs = "--" if self.fs_hz is None else f"{self.fs_hz:5.1f}"
        hr = "--" if self.hr_bpm is None else f"{self.hr_bpm:5.1f}"
        hr_auto = "--" if self.hr_autocorr_bpm is None else f"{self.hr_autocorr_bpm:5.1f}"
        spo2 = "--" if self.spo2 is None else f"{self.spo2:5.1f}"
        ratio = "--" if self.ratio is None else f"{self.ratio:4.2f}"
        score = "--" if self.finger_score is None else f"{self.finger_score:4.2f}"
        finger = "yes" if self.finger_present else "no "
        return (
            f"samples={sample['sample_count']:>7} fs={fs}Hz "
            f"red={sample['red']:>6} ir={sample['ir']:>6} "
            f"finger={finger} score={score} hr={hr} auto={hr_auto} spo2={spo2} ratio={ratio}"
        )


class OledWaveformPreview:
    def __init__(
        self,
        frames_dir: Path,
        width: int = 128,
        height: int = 32,
        px_per_sec: float = 32.0,
        frame_ms: int = 500,
        scale: int = 3,
        max_frames: int = 0,
        waveform_style: str = "pulse",
    ) -> None:
        self.frames_dir = frames_dir
        self.width = width
        self.height = height
        self.mid_y = height // 2
        self.amp_y = max(4, (height // 2) - 4)
        self.column_period_samples = max(1, int(round(100.0 / max(1.0, px_per_sec))))
        self.frame_ms = max(1, frame_ms)
        self.scale = max(1, scale)
        self.max_frames = max(0, max_frames)
        self.waveform_style = waveform_style
        self.columns: list[int | None] = [None] * width
        self.streaming = False
        self.draining = False
        self.ever_started = False
        self.last_column_sample: int | None = None
        self.last_frame_ms: int | None = None
        self.frames_written = 0
        self.start_count = 0
        self.drain_count = 0
        self.column_log_file = None
        self.column_log_writer = None
        self.display_filt = 0.0
        self.display_envelope = 1.0
        self.display_prev_y: int | None = None
        self.last_peak_sample_count: int | None = None
        self.recent_peak_values = deque(maxlen=8)
        self.last_peak_display_px = self.amp_y

    def update(self, sample: dict, estimator: PPGEstimator, processed_row: dict) -> None:
        sample_count = sample["sample_count"]
        sample_time_ms = sample_count * SAMPLE_INTERVAL_MS
        hr_ready = processed_row["hr_bpm"] not in ("", None)
        should_stream = (
            processed_row["finger_present"] == 1
            and processed_row.get("hr_confirmed") == 1
            and hr_ready
        )

        if should_stream and not self.streaming:
            self.streaming = True
            self.draining = False
            self.ever_started = True
            self.start_count += 1
            self.columns = [None] * self.width
            self.last_column_sample = None
            self.last_frame_ms = None
            self.display_filt = 0.0
            self.display_envelope = 1.0
            self.display_prev_y = None
            self.last_peak_sample_count = None
            self.recent_peak_values.clear()
            self.last_peak_display_px = self.amp_y
        elif (not should_stream) and self.streaming:
            self.streaming = False
            self.draining = True
            self.drain_count += 1

        if self.streaming and processed_row["peak"]:
            self.last_peak_sample_count = sample_count
            self.last_peak_display_px = self._bounded_peak_height_px(estimator)

        if self.streaming or self.draining:
            if self.last_column_sample is None:
                self.last_column_sample = sample_count - self.column_period_samples

            while (sample_count - self.last_column_sample) >= self.column_period_samples:
                self.last_column_sample += self.column_period_samples
                if self.streaming:
                    y = self._wave_y(estimator, sample_count)
                    self._shift_insert(y)
                    self._write_column_log(sample, estimator, processed_row, y, "stream")
                else:
                    self._shift_insert(None)
                    self._write_column_log(sample, estimator, processed_row, None, "drain")

                if self.draining and not any(y is not None for y in self.columns):
                    self.draining = False
                    self.last_column_sample = None
                    break

        if (self.streaming or self.draining) and self._should_write_frame(sample_time_ms):
            self._write_frame(sample_time_ms, sample_count)

    def write_summary(self) -> None:
        if not self.frames_dir:
            return

        if self.column_log_file is not None:
            self.column_log_file.flush()
            self.column_log_file.close()
            self.column_log_file = None
            self.column_log_writer = None

        summary = {
            "width": self.width,
            "height": self.height,
            "flow": "new waveform columns enter at x=0 and move right until disappearing",
            "source": "real beat peaks from the existing PPG estimator; display height is bounded-normalized",
            "waveform_style": self.waveform_style,
            "starts_after": "finger_present and first HR confirmed",
            "stops_after": "finger_present clears or HR is no longer confirmed",
            "column_period_samples": self.column_period_samples,
            "frame_ms": self.frame_ms,
            "scale": self.scale,
            "frames_written": self.frames_written,
            "waveform_start_count": self.start_count,
            "waveform_drain_count": self.drain_count,
        }
        self.frames_dir.mkdir(parents=True, exist_ok=True)
        (self.frames_dir / "waveform_summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")

    def _ensure_column_log(self) -> None:
        if self.column_log_writer is not None:
            return

        self.frames_dir.mkdir(parents=True, exist_ok=True)
        self.column_log_file = (self.frames_dir / "waveform_columns.csv").open("w", newline="")
        self.column_log_writer = csv.DictWriter(
            self.column_log_file,
            fieldnames=[
                "sample_count",
                "sample_time_ms",
                "mode",
                "x_insert",
                "y",
                "ir_filt",
                "ir_rms",
                "hr_bpm",
                "hr_confirmed",
                "finger_present",
                "peak",
                "display_filt",
                "display_envelope",
                "peak_display_px",
            ],
        )
        self.column_log_writer.writeheader()

    def _write_column_log(
        self,
        sample: dict,
        estimator: PPGEstimator,
        processed_row: dict,
        y: int | None,
        mode: str,
    ) -> None:
        self._ensure_column_log()
        self.column_log_writer.writerow(
            {
                "sample_count": sample["sample_count"],
                "sample_time_ms": sample["sample_count"] * SAMPLE_INTERVAL_MS,
                "mode": mode,
                "x_insert": 0,
                "y": "" if y is None else y,
                "ir_filt": round_or_none(estimator.ir_filt),
                "ir_rms": round_or_none(estimator.ir_rms),
                "hr_bpm": processed_row["hr_bpm"],
                "hr_confirmed": processed_row.get("hr_confirmed", 0),
                "finger_present": processed_row["finger_present"],
                "peak": processed_row["peak"],
                "display_filt": round_or_none(self.display_filt),
                "display_envelope": round_or_none(self.display_envelope),
                "peak_display_px": round_or_none(self.last_peak_display_px),
            }
        )

    def _wave_y(self, estimator: PPGEstimator, sample_count: int) -> int:
        if self.waveform_style in ("bounded", "pulse"):
            return self._pulse_wave_y(sample_count)
        return self._ppg_wave_y(estimator)

    def _bounded_peak_height_px(self, estimator: PPGEstimator) -> float:
        if self.waveform_style == "pulse":
            return float(self.amp_y)

        peak_value = abs(estimator.ir_filt)
        self.recent_peak_values.append(peak_value)
        lo = min(self.recent_peak_values)
        hi = max(self.recent_peak_values)
        if (hi - lo) < 1.0:
            normalized = 0.50
        else:
            normalized = clamp((peak_value - lo) / (hi - lo), 0.0, 1.0)

        max_peak_px = float(max(6, self.amp_y - 1))
        min_peak_px = max(5.0, max_peak_px - 4.0)
        return min_peak_px + (normalized * (max_peak_px - min_peak_px))

    def _pulse_wave_y(self, sample_count: int) -> int:
        if self.last_peak_sample_count is None:
            return self.mid_y

        dt_ms = (sample_count - self.last_peak_sample_count) * SAMPLE_INTERVAL_MS
        if dt_ms < 0 or dt_ms > 520:
            value = 0.0
        elif dt_ms <= 30:
            value = 1.0 - (0.10 * (dt_ms / 30.0))
        elif dt_ms <= 90:
            value = 0.90 + ((-0.55 - 0.90) * ((dt_ms - 30.0) / 60.0))
        elif dt_ms <= 180:
            value = -0.55 + ((0.20 + 0.55) * ((dt_ms - 90.0) / 90.0))
        elif dt_ms <= 320:
            value = 0.20 * (1.0 - ((dt_ms - 180.0) / 140.0))
        else:
            value = 0.0

        return int(round(clamp(self.mid_y - value * self.last_peak_display_px, 3, self.height - 4)))

    def _ppg_wave_y(self, estimator: PPGEstimator) -> int:
        # Display-only conditioning: smooth high-frequency jitter, use AGC to
        # keep beat heights visually consistent, then compress outliers.
        self.display_filt += 0.35 * (estimator.ir_filt - self.display_filt)
        abs_v = abs(self.display_filt)
        if abs_v > self.display_envelope:
            self.display_envelope = (0.70 * self.display_envelope) + (0.30 * abs_v)
        else:
            self.display_envelope = (0.995 * self.display_envelope) + (0.005 * abs_v)

        scale = max(40.0, self.display_envelope * 0.90)
        normalized = math.tanh(self.display_filt / scale)
        target_y = int(round(clamp(self.mid_y - normalized * self.amp_y, 3, self.height - 4)))

        if self.display_prev_y is not None:
            max_step = 8
            target_y = int(round((0.45 * self.display_prev_y) + (0.55 * target_y)))
            target_y = int(clamp(target_y, self.display_prev_y - max_step, self.display_prev_y + max_step))

        self.display_prev_y = target_y
        return target_y

    def _shift_insert(self, y: int | None) -> None:
        self.columns = [y] + self.columns[:-1]

    def _should_write_frame(self, sample_time_ms: int) -> bool:
        if self.max_frames and self.frames_written >= self.max_frames:
            return False
        return self.last_frame_ms is None or (sample_time_ms - self.last_frame_ms) >= self.frame_ms

    def _write_frame(self, sample_time_ms: int, sample_count: int) -> None:
        pixels = [[False for _ in range(self.width)] for _ in range(self.height)]
        prev_x = None
        prev_y = None
        for x, y in enumerate(self.columns):
            if y is None:
                prev_x = None
                prev_y = None
                continue
            if prev_x is None or prev_y is None:
                pixels[y][x] = True
            else:
                draw_line(pixels, prev_x, prev_y, x, y)
            prev_x = x
            prev_y = y

        self.frames_dir.mkdir(parents=True, exist_ok=True)
        frame_name = f"frame_{self.frames_written:04d}_sc{sample_count}_t{sample_time_ms}.bmp"
        write_oled_bmp(self.frames_dir / frame_name, pixels, self.scale)
        self.frames_written += 1
        self.last_frame_ms = sample_time_ms


def default_raw_csv_path() -> Path:
    stamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    return Path(f"ppg_raw_{stamp}.csv")


def derived_path(path: Path, suffix: str, extension: str) -> Path:
    return path.with_name(f"{path.stem}{suffix}{extension}")


def write_summary(
    summary_json: Path,
    session_id: str,
    label: str,
    notes: str,
    port_name: str | None,
    baud: int | None,
    elapsed_s: float,
    sample_rows: int,
    raw_csv: Path,
    processed_csv: Path,
    event_csv: Path,
    events: list,
    args,
    first_sample,
    last_sample,
    raw_stats: dict,
    processed_stats: dict,
    nonmonotonic_t_ms: int,
    nonmonotonic_sample_count: int,
    missing_sample_count: int,
    finger_present_samples: int,
    peak_count: int,
    estimator: PPGEstimator,
) -> None:
    summary = {
        "session_id": session_id,
        "label": label,
        "notes": notes,
        "port": port_name,
        "baud": baud,
        "elapsed_s": round(elapsed_s, 3),
        "samples": sample_rows,
        "raw_csv": str(raw_csv),
        "processed_csv": str(processed_csv),
        "event_csv": str(event_csv),
        "events": events,
        "parameters": {
            "window_s": args.window,
            "finger_ir_min": args.finger_ir_min,
            "min_ac_rms": args.min_ac_rms,
            "min_peak_ms": args.min_peak_ms,
            "max_peak_ms": args.max_peak_ms,
            "peak_detector": args.peak_detector,
            "peak_base_threshold_ratio": args.peak_base_threshold_ratio,
            "peak_dynamic_min_hr_ratio": args.peak_dynamic_min_hr_ratio,
            "peak_recovery_start_hr_ratio": args.peak_recovery_start_hr_ratio,
            "peak_recovery_threshold_ratio": args.peak_recovery_threshold_ratio,
            "peak_candidate_hold_ms": args.peak_candidate_hold_ms,
            "dc_alpha": args.dc_alpha,
            "filt_alpha": args.filt_alpha,
            "warmup_s": args.warmup,
            "spo2_warmup_s": args.spo2_warmup,
            "min_spo2_score": args.min_spo2_score,
            "min_valid_hr_bpm": args.min_valid_hr_bpm,
            "max_valid_hr_bpm": args.max_valid_hr_bpm,
            "hr_confirm_samples": args.hr_confirm_samples,
            "hr_confirm_tolerance_bpm": args.hr_confirm_tolerance_bpm,
            "hr_confirm_interval_ms": args.hr_confirm_interval_ms,
        },
        "first_sample": first_sample,
        "last_sample": last_sample,
        "data_quality": {
            "time_gap_ms": processed_stats["time_gap_ms"].as_dict(),
            "sample_time_gap_ms": processed_stats["sample_time_gap_ms"].as_dict(),
            "sample_count_gap": processed_stats["sample_count_gap"].as_dict(),
            "nonmonotonic_t_ms": nonmonotonic_t_ms,
            "nonmonotonic_sample_count": nonmonotonic_sample_count,
            "missing_sample_count": missing_sample_count,
        },
        "raw_stats": {
            "red": raw_stats["red"].as_dict(),
            "ir": raw_stats["ir"].as_dict(),
        },
        "processed_stats": {
            "fs_hz": processed_stats["fs_hz"].as_dict(),
            "red_dc": processed_stats["red_dc"].as_dict(),
            "ir_dc": processed_stats["ir_dc"].as_dict(),
            "red_rms": processed_stats["red_rms"].as_dict(),
            "ir_rms": processed_stats["ir_rms"].as_dict(),
            "red_filt": processed_stats["red_filt"].as_dict(),
            "ir_filt": processed_stats["ir_filt"].as_dict(),
            "finger_score": processed_stats["finger_score"].as_dict(),
            "hr_bpm": processed_stats["hr_bpm"].as_dict(),
            "hr_peak_bpm": processed_stats["hr_peak_bpm"].as_dict(),
            "hr_autocorr_bpm": processed_stats["hr_autocorr_bpm"].as_dict(),
            "spo2": processed_stats["spo2"].as_dict(),
            "ratio": processed_stats["ratio"].as_dict(5),
        },
        "detection": {
            "finger_present_samples": finger_present_samples,
            "finger_present_fraction": round((finger_present_samples / sample_rows), 4) if sample_rows else 0.0,
            "peak_count": peak_count,
        },
        "final": {
            "fs_hz": round_or_none(estimator.fs_hz),
            "finger_present": estimator.finger_present,
            "hr_bpm": round_or_none(estimator.hr_bpm),
            "hr_peak_bpm": round_or_none(estimator.hr_peak_bpm),
            "hr_autocorr_bpm": round_or_none(estimator.hr_autocorr_bpm),
            "spo2": round_or_none(estimator.spo2),
            "ratio": round_or_none(estimator.ratio, 5),
        },
    }
    summary_json.write_text(json.dumps(summary, indent=2), encoding="utf-8")


def make_stats() -> tuple[dict, dict]:
    raw_stats = {
        "red": RunningStats(),
        "ir": RunningStats(),
    }
    processed_stats = {
        "fs_hz": RunningStats(),
        "red_dc": RunningStats(),
        "ir_dc": RunningStats(),
        "red_rms": RunningStats(),
        "ir_rms": RunningStats(),
        "red_filt": RunningStats(),
        "ir_filt": RunningStats(),
        "finger_score": RunningStats(),
        "hr_bpm": RunningStats(),
        "hr_peak_bpm": RunningStats(),
        "hr_autocorr_bpm": RunningStats(),
        "spo2": RunningStats(),
        "ratio": RunningStats(),
        "time_gap_ms": RunningStats(),
        "sample_time_gap_ms": RunningStats(),
        "sample_count_gap": RunningStats(),
    }
    return raw_stats, processed_stats


def update_stats(
    sample: dict,
    processed_row: dict,
    raw_stats: dict,
    processed_stats: dict,
) -> None:
    raw_stats["red"].update(sample["red"])
    raw_stats["ir"].update(sample["ir"])
    for key in (
        "fs_hz",
        "red_dc",
        "ir_dc",
        "red_rms",
        "ir_rms",
        "red_filt",
        "ir_filt",
        "finger_score",
        "hr_bpm",
        "hr_peak_bpm",
        "hr_autocorr_bpm",
        "spo2",
        "ratio",
    ):
        processed_stats[key].update(processed_row[key])


def main() -> int:
    parser = argparse.ArgumentParser(description="Read STM32 MAX30102 raw CSV data over USB CDC.")
    parser.add_argument("--port", help="Serial port, for example COM7. Auto-detects STM32 CDC when omitted.")
    parser.add_argument("--baud", type=int, default=115200, help="CDC API baud rate placeholder.")
    parser.add_argument("--duration", type=float, default=0.0, help="Stop after N seconds. 0 means run until Ctrl+C.")
    parser.add_argument("--csv", type=Path, default=default_raw_csv_path(), help="Output raw CSV file path.")
    parser.add_argument("--input-csv", type=Path, help="Replay an existing raw CSV instead of reading the serial port.")
    parser.add_argument("--processed-csv", type=Path, help="Output processed CSV path.")
    parser.add_argument("--summary-json", type=Path, help="Output run summary JSON path.")
    parser.add_argument("--event-csv", type=Path, help="Output event marker CSV path.")
    parser.add_argument("--session-id", help="Session id written to every output row. Defaults to raw CSV stem.")
    parser.add_argument("--label", default="unlabeled", help="Experiment label, for example no_finger or finger_light.")
    parser.add_argument("--notes", default="", help="Free-form notes saved in the summary JSON.")
    parser.add_argument("--event", action="append", default=[], help="Known event marker, for example 0:no_finger or 12.5:finger_on.")
    parser.add_argument("--no-save", action="store_true", help="Do not save raw, processed, or summary files.")
    parser.add_argument("--window", type=float, default=10.0, help="Rolling analysis window in seconds.")
    parser.add_argument("--finger-ir-min", type=float, default=10000.0, help="Minimum IR DC level for finger-present.")
    parser.add_argument("--min-ac-rms", type=float, default=10.0, help="Minimum IR AC RMS for finger-present and HR analysis.")
    parser.add_argument("--min-peak-ms", type=float, default=300.0, help="Minimum beat interval in ms.")
    parser.add_argument("--max-peak-ms", type=float, default=1200.0, help="Maximum beat interval in ms.")
    parser.add_argument("--peak-detector", choices=("legacy", "robust"), default="robust", help="Peak detector used for PPI events.")
    parser.add_argument("--peak-base-threshold-ratio", type=float, default=0.45, help="Base peak threshold as a fraction of IR RMS.")
    parser.add_argument("--peak-dynamic-min-hr-ratio", type=float, default=0.58, help="Robust mode: minimum peak spacing as a fraction of HR-derived PPI.")
    parser.add_argument("--peak-recovery-start-hr-ratio", type=float, default=0.72, help="Robust mode: start lowering peak threshold after this fraction of HR-derived PPI.")
    parser.add_argument("--peak-recovery-threshold-ratio", type=float, default=0.28, help="Robust mode: recovery threshold as a fraction of IR RMS.")
    parser.add_argument("--peak-candidate-hold-ms", type=float, default=140.0, help="Robust mode: hold candidate local maxima before accepting the strongest peak.")
    parser.add_argument("--dc-alpha", type=float, default=0.02, help="IIR DC tracking alpha for drift removal.")
    parser.add_argument("--filt-alpha", type=float, default=0.20, help="IIR low-pass alpha applied after DC removal.")
    parser.add_argument("--warmup", type=float, default=10.0, help="Seconds to suppress HR while filters settle after finger detection.")
    parser.add_argument("--spo2-warmup", type=float, default=20.0, help="Seconds to suppress SpO2 after finger detection.")
    parser.add_argument("--min-spo2-score", type=float, default=4.0, help="Minimum finger quality score required before reporting SpO2.")
    parser.add_argument("--min-valid-hr-bpm", type=float, default=45.0, help="Minimum reportable heart rate.")
    parser.add_argument("--max-valid-hr-bpm", type=float, default=190.0, help="Maximum reportable heart rate.")
    parser.add_argument("--hr-confirm-samples", type=int, default=3, help="Autocorrelation HR candidates required before first HR display.")
    parser.add_argument("--hr-confirm-tolerance-bpm", type=float, default=12.0, help="Maximum spread among first HR confirmation candidates.")
    parser.add_argument("--hr-confirm-interval-ms", type=int, default=1000, help="Minimum spacing between first HR confirmation candidates.")
    parser.add_argument("--waveform-preview-dir", type=Path, help="Optional directory for 128x32 OLED waveform preview BMP frames.")
    parser.add_argument("--waveform-frame-ms", type=int, default=500, help="OLED waveform preview frame interval.")
    parser.add_argument("--waveform-px-per-sec", type=float, default=32.0, help="OLED waveform horizontal speed in pixels per second.")
    parser.add_argument("--waveform-scale", type=int, default=3, help="Scale factor for saved OLED preview BMP frames.")
    parser.add_argument("--waveform-max-frames", type=int, default=0, help="Maximum waveform preview frames to write. 0 means no limit.")
    parser.add_argument("--waveform-style", choices=("bounded", "pulse", "ppg"), default="bounded", help="OLED preview waveform style: bounded beat-height pulse, fixed pulse, or conditioned PPG.")
    args = parser.parse_args()

    port_name = None if args.input_csv else find_stm32_port(args.port)
    session_id = args.session_id or args.csv.stem
    processed_csv = args.processed_csv or derived_path(args.csv, "_processed", ".csv")
    summary_json = args.summary_json or derived_path(args.csv, "_summary", ".json")
    event_csv = args.event_csv or derived_path(args.csv, "_events", ".csv")
    events = [parse_event_arg(item) for item in args.event]
    estimator = PPGEstimator(
        window_s=args.window,
        finger_ir_min=args.finger_ir_min,
        min_ac_rms=args.min_ac_rms,
        min_peak_ms=args.min_peak_ms,
        max_peak_ms=args.max_peak_ms,
        peak_detector=args.peak_detector,
        peak_base_threshold_ratio=args.peak_base_threshold_ratio,
        peak_dynamic_min_hr_ratio=args.peak_dynamic_min_hr_ratio,
        peak_recovery_start_hr_ratio=args.peak_recovery_start_hr_ratio,
        peak_recovery_threshold_ratio=args.peak_recovery_threshold_ratio,
        peak_candidate_hold_ms=args.peak_candidate_hold_ms,
        dc_alpha=args.dc_alpha,
        filt_alpha=args.filt_alpha,
        warmup_s=args.warmup,
        spo2_warmup_s=args.spo2_warmup,
        min_spo2_score=args.min_spo2_score,
        min_valid_hr_bpm=args.min_valid_hr_bpm,
        max_valid_hr_bpm=args.max_valid_hr_bpm,
        hr_confirm_samples=args.hr_confirm_samples,
        hr_confirm_tolerance_bpm=args.hr_confirm_tolerance_bpm,
        hr_confirm_interval_ms=args.hr_confirm_interval_ms,
    )
    waveform_preview = (
        OledWaveformPreview(
            frames_dir=args.waveform_preview_dir,
            px_per_sec=args.waveform_px_per_sec,
            frame_ms=args.waveform_frame_ms,
            scale=args.waveform_scale,
            max_frames=args.waveform_max_frames,
            waveform_style=args.waveform_style,
        )
        if args.waveform_preview_dir
        else None
    )

    raw_file = None
    processed_file = None
    event_file = None
    raw_writer = None
    processed_writer = None
    event_writer = None
    ser = None
    input_file = None
    input_reader = None
    sample_rows = 0
    first_sample = None
    last_sample = None
    prev_t_ms = None
    prev_sample_time_ms = None
    prev_sample_count = None
    nonmonotonic_t_ms = 0
    nonmonotonic_sample_count = 0
    missing_sample_count = 0
    finger_present_samples = 0
    peak_count = 0
    raw_stats, processed_stats = make_stats()

    if args.input_csv:
        print(f"Replaying raw samples from {args.input_csv}", flush=True)
    else:
        print(f"Opening {port_name} at {args.baud} baud. Ctrl+C to stop.", flush=True)
    print(f"Session {session_id}, label={args.label}", flush=True)

    start = time.monotonic()
    capture_start = start
    last_status = 0.0

    try:
        if not args.no_save:
            raw_file = args.csv.open("w", newline="")
            raw_writer = csv.DictWriter(raw_file, fieldnames=RAW_CSV_FIELDS)
            raw_writer.writeheader()

            processed_file = processed_csv.open("w", newline="")
            processed_writer = csv.DictWriter(processed_file, fieldnames=PROCESSED_CSV_FIELDS)
            processed_writer.writeheader()

            event_file = event_csv.open("w", newline="")
            event_writer = csv.DictWriter(event_file, fieldnames=EVENT_CSV_FIELDS)
            event_writer.writeheader()
            for event in events:
                event_writer.writerow(
                    {
                        "session_id": session_id,
                        "pc_time": dt.datetime.now().isoformat(timespec="milliseconds"),
                        "event_s": event["event_s"],
                        "event_label": event["event_label"],
                    }
                )
            event_file.flush()

            print(f"Saving raw samples to {args.csv}", flush=True)
            print(f"Saving processed samples to {processed_csv}", flush=True)
            print(f"Saving event markers to {event_csv}", flush=True)

        if args.input_csv:
            input_file = args.input_csv.open(newline="")
            input_reader = csv.DictReader(input_file)
        else:
            ser = serial.Serial(
                port_name,
                args.baud,
                timeout=0.5,
                write_timeout=0.5,
                rtscts=False,
                dsrdtr=False,
            )
            ser.dtr = True
            ser.rts = True

        capture_start = time.monotonic()

        while True:
            if args.duration > 0.0 and (time.monotonic() - capture_start) >= args.duration:
                break

            if input_reader is not None:
                try:
                    input_row = next(input_reader)
                except StopIteration:
                    break
                try:
                    parsed = {
                        "t_ms": int(input_row["t_ms"]),
                        "red": int(input_row["red"]),
                        "ir": int(input_row["ir"]),
                        "irq_count": int(input_row["irq_count"]),
                        "sample_count": int(input_row["sample_count"]),
                    }
                except (KeyError, ValueError):
                    continue
            else:
                parsed = parse_device_line(ser.readline())
            if parsed is None or parsed == "header":
                continue

            pc_time = dt.datetime.now().isoformat(timespec="milliseconds")
            estimator.update(parsed)

            if first_sample is None:
                first_sample = parsed.copy()
            last_sample = parsed.copy()
            sample_rows += 1
            raw_stats["red"].update(parsed["red"])
            raw_stats["ir"].update(parsed["ir"])

            if prev_t_ms is not None:
                time_gap = parsed["t_ms"] - prev_t_ms
                processed_stats["time_gap_ms"].update(time_gap)
                if time_gap <= 0:
                    nonmonotonic_t_ms += 1
            prev_t_ms = parsed["t_ms"]

            sample_time_ms = parsed["sample_count"] * SAMPLE_INTERVAL_MS
            if prev_sample_time_ms is not None:
                processed_stats["sample_time_gap_ms"].update(sample_time_ms - prev_sample_time_ms)
            prev_sample_time_ms = sample_time_ms

            if prev_sample_count is not None:
                sample_gap = parsed["sample_count"] - prev_sample_count
                processed_stats["sample_count_gap"].update(sample_gap)
                if sample_gap <= 0:
                    nonmonotonic_sample_count += 1
                elif sample_gap > 1:
                    missing_sample_count += sample_gap - 1
            prev_sample_count = parsed["sample_count"]

            processed_row = estimator.processed_row(session_id, args.label, pc_time, parsed)
            for key in (
                "fs_hz",
                "red_dc",
                "ir_dc",
                "red_rms",
                "ir_rms",
                "red_filt",
                "ir_filt",
                "finger_score",
                "hr_bpm",
                "hr_peak_bpm",
                "hr_autocorr_bpm",
                "spo2",
                "ratio",
            ):
                processed_stats[key].update(processed_row[key])
            if processed_row["finger_present"]:
                finger_present_samples += 1
            if processed_row["peak"]:
                peak_count += 1
            if waveform_preview is not None:
                waveform_preview.update(parsed, estimator, processed_row)

            if raw_writer:
                raw_writer.writerow({"session_id": session_id, "label": args.label, "pc_time": pc_time, **parsed})
            if processed_writer:
                processed_writer.writerow(processed_row)
            if sample_rows % 50 == 0:
                if raw_file:
                    raw_file.flush()
                if processed_file:
                    processed_file.flush()
                if event_file:
                    event_file.flush()

            now = time.monotonic()
            if now - last_status >= 1.0:
                print(estimator.status_text(parsed), flush=True)
                last_status = now

    except KeyboardInterrupt:
        print("\nStopped by user.")
    finally:
        if ser is not None:
            try:
                if ser.is_open:
                    ser.dtr = False
                    ser.rts = False
                    time.sleep(0.05)
                    ser.close()
            except serial.SerialException:
                pass

        if raw_file:
            raw_file.flush()
            raw_file.close()
        if processed_file:
            processed_file.flush()
            processed_file.close()
        if event_file:
            event_file.flush()
            event_file.close()
        if input_file:
            input_file.close()
        if waveform_preview is not None:
            waveform_preview.write_summary()

        elapsed_s = time.monotonic() - start
        if not args.no_save:
            summary = {
                "session_id": session_id,
                "label": args.label,
                "notes": args.notes,
                "port": port_name,
                "baud": args.baud,
                "elapsed_s": round(elapsed_s, 3),
                "samples": sample_rows,
                "raw_csv": str(args.csv),
                "processed_csv": str(processed_csv),
                "event_csv": str(event_csv),
                "events": events,
                "parameters": {
                    "window_s": args.window,
                    "finger_ir_min": args.finger_ir_min,
                    "min_ac_rms": args.min_ac_rms,
                    "min_peak_ms": args.min_peak_ms,
                    "max_peak_ms": args.max_peak_ms,
                    "peak_detector": args.peak_detector,
                    "peak_base_threshold_ratio": args.peak_base_threshold_ratio,
                    "peak_dynamic_min_hr_ratio": args.peak_dynamic_min_hr_ratio,
                    "peak_recovery_start_hr_ratio": args.peak_recovery_start_hr_ratio,
                    "peak_recovery_threshold_ratio": args.peak_recovery_threshold_ratio,
                    "peak_candidate_hold_ms": args.peak_candidate_hold_ms,
                    "dc_alpha": args.dc_alpha,
                    "filt_alpha": args.filt_alpha,
                    "warmup_s": args.warmup,
                    "spo2_warmup_s": args.spo2_warmup,
                    "min_spo2_score": args.min_spo2_score,
                    "min_valid_hr_bpm": args.min_valid_hr_bpm,
                    "max_valid_hr_bpm": args.max_valid_hr_bpm,
                    "hr_confirm_samples": args.hr_confirm_samples,
                    "hr_confirm_tolerance_bpm": args.hr_confirm_tolerance_bpm,
                    "hr_confirm_interval_ms": args.hr_confirm_interval_ms,
                },
                "first_sample": first_sample,
                "last_sample": last_sample,
                "data_quality": {
                    "time_gap_ms": processed_stats["time_gap_ms"].as_dict(),
                    "sample_time_gap_ms": processed_stats["sample_time_gap_ms"].as_dict(),
                    "sample_count_gap": processed_stats["sample_count_gap"].as_dict(),
                    "nonmonotonic_t_ms": nonmonotonic_t_ms,
                    "nonmonotonic_sample_count": nonmonotonic_sample_count,
                    "missing_sample_count": missing_sample_count,
                },
                "raw_stats": {
                    "red": raw_stats["red"].as_dict(),
                    "ir": raw_stats["ir"].as_dict(),
                },
                "processed_stats": {
                    "fs_hz": processed_stats["fs_hz"].as_dict(),
                    "red_dc": processed_stats["red_dc"].as_dict(),
                    "ir_dc": processed_stats["ir_dc"].as_dict(),
                    "red_rms": processed_stats["red_rms"].as_dict(),
                    "ir_rms": processed_stats["ir_rms"].as_dict(),
                    "red_filt": processed_stats["red_filt"].as_dict(),
                    "ir_filt": processed_stats["ir_filt"].as_dict(),
                    "finger_score": processed_stats["finger_score"].as_dict(),
                    "hr_bpm": processed_stats["hr_bpm"].as_dict(),
                    "hr_peak_bpm": processed_stats["hr_peak_bpm"].as_dict(),
                    "hr_autocorr_bpm": processed_stats["hr_autocorr_bpm"].as_dict(),
                    "spo2": processed_stats["spo2"].as_dict(),
                    "ratio": processed_stats["ratio"].as_dict(5),
                },
                "detection": {
                    "finger_present_samples": finger_present_samples,
                    "finger_present_fraction": round((finger_present_samples / sample_rows), 4) if sample_rows else 0.0,
                    "peak_count": peak_count,
                },
                "final": {
                    "fs_hz": round_or_none(estimator.fs_hz),
                    "finger_present": estimator.finger_present,
                    "hr_bpm": round_or_none(estimator.hr_bpm),
                    "hr_peak_bpm": round_or_none(estimator.hr_peak_bpm),
                    "hr_autocorr_bpm": round_or_none(estimator.hr_autocorr_bpm),
                    "spo2": round_or_none(estimator.spo2),
                    "ratio": round_or_none(estimator.ratio, 5),
                },
            }
            summary_json.write_text(json.dumps(summary, indent=2), encoding="utf-8")
            print(f"Saved summary to {summary_json}", flush=True)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
