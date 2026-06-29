#!/usr/bin/env python3
# 中文说明：
#   电脑端复现“单片机实时 AF 风险显示逻辑”的验证脚本。
#   它不重新训练模型，而是读取 scripts/train_af_naive_bayes.py 导出的模型参数，
#   按单片机当前策略模拟 30 秒 PPI 窗口、10s/30s 快慢刷新、HR 一致性判断等流程。
#   主要用于验证：当前固件里的 AF 计算逻辑在本地 PPG、公开数据集、模拟测试组上的表现。
#   常看输出目录：training_dataset/reports/mcu_live_af_*。
"""Validate the MCU live AF-risk display policy on PC.

This script intentionally mirrors the STM32 live-display layer:

- collect pulse-to-pulse intervals only after HR/SpO2 are valid
- keep a ring of recent PPI values
- update every 10 seconds before a stable low-risk value, then every 30 seconds
- require at least 20 intervals and 30 seconds of PPI duration
- reject windows whose PPI-derived HR disagrees with current HR by >18 bpm
- display the trained Naive Bayes probability rounded to integer percent

It does not retrain the model. It loads the same JSON weights exported by
scripts/train_af_naive_bayes.py and uses the same RR/PPI features.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import random
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()

import simulate_af_ppi_risk as sim
import train_af_naive_bayes as afnb


AF_MAX_PPI_COUNT = 100
AF_MIN_PPI_COUNT = 20
AF_WINDOW_TARGET_MS = 30_000
AF_RISK_FAST_STEP_S = 10.0
AF_RISK_SLOW_STEP_S = 30.0
AF_STABLE_RISK_THRESHOLD = 20
AF_MAX_UP_JUMP_PERCENT = 20
AF_LIVE_HR_TOLERANCE_BPM = 18.0
PPI_FILTER_MIN_HR_RATIO = 0.70
PPI_FILTER_MAX_HR_RATIO = 1.45
PPI_FILTER_SLOW_MIN_HR_RATIO = 0.58
PPI_FILTER_SLOW_MAX_HR_RATIO = 1.65
PPI_FILTER_FAST_MIN_HR_RATIO = 0.64
PPI_FILTER_FAST_MAX_HR_RATIO = 1.38
PPI_ARTIFACT_FRAC_GATE = 0.20
PPI_QUALITY_MIN_INTERVALS = 6
HR_JUMP_GATE_BPM = 25.0
HR_JUMP_ACCEPT_S = 10.0


@dataclass
class PpiEvent:
    time_s: float
    interval_ms: float
    hr_bpm: float | None = None
    valid_vitals: bool = True
    rhythm: str = ""


@dataclass
class LiveRiskPoint:
    source: str
    run_id: str
    update_idx: int
    time_s: float
    risk_percent: int | None
    reason: str
    ppi_count: int
    ppi_duration_ms: int
    ppi_hr_bpm: float | None
    current_hr_bpm: float | None
    hr_diff_bpm: float | None
    expected_label: str = ""
    af_fraction: float | None = None
    normal_fraction: float | None = None
    outlier_frac: float = 0.0
    short_long_pair_frac: float = 0.0
    alternating_large_delta_frac: float = 0.0
    p95_abs_delta_ms: float = 0.0
    max_abs_delta_ms: float = 0.0
    ppi_candidate_count: int = 0
    ppi_rejected_count: int = 0
    ppi_artifact_frac: float = 0.0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", type=Path, default=Path("training_dataset/models/af_nb_model.json"))
    parser.add_argument("--main-c", type=Path, default=Path("Core/Src/main.c"))
    parser.add_argument("--out-dir", type=Path, default=Path("training_dataset/reports/mcu_live_af"))
    parser.add_argument("--ppg-glob", default="testing dataset/1[0-2]_*/processed.csv")
    parser.add_argument("--sim-duration-s", type=float, default=120.0)
    parser.add_argument("--sim-runs", type=int, default=80)
    parser.add_argument("--seed", type=int, default=411)
    parser.add_argument("--include-public", action="store_true", help="Run downloaded AFDB/LTAFDB/NSR2DB streams")
    parser.add_argument(
        "--public-datasets",
        default="afdb,ltafdb,nsr2db",
        help="Comma-separated public datasets to stream when --include-public is set",
    )
    parser.add_argument(
        "--public-record-limit",
        type=int,
        default=0,
        help="Optional per-dataset record limit for quick checks; 0 means all records",
    )
    parser.add_argument(
        "--quality-gate-experiment",
        action="store_true",
        help="Also write candidate PPI quality-gate comparison reports from the generated windows",
    )
    parser.add_argument(
        "--disable-ppi-filter",
        action="store_true",
        help="Disable HR-referenced PPI filtering and artifact-fraction display gating.",
    )
    parser.add_argument("--ppi-min-hr-ratio", type=float, default=PPI_FILTER_MIN_HR_RATIO)
    parser.add_argument("--ppi-max-hr-ratio", type=float, default=PPI_FILTER_MAX_HR_RATIO)
    parser.add_argument("--ppi-slow-min-hr-ratio", type=float, default=PPI_FILTER_SLOW_MIN_HR_RATIO)
    parser.add_argument("--ppi-slow-max-hr-ratio", type=float, default=PPI_FILTER_SLOW_MAX_HR_RATIO)
    parser.add_argument("--ppi-fast-min-hr-ratio", type=float, default=PPI_FILTER_FAST_MIN_HR_RATIO)
    parser.add_argument("--ppi-fast-max-hr-ratio", type=float, default=PPI_FILTER_FAST_MAX_HR_RATIO)
    parser.add_argument("--ppi-quality-min-intervals", type=int, default=PPI_QUALITY_MIN_INTERVALS)
    parser.add_argument("--ppi-artifact-frac-gate", type=float, default=PPI_ARTIFACT_FRAC_GATE)
    parser.add_argument("--hr-jump-gate-bpm", type=float, default=HR_JUMP_GATE_BPM)
    parser.add_argument("--hr-jump-accept-s", type=float, default=HR_JUMP_ACCEPT_S)
    parser.add_argument("--af-fast-step-s", type=float, default=AF_RISK_FAST_STEP_S)
    parser.add_argument("--af-slow-step-s", type=float, default=AF_RISK_SLOW_STEP_S)
    parser.add_argument("--af-stable-risk-threshold", type=int, default=AF_STABLE_RISK_THRESHOLD)
    parser.add_argument("--af-max-up-jump-percent", type=int, default=AF_MAX_UP_JUMP_PERCENT)
    return parser.parse_args()


def c_round_to_int(value: float) -> int:
    if value >= 0.0:
        return int(value + 0.5)
    return int(value - 0.5)


def summarize(values: list[float]) -> dict[str, float | int | None]:
    if not values:
        return {"count": 0, "min": None, "p25": None, "median": None, "mean": None, "p75": None, "max": None}
    return {
        "count": len(values),
        "min": round(min(values), 3),
        "p25": round(afnb.percentile(values, 0.25), 3),
        "median": round(afnb.median(values), 3),
        "mean": round(sum(values) / len(values), 3),
        "p75": round(afnb.percentile(values, 0.75), 3),
        "max": round(max(values), 3),
    }


def intervals_to_events(
    intervals_ms: list[float],
    hr_bpm: float | None = None,
    rhythm: str = "",
) -> list[PpiEvent]:
    events: list[PpiEvent] = []
    time_s = 0.0
    for interval_ms in intervals_ms:
        interval_ms = float(interval_ms)
        time_s += interval_ms / 1000.0
        events.append(PpiEvent(time_s=time_s, interval_ms=interval_ms, hr_bpm=hr_bpm, rhythm=rhythm))
    return events


def latest_window(intervals_ms: list[int]) -> list[int]:
    duration = 0
    selected: list[int] = []
    for value in reversed(intervals_ms):
        selected.append(value)
        duration += value
        if duration >= AF_WINDOW_TARGET_MS:
            break
    selected.reverse()
    return selected


def latest_window_pairs(ring: list[tuple[int, str]]) -> list[tuple[int, str]]:
    duration = 0
    selected: list[tuple[int, str]] = []
    for value, rhythm in reversed(ring):
        selected.append((value, rhythm))
        duration += value
        if duration >= AF_WINDOW_TARGET_MS:
            break
    selected.reverse()
    return selected


def classify_rhythms(rhythms: list[str]) -> tuple[str, float | None, float | None]:
    clean = [rhythm.upper() for rhythm in rhythms if rhythm]
    if not clean:
        return "", None, None
    total = len(clean)
    af_count = sum(1 for rhythm in clean if rhythm.startswith("AFIB") or rhythm == "AF")
    normal_count = sum(1 for rhythm in clean if rhythm in {"N", "NORMAL"})
    af_fraction = af_count / total
    normal_fraction = normal_count / total
    if af_fraction >= 0.80:
        return "af", af_fraction, normal_fraction
    if normal_fraction >= 0.80:
        return "normal", af_fraction, normal_fraction
    if af_count > 0 or normal_count > 0:
        return "mixed", af_fraction, normal_fraction
    return "other", af_fraction, normal_fraction


def quality_metrics_from_ppi(ppi_ms: list[int]) -> dict[str, float]:
    if len(ppi_ms) < 2:
        return {
            "outlier_frac": 0.0,
            "short_long_pair_frac": 0.0,
            "alternating_large_delta_frac": 0.0,
            "p95_abs_delta_ms": 0.0,
            "max_abs_delta_ms": 0.0,
        }

    intervals = [float(v) for v in ppi_ms]
    median_ppi = afnb.median(intervals)
    deltas = [intervals[i] - intervals[i - 1] for i in range(1, len(intervals))]
    abs_deltas = [abs(v) for v in deltas]

    if median_ppi <= 0.0:
        outlier_frac = 0.0
        short_long_pair_frac = 0.0
        alternating_large_delta_frac = 0.0
    else:
        outlier_low = median_ppi * 0.65
        outlier_high = median_ppi * 1.55
        outlier_frac = sum(1 for v in intervals if v < outlier_low or v > outlier_high) / len(intervals)

        short_thr = median_ppi * 0.78
        long_thr = median_ppi * 1.25
        short_long_pair_frac = sum(
            1
            for i in range(len(intervals) - 1)
            if (intervals[i] < short_thr and intervals[i + 1] > long_thr)
            or (intervals[i] > long_thr and intervals[i + 1] < short_thr)
        ) / max(1, len(intervals) - 1)

        large_delta = median_ppi * 0.22
        signs: list[int] = []
        for delta in deltas:
            if abs(delta) >= large_delta:
                signs.append(1 if delta > 0.0 else -1)
            else:
                signs.append(0)
        alternating = 0
        comparable = 0
        for i in range(1, len(signs)):
            if signs[i - 1] != 0 and signs[i] != 0:
                comparable += 1
                if signs[i - 1] != signs[i]:
                    alternating += 1
        alternating_large_delta_frac = alternating / comparable if comparable else 0.0

    return {
        "outlier_frac": outlier_frac,
        "short_long_pair_frac": short_long_pair_frac,
        "alternating_large_delta_frac": alternating_large_delta_frac,
        "p95_abs_delta_ms": afnb.percentile(abs_deltas, 0.95),
        "max_abs_delta_ms": max(abs_deltas) if abs_deltas else 0.0,
    }


def ppi_ratio_limits(current_hr_bpm: float, args: argparse.Namespace) -> tuple[float, float]:
    if current_hr_bpm < 65.0:
        return args.ppi_slow_min_hr_ratio, args.ppi_slow_max_hr_ratio
    if current_hr_bpm > 115.0:
        return args.ppi_fast_min_hr_ratio, args.ppi_fast_max_hr_ratio
    return args.ppi_min_hr_ratio, args.ppi_max_hr_ratio


def ppi_is_usable(interval_ms: int, current_hr_bpm: float | None, args: argparse.Namespace) -> bool:
    if not (300 <= interval_ms <= 2200):
        return False
    if args.disable_ppi_filter or current_hr_bpm is None or current_hr_bpm <= 0.0:
        return True
    expected_ppi_ms = 60000.0 / current_hr_bpm
    min_ratio, max_ratio = ppi_ratio_limits(current_hr_bpm, args)
    return (
        interval_ms >= expected_ppi_ms * min_ratio
        and interval_ms <= expected_ppi_ms * max_ratio
    )


def ppi_quality_ok(candidate_count: int, rejected_count: int, args: argparse.Namespace) -> bool:
    if args.disable_ppi_filter:
        return True
    if candidate_count < args.ppi_quality_min_intervals:
        return True
    return (rejected_count / candidate_count) <= args.ppi_artifact_frac_gate


def next_display_hr(
    current_hr_bpm: float | None,
    new_hr_bpm: float | None,
    event_time_s: float,
    last_hr_accept_s: float | None,
    args: argparse.Namespace,
) -> tuple[float | None, float | None]:
    if new_hr_bpm is None or new_hr_bpm <= 0.0:
        return current_hr_bpm, last_hr_accept_s
    if current_hr_bpm is None or current_hr_bpm <= 0.0:
        return new_hr_bpm, event_time_s
    if abs(new_hr_bpm - current_hr_bpm) <= args.hr_jump_gate_bpm:
        return new_hr_bpm, event_time_s
    if last_hr_accept_s is not None and (event_time_s - last_hr_accept_s) >= args.hr_jump_accept_s:
        return new_hr_bpm, event_time_s
    return current_hr_bpm, last_hr_accept_s


def af_update_interval_s(displayed_risk: int | None, args: argparse.Namespace) -> float:
    if displayed_risk is not None and displayed_risk < args.af_stable_risk_threshold:
        return args.af_slow_step_s
    return args.af_fast_step_s


def apply_af_display_policy(
    displayed_risk: int | None,
    candidate_risk: int | None,
    reason: str,
    args: argparse.Namespace,
) -> tuple[int | None, str]:
    if candidate_risk is None:
        if displayed_risk is not None:
            return displayed_risk, f"hold_{reason}"
        return None, reason
    if displayed_risk is not None and candidate_risk > displayed_risk + args.af_max_up_jump_percent:
        return displayed_risk, "hold_af_up_jump"
    return candidate_risk, reason


def risk_from_ppi(model: dict[str, Any], ppi_ms: list[int]) -> int | None:
    features = afnb.compute_features([float(v) for v in ppi_ms])
    if features is None:
        return None
    risk, _ = afnb.predict_one(model, features)
    risk = max(0.0, min(100.0, risk))
    return c_round_to_int(risk)


def derived_hr_from_ppi(ppi_ms: list[int]) -> float | None:
    duration = sum(ppi_ms)
    if not ppi_ms or duration <= 0:
        return None
    return 60000.0 / (duration / len(ppi_ms))


def evaluate_live_risk(
    model: dict[str, Any],
    intervals_ms: list[int],
    current_hr_bpm: float | None,
) -> tuple[int | None, str, int, int, float | None, float | None]:
    ppi_ms = latest_window(intervals_ms)
    duration = sum(ppi_ms)
    if len(ppi_ms) < AF_MIN_PPI_COUNT:
        return None, "not_enough_ppi", len(ppi_ms), duration, None, None
    if duration < AF_WINDOW_TARGET_MS:
        return None, "window_short", len(ppi_ms), duration, None, None

    ppi_hr_bpm = derived_hr_from_ppi(ppi_ms)
    if ppi_hr_bpm is None:
        return None, "bad_ppi_hr", len(ppi_ms), duration, None, None

    if current_hr_bpm is None or current_hr_bpm <= 0.0:
        current_hr_bpm = ppi_hr_bpm
    hr_diff = abs(ppi_hr_bpm - current_hr_bpm)
    if hr_diff > AF_LIVE_HR_TOLERANCE_BPM:
        return None, "hr_mismatch", len(ppi_ms), duration, ppi_hr_bpm, hr_diff

    risk = risk_from_ppi(model, ppi_ms)
    if risk is None:
        return None, "feature_fail", len(ppi_ms), duration, ppi_hr_bpm, hr_diff
    return risk, "ok", len(ppi_ms), duration, ppi_hr_bpm, hr_diff


def run_mcu_live(
    source: str,
    run_id: str,
    events: list[PpiEvent],
    model: dict[str, Any],
    args: argparse.Namespace,
) -> list[LiveRiskPoint]:
    ring: list[tuple[int, str]] = []
    points: list[LiveRiskPoint] = []
    last_update_s: float | None = None
    update_idx = 0
    current_hr_bpm: float | None = None
    last_hr_accept_s: float | None = None
    displayed_risk: int | None = None
    accepted_since_update = 0
    rejected_since_update = 0

    for event in events:
        if not event.valid_vitals:
            ring.clear()
            current_hr_bpm = None
            last_hr_accept_s = None
            last_update_s = None
            displayed_risk = None
            accepted_since_update = 0
            rejected_since_update = 0
            continue

        current_hr_bpm, last_hr_accept_s = next_display_hr(
            current_hr_bpm,
            event.hr_bpm,
            event.time_s,
            last_hr_accept_s,
            args,
        )

        interval = c_round_to_int(event.interval_ms)
        usable_ppi = ppi_is_usable(interval, current_hr_bpm, args)
        if usable_ppi:
            accepted_since_update += 1
            ring.append((interval, event.rhythm))
            if len(ring) > AF_MAX_PPI_COUNT:
                ring = ring[-AF_MAX_PPI_COUNT:]
        else:
            rejected_since_update += 1

        update_interval_s = af_update_interval_s(displayed_risk, args)
        due = last_update_s is None or (event.time_s - last_update_s) >= update_interval_s
        if not due:
            continue

        selected = latest_window_pairs(ring)
        ppi_values = [value for value, _rhythm in selected]
        expected_label, af_fraction, normal_fraction = classify_rhythms(
            [rhythm for _value, rhythm in selected]
        )
        quality_metrics = quality_metrics_from_ppi(ppi_values)
        risk, reason, ppi_count, ppi_duration, ppi_hr, hr_diff = evaluate_live_risk(
            model,
            ppi_values,
            current_hr_bpm,
        )
        ppi_candidate_count = accepted_since_update + rejected_since_update
        ppi_rejected_count = rejected_since_update
        ppi_artifact_frac = ppi_rejected_count / ppi_candidate_count if ppi_candidate_count else 0.0
        if not ppi_quality_ok(ppi_candidate_count, ppi_rejected_count, args):
            risk = None
            reason = "ppi_artifact_frac"
        risk, reason = apply_af_display_policy(displayed_risk, risk, reason, args)
        displayed_risk = risk
        points.append(
            LiveRiskPoint(
                source=source,
                run_id=run_id,
                update_idx=update_idx,
                time_s=event.time_s,
                risk_percent=risk,
                reason=reason,
                ppi_count=ppi_count,
                ppi_duration_ms=ppi_duration,
                ppi_hr_bpm=ppi_hr,
                current_hr_bpm=current_hr_bpm,
                hr_diff_bpm=hr_diff,
                expected_label=expected_label,
                af_fraction=af_fraction,
                normal_fraction=normal_fraction,
                outlier_frac=quality_metrics["outlier_frac"],
                short_long_pair_frac=quality_metrics["short_long_pair_frac"],
                alternating_large_delta_frac=quality_metrics["alternating_large_delta_frac"],
                p95_abs_delta_ms=quality_metrics["p95_abs_delta_ms"],
                max_abs_delta_ms=quality_metrics["max_abs_delta_ms"],
                ppi_candidate_count=ppi_candidate_count,
                ppi_rejected_count=ppi_rejected_count,
                ppi_artifact_frac=ppi_artifact_frac,
            )
        )
        update_idx += 1
        last_update_s = event.time_s
        accepted_since_update = 0
        rejected_since_update = 0

    return points


def run_mcu_test_playback(
    source: str,
    run_id: str,
    intervals_ms: list[float],
    model: dict[str, Any],
) -> list[LiveRiskPoint]:
    window: list[int] = []
    points: list[LiveRiskPoint] = []
    time_s = 0.0

    for update_idx, interval_ms in enumerate(intervals_ms):
        interval = c_round_to_int(float(interval_ms))
        time_s += interval / 1000.0
        if 300 <= interval <= 2200:
            window.append(interval)
            if len(window) > AF_MAX_PPI_COUNT:
                window = window[-AF_MAX_PPI_COUNT:]

        duration = sum(window)
        if len(window) < AF_MIN_PPI_COUNT:
            risk = None
            reason = "not_enough_ppi"
        elif duration < AF_WINDOW_TARGET_MS:
            risk = None
            reason = "window_short"
        else:
            risk = risk_from_ppi(model, window)
            reason = "ok" if risk is not None else "feature_fail"

        quality_metrics = quality_metrics_from_ppi(window)
        points.append(
            LiveRiskPoint(
                source=source,
                run_id=run_id,
                update_idx=update_idx,
                time_s=time_s,
                risk_percent=risk,
                reason=reason,
                ppi_count=len(window),
                ppi_duration_ms=duration,
                ppi_hr_bpm=derived_hr_from_ppi(window),
                current_hr_bpm=None,
                hr_diff_bpm=None,
                expected_label="af",
                af_fraction=1.0,
                normal_fraction=0.0,
                outlier_frac=quality_metrics["outlier_frac"],
                short_long_pair_frac=quality_metrics["short_long_pair_frac"],
                alternating_large_delta_frac=quality_metrics["alternating_large_delta_frac"],
                p95_abs_delta_ms=quality_metrics["p95_abs_delta_ms"],
                max_abs_delta_ms=quality_metrics["max_abs_delta_ms"],
            )
        )

    return points


def load_ppg_events(processed_csv: Path) -> list[PpiEvent]:
    events: list[PpiEvent] = []
    first_t_ms: float | None = None
    last_peak_t_ms: float | None = None

    def positive(row: dict[str, str], key: str) -> float | None:
        try:
            value = float(str(row.get(key, "")).strip())
        except ValueError:
            return None
        if math.isfinite(value) and value > 0.0:
            return value
        return None

    with processed_csv.open("r", newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            try:
                t_ms = float(row.get("t_ms") or "nan")
            except ValueError:
                continue
            if not math.isfinite(t_ms):
                continue
            if first_t_ms is None:
                first_t_ms = t_ms

            if str(row.get("finger_present", "")).strip() not in {"1", "1.0", "True", "true"}:
                continue

            hr_bpm = positive(row, "hr_bpm")
            spo2 = positive(row, "spo2")
            if hr_bpm is None or spo2 is None:
                continue

            if str(row.get("peak", "")).strip() not in {"1", "1.0", "True", "true"}:
                continue

            interval_ms: float | None = None
            text = str(row.get("peak_interval_ms", "")).strip()
            if text:
                try:
                    interval_ms = float(text)
                except ValueError:
                    interval_ms = None
            if interval_ms is None and last_peak_t_ms is not None:
                interval_ms = t_ms - last_peak_t_ms
            last_peak_t_ms = t_ms

            if interval_ms is None:
                continue
            events.append(
                PpiEvent(
                    time_s=(t_ms - first_t_ms) / 1000.0,
                    interval_ms=interval_ms,
                    hr_bpm=hr_bpm,
                    valid_vitals=True,
                    rhythm="N",
                )
            )
    return events


def parse_mcu_test_ppi(main_c: Path) -> list[float]:
    text = main_c.read_text(encoding="utf-8", errors="ignore")
    match = re.search(r"static\s+const\s+uint16_t\s+af_test_ppi_ms\[\]\s*=\s*\{(.*?)\};", text, re.S)
    if not match:
        raise ValueError(f"cannot find af_test_ppi_ms[] in {main_c}")
    return [float(value) for value in re.findall(r"(\d+)U", match.group(1))]


def public_dataset_dir(base_dir: Path, dataset: str) -> Path:
    return base_dir / "physionet" / dataset / "1.0.0"


def load_public_record_events(base_dir: Path, dataset: str, record: str) -> list[PpiEvent]:
    dataset_dir = public_dataset_dir(base_dir, dataset)
    interval_events = afnb.events_from_record(dataset, dataset_dir, record)
    return [
        PpiEvent(
            time_s=event.time_s,
            interval_ms=event.interval_ms,
            hr_bpm=None,
            valid_vitals=True,
            rhythm=event.rhythm,
        )
        for event in interval_events
    ]


def load_public_points(args: argparse.Namespace, model: dict[str, Any]) -> list[LiveRiskPoint]:
    points: list[LiveRiskPoint] = []
    base_dir = args.model.parent.parent
    datasets = [item.strip() for item in args.public_datasets.split(",") if item.strip()]

    for dataset in datasets:
        dataset_dir = public_dataset_dir(base_dir, dataset)
        records_path = dataset_dir / "RECORDS"
        if not records_path.exists():
            print(f"WARN: skip public_{dataset}, missing {records_path}")
            continue
        records = afnb.read_records_file(records_path)
        if args.public_record_limit > 0:
            records = records[: args.public_record_limit]

        for record in records:
            try:
                events = load_public_record_events(base_dir, dataset, record)
                points.extend(run_mcu_live(f"public_{dataset}", record, events, model, args))
            except Exception as exc:
                print(f"WARN: failed public_{dataset}/{record}: {exc}")

    return points


def quality_gate_variants() -> dict[str, Any]:
    def no_gate(_point: LiveRiskPoint) -> tuple[bool, str]:
        return False, ""

    def short_long_25(point: LiveRiskPoint) -> tuple[bool, str]:
        reject = point.short_long_pair_frac > 0.25
        return reject, "short_long_pair" if reject else ""

    def short_long_18(point: LiveRiskPoint) -> tuple[bool, str]:
        reject = point.short_long_pair_frac > 0.18
        return reject, "short_long_pair" if reject else ""

    def outlier_18(point: LiveRiskPoint) -> tuple[bool, str]:
        reject = point.outlier_frac > 0.18
        return reject, "outlier_frac" if reject else ""

    def outlier_12(point: LiveRiskPoint) -> tuple[bool, str]:
        reject = point.outlier_frac > 0.12
        return reject, "outlier_frac" if reject else ""

    def artifact_mild(point: LiveRiskPoint) -> tuple[bool, str]:
        if point.outlier_frac > 0.22:
            return True, "outlier_frac"
        if point.short_long_pair_frac > 0.30:
            return True, "short_long_pair"
        if point.alternating_large_delta_frac > 0.80 and point.short_long_pair_frac > 0.12:
            return True, "alternating_pair"
        return False, ""

    def artifact_medium(point: LiveRiskPoint) -> tuple[bool, str]:
        if point.outlier_frac > 0.16:
            return True, "outlier_frac"
        if point.short_long_pair_frac > 0.22:
            return True, "short_long_pair"
        if point.alternating_large_delta_frac > 0.70 and point.short_long_pair_frac > 0.10:
            return True, "alternating_pair"
        return False, ""

    def artifact_strict(point: LiveRiskPoint) -> tuple[bool, str]:
        if point.outlier_frac > 0.10:
            return True, "outlier_frac"
        if point.short_long_pair_frac > 0.16:
            return True, "short_long_pair"
        if point.alternating_large_delta_frac > 0.65 and point.short_long_pair_frac > 0.08:
            return True, "alternating_pair"
        return False, ""

    return {
        "baseline_no_gate": no_gate,
        "short_long_pair_gt25pct": short_long_25,
        "short_long_pair_gt18pct": short_long_18,
        "outlier_frac_gt18pct": outlier_18,
        "outlier_frac_gt12pct": outlier_12,
        "artifact_mild": artifact_mild,
        "artifact_medium": artifact_medium,
        "artifact_strict": artifact_strict,
    }


def write_quality_gate_reports(out_dir: Path, points: list[LiveRiskPoint]) -> dict[str, str]:
    candidate_points = [
        point
        for point in points
        if point.source.startswith("public_") and point.reason == "ok" and point.risk_percent is not None
    ]
    rows: list[dict[str, Any]] = []

    for variant_name, gate in quality_gate_variants().items():
        groups: dict[tuple[str, str], list[LiveRiskPoint]] = defaultdict(list)
        for point in candidate_points:
            groups[(point.source, point.expected_label or "unknown")].append(point)
            groups[("ALL_PUBLIC", point.expected_label or "unknown")].append(point)

        for (source, label), items in sorted(groups.items()):
            displayed: list[LiveRiskPoint] = []
            reject_reasons: dict[str, int] = defaultdict(int)
            for point in items:
                rejected, reason = gate(point)
                if rejected:
                    reject_reasons[reason or "rejected"] += 1
                else:
                    displayed.append(point)

            high50 = sum(1 for point in displayed if point.risk_percent is not None and point.risk_percent >= 50)
            high80 = sum(1 for point in displayed if point.risk_percent is not None and point.risk_percent >= 80)
            risks = [float(point.risk_percent) for point in displayed if point.risk_percent is not None]
            rows.append(
                {
                    "variant": variant_name,
                    "source": source,
                    "label": label,
                    "total_windows": len(items),
                    "displayed_windows": len(displayed),
                    "rejected_windows": len(items) - len(displayed),
                    "display_rate_pct": round(100.0 * len(displayed) / len(items), 3) if items else 0.0,
                    "ge50_all_pct": round(100.0 * high50 / len(items), 3) if items else 0.0,
                    "ge80_all_pct": round(100.0 * high80 / len(items), 3) if items else 0.0,
                    "ge50_displayed_pct": round(100.0 * high50 / len(displayed), 3) if displayed else None,
                    "ge80_displayed_pct": round(100.0 * high80 / len(displayed), 3) if displayed else None,
                    "risk_median_displayed": round(afnb.median(risks), 3) if risks else None,
                    "risk_mean_displayed": round(sum(risks) / len(risks), 3) if risks else None,
                    "reject_reasons": json.dumps(dict(reject_reasons), ensure_ascii=False),
                }
            )

    summary_path = out_dir / "quality_gate_variant_summary.csv"
    compact_path = out_dir / "quality_gate_variant_summary_compact.csv"

    if rows:
        with summary_path.open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
            writer.writeheader()
            writer.writerows(rows)

        compact = [
            row
            for row in rows
            if row["source"] == "ALL_PUBLIC" and row["label"] in {"af", "normal", "mixed", "other"}
        ]
        with compact_path.open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=list(compact[0].keys()))
            writer.writeheader()
            writer.writerows(compact)

    return {
        "quality_gate_summary_csv": str(summary_path),
        "quality_gate_compact_csv": str(compact_path),
    }


def write_reports(out_dir: Path, points: list[LiveRiskPoint], config: dict[str, Any]) -> dict[str, Any]:
    out_dir.mkdir(parents=True, exist_ok=True)
    detail_path = out_dir / "mcu_live_af_detail.csv"
    summary_path = out_dir / "mcu_live_af_summary.csv"
    label_summary_path = out_dir / "mcu_live_af_label_summary.csv"
    json_path = out_dir / "mcu_live_af_summary.json"

    detail_fields = [
        "source",
        "run_id",
        "update_idx",
        "time_s",
        "risk_percent",
        "reason",
        "ppi_count",
        "ppi_duration_ms",
        "ppi_hr_bpm",
        "current_hr_bpm",
        "hr_diff_bpm",
        "expected_label",
        "af_fraction",
        "normal_fraction",
        "outlier_frac",
        "short_long_pair_frac",
        "alternating_large_delta_frac",
        "p95_abs_delta_ms",
        "max_abs_delta_ms",
        "ppi_candidate_count",
        "ppi_rejected_count",
        "ppi_artifact_frac",
    ]
    with detail_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=detail_fields)
        writer.writeheader()
        for point in points:
            writer.writerow(
                {
                    "source": point.source,
                    "run_id": point.run_id,
                    "update_idx": point.update_idx,
                    "time_s": round(point.time_s, 3),
                    "risk_percent": "" if point.risk_percent is None else point.risk_percent,
                    "reason": point.reason,
                    "ppi_count": point.ppi_count,
                    "ppi_duration_ms": point.ppi_duration_ms,
                    "ppi_hr_bpm": "" if point.ppi_hr_bpm is None else round(point.ppi_hr_bpm, 3),
                    "current_hr_bpm": "" if point.current_hr_bpm is None else round(point.current_hr_bpm, 3),
                    "hr_diff_bpm": "" if point.hr_diff_bpm is None else round(point.hr_diff_bpm, 3),
                    "expected_label": point.expected_label,
                    "af_fraction": "" if point.af_fraction is None else round(point.af_fraction, 6),
                    "normal_fraction": "" if point.normal_fraction is None else round(point.normal_fraction, 6),
                    "outlier_frac": round(point.outlier_frac, 6),
                    "short_long_pair_frac": round(point.short_long_pair_frac, 6),
                    "alternating_large_delta_frac": round(point.alternating_large_delta_frac, 6),
                    "p95_abs_delta_ms": round(point.p95_abs_delta_ms, 3),
                    "max_abs_delta_ms": round(point.max_abs_delta_ms, 3),
                    "ppi_candidate_count": point.ppi_candidate_count,
                    "ppi_rejected_count": point.ppi_rejected_count,
                    "ppi_artifact_frac": round(point.ppi_artifact_frac, 6),
                }
            )

    grouped: dict[str, list[LiveRiskPoint]] = defaultdict(list)
    for point in points:
        grouped[point.source].append(point)

    summary_rows: list[dict[str, Any]] = []
    summary_json: dict[str, Any] = {
        "config": config,
        "sources": {},
        "labels": {},
        "detail_csv": str(detail_path),
    }
    for source, items in sorted(grouped.items()):
        valid = [float(item.risk_percent) for item in items if item.risk_percent is not None]
        reasons = Counter(item.reason for item in items)
        first_valid_times = []
        by_run: dict[str, list[LiveRiskPoint]] = defaultdict(list)
        for item in items:
            by_run[item.run_id].append(item)
        for run_items in by_run.values():
            valid_times = [item.time_s for item in run_items if item.risk_percent is not None]
            if valid_times:
                first_valid_times.append(min(valid_times))

        risk_summary = summarize(valid)
        row = {
            "source": source,
            "runs": len(by_run),
            "updates": len(items),
            "valid_updates": len(valid),
            "risk_min": risk_summary["min"],
            "risk_p25": risk_summary["p25"],
            "risk_median": risk_summary["median"],
            "risk_mean": risk_summary["mean"],
            "risk_p75": risk_summary["p75"],
            "risk_max": risk_summary["max"],
            "ge50_pct": round(100.0 * sum(1 for v in valid if v >= 50.0) / len(valid), 3) if valid else None,
            "ge80_pct": round(100.0 * sum(1 for v in valid if v >= 80.0) / len(valid), 3) if valid else None,
            "first_valid_median_s": round(afnb.median(first_valid_times), 3) if first_valid_times else None,
            "reason_counts": json.dumps(dict(reasons), ensure_ascii=False),
        }
        summary_rows.append(row)
        summary_json["sources"][source] = row

    with summary_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(summary_rows[0].keys()) if summary_rows else [])
        if summary_rows:
            writer.writeheader()
            writer.writerows(summary_rows)

    label_groups: dict[tuple[str, str], list[LiveRiskPoint]] = defaultdict(list)
    for point in points:
        if point.expected_label:
            label_groups[(point.source, point.expected_label)].append(point)

    label_rows: list[dict[str, Any]] = []
    for (source, label), items in sorted(label_groups.items()):
        valid = [float(item.risk_percent) for item in items if item.risk_percent is not None]
        risk_summary = summarize(valid)
        row = {
            "source": source,
            "expected_label": label,
            "updates": len(items),
            "valid_updates": len(valid),
            "risk_min": risk_summary["min"],
            "risk_p25": risk_summary["p25"],
            "risk_median": risk_summary["median"],
            "risk_mean": risk_summary["mean"],
            "risk_p75": risk_summary["p75"],
            "risk_max": risk_summary["max"],
            "ge50_pct": round(100.0 * sum(1 for v in valid if v >= 50.0) / len(valid), 3) if valid else None,
            "ge80_pct": round(100.0 * sum(1 for v in valid if v >= 80.0) / len(valid), 3) if valid else None,
        }
        label_rows.append(row)
        summary_json["labels"][f"{source}/{label}"] = row

    with label_summary_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(label_rows[0].keys()) if label_rows else [])
        if label_rows:
            writer.writeheader()
            writer.writerows(label_rows)

    json_path.write_text(json.dumps(summary_json, indent=2, ensure_ascii=False), encoding="utf-8")
    return {
        "summary_csv": str(summary_path),
        "label_summary_csv": str(label_summary_path),
        "summary_json": str(json_path),
        "detail_csv": str(detail_path),
    }


def main() -> int:
    args = parse_args()
    model = json.loads(args.model.read_text(encoding="utf-8"))
    rng = random.Random(args.seed)
    all_points: list[LiveRiskPoint] = []

    for path in sorted(Path(".").glob(args.ppg_glob)):
        events = load_ppg_events(path)
        all_points.extend(run_mcu_live("local_ppg", str(path.parent.name), events, model, args))

    test_ppi = parse_mcu_test_ppi(args.main_c)
    all_points.extend(
        run_mcu_test_playback("mcu_test_afdb_06995", "stored_test_segment", test_ppi, model)
    )

    for scenario_name, _description, fn in sim.SCENARIOS:
        for run_idx in range(args.sim_runs):
            intervals = fn(rng, args.sim_duration_s)
            run_id = f"{scenario_name}_{run_idx:03d}"
            if scenario_name.startswith("normal_"):
                rhythm = "N"
            elif scenario_name.startswith("af_like_"):
                rhythm = "AFIB"
            else:
                rhythm = "OTHER"
            all_points.extend(
                run_mcu_live(f"sim_{scenario_name}", run_id, intervals_to_events(intervals, rhythm=rhythm), model, args)
            )

    if args.include_public:
        all_points.extend(load_public_points(args, model))

    config = {
        "validator_sha256": sha256_file(Path(__file__)),
        "model": str(args.model),
        "model_sha256": sha256_file(args.model),
        "main_c": str(args.main_c),
        "main_c_sha256": sha256_file(args.main_c),
        "ppg_glob": args.ppg_glob,
        "sim_duration_s": args.sim_duration_s,
        "sim_runs": args.sim_runs,
        "seed": args.seed,
        "include_public": args.include_public,
        "public_datasets": args.public_datasets,
        "public_record_limit": args.public_record_limit,
        "af_max_ppi_count": AF_MAX_PPI_COUNT,
        "af_min_ppi_count": AF_MIN_PPI_COUNT,
        "af_window_target_ms": AF_WINDOW_TARGET_MS,
        "af_fast_step_s": args.af_fast_step_s,
        "af_slow_step_s": args.af_slow_step_s,
        "af_stable_risk_threshold": args.af_stable_risk_threshold,
        "af_max_up_jump_percent": args.af_max_up_jump_percent,
        "af_live_hr_tolerance_bpm": AF_LIVE_HR_TOLERANCE_BPM,
        "hr_jump_gate_bpm": args.hr_jump_gate_bpm,
        "hr_jump_accept_s": args.hr_jump_accept_s,
        "ppi_filter_enabled": not args.disable_ppi_filter,
        "ppi_min_hr_ratio": args.ppi_min_hr_ratio,
        "ppi_max_hr_ratio": args.ppi_max_hr_ratio,
        "ppi_slow_min_hr_ratio": args.ppi_slow_min_hr_ratio,
        "ppi_slow_max_hr_ratio": args.ppi_slow_max_hr_ratio,
        "ppi_fast_min_hr_ratio": args.ppi_fast_min_hr_ratio,
        "ppi_fast_max_hr_ratio": args.ppi_fast_max_hr_ratio,
        "ppi_quality_min_intervals": args.ppi_quality_min_intervals,
        "ppi_artifact_frac_gate": args.ppi_artifact_frac_gate,
    }
    paths = write_reports(args.out_dir, all_points, config)
    if args.quality_gate_experiment:
        paths.update(write_quality_gate_reports(args.out_dir, all_points))

    print("MCU live AF validation summary")
    summary = json.loads(Path(paths["summary_json"]).read_text(encoding="utf-8"))["sources"]
    for source, row in summary.items():
        print(
            f"{source}: valid={row['valid_updates']}/{row['updates']} "
            f"median={row['risk_median']} mean={row['risk_mean']} "
            f"max={row['risk_max']} ge80={row['ge80_pct']}% "
            f"first_valid_s={row['first_valid_median_s']}"
        )
    print("")
    print(f"wrote {paths['summary_csv']}")
    print(f"wrote {paths['label_summary_csv']}")
    if args.quality_gate_experiment:
        print(f"wrote {paths['quality_gate_summary_csv']}")
        print(f"wrote {paths['quality_gate_compact_csv']}")
    print(f"wrote {paths['summary_json']}")
    print(f"wrote {paths['detail_csv']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
