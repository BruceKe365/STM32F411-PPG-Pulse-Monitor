#!/usr/bin/env python3
# 中文说明：
#   训练第一版“PPG-HRV 压力指数”模型。
#   输入优先使用 WESAD wrist BVP，通过峰值间期 PPI 计算 HRV 特征；
#   模型采用 Logistic Regression，导出 JSON 和 MCU 可移植 C 头文件。
#   输出的 stress_index 为 1-99：1-29 放松，30-59 正常，60-79 中等，80-99 偏高。
"""Train a lightweight PPG-HRV stress-index model from WESAD.

This script intentionally uses interval features only. It does not use ECG
morphology, EDA, respiration, or temperature, because the current STM32 device
only has MAX30102 PPG. WESAD is used as a calibration source for stress vs
non-stress, while local MAX30102 captures are used as normal-data smoke tests.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import pickle
import random
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
from scipy.signal import butter, filtfilt, find_peaks
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import roc_auc_score
from sklearn.preprocessing import StandardScaler


WESAD_WRIST_BVP_FS = 64.0
WESAD_CHEST_ECG_FS = 700.0
WESAD_LABEL_NAMES = {
    0: "undefined",
    1: "baseline",
    2: "stress",
    3: "amusement",
    4: "meditation",
}

MODEL_FEATURES = [
    "valid_interval_count",
    "mean_hr_bpm",
    "mean_ppi_ms",
    "sdnn_ms",
    "rmssd_ms",
    "sdsd_ms",
    "pnn20_pct",
    "pnn50_pct",
    "cv_ppi",
    "median_abs_delta_ms",
    "p80_abs_delta_ms",
    "p95_abs_delta_ms",
    "outlier_frac",
]

DEFAULT_LOCAL_PPG_GLOBS = [
    "testing dataset/10_*/processed.csv",
    "testing dataset/11_*/processed.csv",
    "testing dataset/12_*/processed.csv",
]


@dataclass
class PpiEvent:
    time_s: float
    interval_ms: float
    state: int | None = None


@dataclass
class StressWindow:
    source: str
    subject: str
    state_name: str
    start_s: float
    end_s: float
    label: int | None
    features: dict[str, float]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-dir", type=Path, default=Path("training_dataset"))
    parser.add_argument("--wesad-dir", type=Path, default=None, help="Directory containing WESAD/S*/S*.pkl")
    parser.add_argument("--window-s", type=float, default=60.0)
    parser.add_argument("--step-s", type=float, default=10.0)
    parser.add_argument("--min-intervals", type=int, default=45)
    parser.add_argument("--label-purity", type=float, default=0.80)
    parser.add_argument("--test-fraction", type=float, default=0.25)
    parser.add_argument("--seed", type=int, default=411)
    parser.add_argument("--signal", choices=("bvp", "ecg"), default="bvp", help="Training signal source")
    parser.add_argument(
        "--exclude-amusement",
        action="store_true",
        help="Exclude WESAD amusement instead of treating it as non-stress",
    )
    parser.add_argument(
        "--local-ppg-glob",
        action="append",
        default=None,
        help="Local processed CSV glob. May be supplied multiple times.",
    )
    parser.add_argument("--index-nonstress-target", type=float, default=45.0)
    parser.add_argument("--index-stress-target", type=float, default=82.0)
    parser.add_argument(
        "--display-index-bias",
        type=float,
        default=5.0,
        help="Uniform display offset added to the calibrated 1-99 stress index.",
    )
    return parser.parse_args()


def c_round(value: float) -> int:
    if value >= 0.0:
        return int(value + 0.5)
    return int(value - 0.5)


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def median(values: list[float]) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    mid = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[mid]
    return 0.5 * (ordered[mid - 1] + ordered[mid])


def percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    pos = (len(ordered) - 1) * clamp(pct, 0.0, 1.0)
    low = int(math.floor(pos))
    high = int(math.ceil(pos))
    if low == high:
        return ordered[low]
    frac = pos - low
    return ordered[low] * (1.0 - frac) + ordered[high] * frac


def summarize(values: list[float]) -> dict[str, float | int | None]:
    if not values:
        return {"count": 0, "min": None, "p25": None, "median": None, "mean": None, "p75": None, "max": None}
    return {
        "count": len(values),
        "min": round(min(values), 3),
        "p25": round(percentile(values, 0.25), 3),
        "median": round(median(values), 3),
        "mean": round(sum(values) / len(values), 3),
        "p75": round(percentile(values, 0.75), 3),
        "max": round(max(values), 3),
    }


def truthy(text: str | None) -> bool:
    if text is None:
        return False
    return text.strip().lower() in {"1", "true", "yes", "y"}


def stress_level(index: int | None) -> str:
    if index is None:
        return "--"
    if index <= 29:
        return "relaxed"
    if index <= 59:
        return "normal"
    if index <= 79:
        return "medium"
    return "high"


def compute_hrv_features(intervals_ms: list[float]) -> dict[str, float] | None:
    valid = [float(v) for v in intervals_ms if 300.0 <= float(v) <= 2200.0]
    if len(valid) < 2:
        return None

    deltas = [valid[i] - valid[i - 1] for i in range(1, len(valid))]
    abs_deltas = [abs(v) for v in deltas]
    mean_ppi = sum(valid) / len(valid)
    if mean_ppi <= 0.0:
        return None

    variance = sum((v - mean_ppi) ** 2 for v in valid) / max(1, len(valid) - 1)
    sdnn = math.sqrt(variance)
    rmssd = math.sqrt(sum(v * v for v in deltas) / len(deltas)) if deltas else 0.0
    mean_delta = sum(deltas) / len(deltas) if deltas else 0.0
    sdsd = math.sqrt(sum((v - mean_delta) ** 2 for v in deltas) / max(1, len(deltas) - 1)) if deltas else 0.0
    median_ppi = median(valid)
    outlier_low = median_ppi * 0.65
    outlier_high = median_ppi * 1.55
    outlier_count = sum(1 for v in valid if v < outlier_low or v > outlier_high)

    return {
        "valid_interval_count": float(len(valid)),
        "mean_hr_bpm": 60000.0 / mean_ppi,
        "mean_ppi_ms": mean_ppi,
        "sdnn_ms": sdnn,
        "rmssd_ms": rmssd,
        "sdsd_ms": sdsd,
        "pnn20_pct": 100.0 * sum(1 for v in abs_deltas if v > 20.0) / len(abs_deltas) if abs_deltas else 0.0,
        "pnn50_pct": 100.0 * sum(1 for v in abs_deltas if v > 50.0) / len(abs_deltas) if abs_deltas else 0.0,
        "cv_ppi": sdnn / mean_ppi,
        "median_abs_delta_ms": median(abs_deltas),
        "p80_abs_delta_ms": percentile(abs_deltas, 0.80),
        "p95_abs_delta_ms": percentile(abs_deltas, 0.95),
        "outlier_frac": outlier_count / len(valid),
    }


def find_wesad_pickles(wesad_dir: Path) -> list[Path]:
    candidates = [
        wesad_dir,
        wesad_dir / "WESAD",
        wesad_dir / "wesad",
    ]
    paths: list[Path] = []
    for root in candidates:
        if root.exists():
            paths.extend(sorted(root.glob("S*/S*.pkl")))
    unique = sorted({path.resolve(): path for path in paths}.values())
    return unique


def load_wesad_pickle(path: Path) -> dict[str, Any]:
    with path.open("rb") as f:
        return pickle.load(f, encoding="latin1")


def bandpass(values: np.ndarray, fs: float) -> np.ndarray:
    finite = np.asarray(values, dtype=float).reshape(-1)
    finite = np.nan_to_num(finite, nan=float(np.nanmedian(finite)))
    if finite.size < int(fs * 5.0):
        return finite - float(np.mean(finite))
    centered = finite - float(np.median(finite))
    nyq = fs * 0.5
    b, a = butter(2, [0.5 / nyq, 5.0 / nyq], btype="bandpass")
    return filtfilt(b, a, centered)


def plausible_peak_score(peaks: np.ndarray, fs: float) -> tuple[float, float]:
    if peaks.size < 4:
        return -1.0, 0.0
    intervals_ms = np.diff(peaks) * 1000.0 / fs
    valid = intervals_ms[(intervals_ms >= 300.0) & (intervals_ms <= 2200.0)]
    if valid.size < max(3, intervals_ms.size // 2):
        return -1.0, 0.0
    median_hr = 60000.0 / float(np.median(valid))
    if not (40.0 <= median_hr <= 190.0):
        return -1.0, median_hr
    valid_ratio = float(valid.size) / max(1, intervals_ms.size)
    return valid_ratio * min(1.0, valid.size / 60.0), median_hr


def detect_peak_events(values: np.ndarray, fs: float) -> list[tuple[float, float]]:
    filtered = bandpass(values, fs)
    distance = max(1, int(0.30 * fs))
    prominence = max(float(np.std(filtered)) * 0.25, 1e-6)
    candidates: list[tuple[float, float, np.ndarray]] = []
    for polarity, signal_values in ((1.0, filtered), (-1.0, -filtered)):
        peaks, _props = find_peaks(signal_values, distance=distance, prominence=prominence)
        score, _median_hr = plausible_peak_score(peaks, fs)
        candidates.append((score, polarity, peaks))
    candidates.sort(key=lambda item: item[0], reverse=True)
    peaks = candidates[0][2]
    events: list[tuple[float, float]] = []
    for i in range(1, len(peaks)):
        interval_ms = (float(peaks[i] - peaks[i - 1]) * 1000.0) / fs
        if 300.0 <= interval_ms <= 2200.0:
            events.append((float(peaks[i]) / fs, interval_ms))
    return events


def wesad_events_from_subject(path: Path, signal_source: str) -> list[PpiEvent]:
    data = load_wesad_pickle(path)
    signal = data["signal"]
    labels = np.asarray(data["label"]).reshape(-1)

    if signal_source == "bvp":
        values = np.asarray(signal["wrist"]["BVP"]).reshape(-1)
        fs = WESAD_WRIST_BVP_FS
    else:
        values = np.asarray(signal["chest"]["ECG"]).reshape(-1)
        fs = WESAD_CHEST_ECG_FS

    peak_events = detect_peak_events(values, fs)
    duration_s = len(values) / fs
    label_fs = len(labels) / duration_s if duration_s > 0.0 else WESAD_CHEST_ECG_FS
    events: list[PpiEvent] = []
    for time_s, interval_ms in peak_events:
        label_index = int(clamp(time_s * label_fs, 0.0, float(len(labels) - 1)))
        state = int(labels[label_index])
        events.append(PpiEvent(time_s=time_s, interval_ms=interval_ms, state=state))
    return events


def label_for_state(state: int, exclude_amusement: bool) -> int | None:
    if state == 2:
        return 1
    if state == 1:
        return 0
    if state == 3 and not exclude_amusement:
        return 0
    return None


def windows_from_events(
    events: list[PpiEvent],
    source: str,
    subject: str,
    window_s: float,
    step_s: float,
    min_intervals: int,
    label_purity: float,
    exclude_amusement: bool,
) -> list[StressWindow]:
    if not events:
        return []
    start_s = math.floor(events[0].time_s / step_s) * step_s
    end_limit = events[-1].time_s
    windows: list[StressWindow] = []

    while start_s + window_s <= end_limit:
        end_s = start_s + window_s
        selected = [event for event in events if start_s <= event.time_s < end_s]
        if len(selected) >= min_intervals:
            state_counts = Counter(event.state for event in selected if event.state is not None)
            if state_counts:
                state, count = state_counts.most_common(1)[0]
                purity = count / len(selected)
                label = label_for_state(int(state), exclude_amusement)
                if label is not None and purity >= label_purity:
                    features = compute_hrv_features([event.interval_ms for event in selected])
                    if features is not None:
                        windows.append(
                            StressWindow(
                                source=source,
                                subject=subject,
                                state_name=WESAD_LABEL_NAMES.get(int(state), str(state)),
                                start_s=start_s,
                                end_s=end_s,
                                label=label,
                                features=features,
                            )
                        )
        start_s += step_s
    return windows


def load_wesad_windows(args: argparse.Namespace) -> list[StressWindow]:
    wesad_dir = args.wesad_dir or (args.base_dir / "wesad")
    paths = find_wesad_pickles(wesad_dir)
    if not paths:
        raise FileNotFoundError(
            f"missing WESAD .pkl files under {wesad_dir}; run python scripts/download_stress_training_data.py first"
        )

    windows: list[StressWindow] = []
    for path in paths:
        subject = path.stem
        try:
            events = wesad_events_from_subject(path, args.signal)
            subject_windows = windows_from_events(
                events,
                source=f"wesad_{args.signal}",
                subject=subject,
                window_s=args.window_s,
                step_s=args.step_s,
                min_intervals=args.min_intervals,
                label_purity=args.label_purity,
                exclude_amusement=args.exclude_amusement,
            )
            print(f"{subject}: events={len(events)} windows={len(subject_windows)}")
            windows.extend(subject_windows)
        except Exception as exc:
            print(f"WARN: failed {path}: {exc}", file=sys.stderr)
    return windows


def split_by_subject(
    windows: list[StressWindow], seed: int, test_fraction: float
) -> tuple[list[StressWindow], list[StressWindow]]:
    by_subject: dict[str, list[StressWindow]] = defaultdict(list)
    for window in windows:
        by_subject[window.subject].append(window)

    subjects = sorted(by_subject)
    rng = random.Random(seed)
    rng.shuffle(subjects)
    test_count = max(1, min(len(subjects) - 1, int(round(len(subjects) * test_fraction))))
    test_subjects = set(subjects[:test_count])

    train = [window for subject, items in by_subject.items() if subject not in test_subjects for window in items]
    test = [window for subject, items in by_subject.items() if subject in test_subjects for window in items]
    if len({window.label for window in train}) < 2 or len({window.label for window in test}) < 2:
        raise RuntimeError("subject split did not contain both classes in train/test; adjust --seed")
    return train, test


def matrix_from_windows(windows: list[StressWindow]) -> tuple[np.ndarray, np.ndarray]:
    x = np.asarray([[window.features[feature] for feature in MODEL_FEATURES] for window in windows], dtype=float)
    y = np.asarray([int(window.label) for window in windows], dtype=int)
    return x, y


def fit_model(train_windows: list[StressWindow], args: argparse.Namespace) -> dict[str, Any]:
    x_train, y_train = matrix_from_windows(train_windows)
    scaler = StandardScaler()
    x_scaled = scaler.fit_transform(x_train)
    clf = LogisticRegression(
        class_weight="balanced",
        max_iter=2000,
        random_state=args.seed,
        solver="liblinear",
    )
    clf.fit(x_scaled, y_train)

    probs = clf.predict_proba(x_scaled)[:, 1]
    nonstress_probs = [float(p) for p, y in zip(probs, y_train) if y == 0]
    stress_probs = [float(p) for p, y in zip(probs, y_train) if y == 1]
    p_non = median(nonstress_probs)
    p_stress = median(stress_probs)
    if p_stress <= p_non + 1e-6:
        p_non = 0.25
        p_stress = 0.75

    return {
        "model_type": "logistic_regression",
        "features": MODEL_FEATURES,
        "class_names": {"0": "nonstress", "1": "stress"},
        "scaler_mean": [float(v) for v in scaler.mean_],
        "scaler_scale": [float(v) if float(v) > 1e-12 else 1.0 for v in scaler.scale_],
        "coef": [float(v) for v in clf.coef_[0]],
        "intercept": float(clf.intercept_[0]),
        "index_calibration": {
            "prob_nonstress_median": p_non,
            "prob_stress_median": p_stress,
            "index_nonstress_target": args.index_nonstress_target,
            "index_stress_target": args.index_stress_target,
            "display_index_bias": args.display_index_bias,
            "index_min": 1,
            "index_max": 99,
        },
        "metadata": {
            "source_dataset": "WESAD",
            "signal": args.signal,
            "window_s": args.window_s,
            "step_s": args.step_s,
            "min_intervals": args.min_intervals,
            "label_purity": args.label_purity,
            "exclude_amusement": args.exclude_amusement,
            "seed": args.seed,
            "train_windows": len(train_windows),
            "feature_note": "PPI/HRV interval features only; no ECG/PPG morphology, EDA, respiration, or temperature.",
            "display_note": "1-29 relaxed, 30-59 normal, 60-79 medium, 80-99 high; invalid windows should display --.",
        },
    }


def predict_probability(model: dict[str, Any], features: dict[str, float]) -> float:
    z = float(model["intercept"])
    for i, feature in enumerate(model["features"]):
        value = float(features[feature])
        mean = float(model["scaler_mean"][i])
        scale = float(model["scaler_scale"][i]) or 1.0
        z += float(model["coef"][i]) * ((value - mean) / scale)
    if z >= 0.0:
        return 1.0 / (1.0 + math.exp(-z))
    exp_z = math.exp(z)
    return exp_z / (1.0 + exp_z)


def stress_index_from_probability(model: dict[str, Any], probability: float) -> int:
    cal = model["index_calibration"]
    p_non = float(cal["prob_nonstress_median"])
    p_stress = float(cal["prob_stress_median"])
    idx_non = float(cal["index_nonstress_target"])
    idx_stress = float(cal["index_stress_target"])
    if p_stress <= p_non + 1e-6:
        raw = 1.0 + probability * 98.0
    else:
        raw = idx_non + (probability - p_non) * (idx_stress - idx_non) / (p_stress - p_non)
    raw += float(cal.get("display_index_bias", 0.0))
    return c_round(clamp(raw, float(cal["index_min"]), float(cal["index_max"])))


def evaluate_windows(name: str, windows: list[StressWindow], model: dict[str, Any]) -> dict[str, Any]:
    rows = []
    y_true: list[int] = []
    probs: list[float] = []
    indexes: list[int] = []
    by_state: dict[str, list[int]] = defaultdict(list)

    for window in windows:
        probability = predict_probability(model, window.features)
        index = stress_index_from_probability(model, probability)
        rows.append((window, probability, index))
        y_true.append(int(window.label))
        probs.append(probability)
        indexes.append(index)
        by_state[window.state_name].append(index)

    pred = [1 if index >= 60 else 0 for index in indexes]
    tp = sum(1 for y, p in zip(y_true, pred) if y == 1 and p == 1)
    fp = sum(1 for y, p in zip(y_true, pred) if y == 0 and p == 1)
    tn = sum(1 for y, p in zip(y_true, pred) if y == 0 and p == 0)
    fn = sum(1 for y, p in zip(y_true, pred) if y == 1 and p == 0)
    precision = tp / (tp + fp) if (tp + fp) else 0.0
    recall = tp / (tp + fn) if (tp + fn) else 0.0
    specificity = tn / (tn + fp) if (tn + fp) else 0.0
    f1 = 2.0 * precision * recall / (precision + recall) if (precision + recall) else 0.0
    auc = None
    if len(set(y_true)) == 2:
        auc = round(float(roc_auc_score(y_true, probs)), 4)

    return {
        "name": name,
        "count": len(windows),
        "class_counts": dict(Counter(y_true)),
        "index_threshold_for_stress": 60,
        "confusion": {"tp": tp, "fp": fp, "tn": tn, "fn": fn},
        "accuracy": round((tp + tn) / len(windows), 4) if windows else 0.0,
        "precision": round(precision, 4),
        "recall": round(recall, 4),
        "specificity": round(specificity, 4),
        "f1": round(f1, 4),
        "auc": auc,
        "stress_index_all": summarize([float(v) for v in indexes]),
        "stress_index_nonstress": summarize([float(v) for v, y in zip(indexes, y_true) if y == 0]),
        "stress_index_stress": summarize([float(v) for v, y in zip(indexes, y_true) if y == 1]),
        "stress_index_by_state": {state: summarize([float(v) for v in vals]) for state, vals in sorted(by_state.items())},
    }


def write_windows_csv(path: Path, windows: list[StressWindow]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = ["source", "subject", "state_name", "start_s", "end_s", "label", *MODEL_FEATURES]
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for window in windows:
            row: dict[str, Any] = {
                "source": window.source,
                "subject": window.subject,
                "state_name": window.state_name,
                "start_s": round(window.start_s, 3),
                "end_s": round(window.end_s, 3),
                "label": window.label,
            }
            row.update({feature: round(window.features[feature], 6) for feature in MODEL_FEATURES})
            writer.writerow(row)


def local_ppg_paths(patterns: list[str]) -> list[Path]:
    paths: list[Path] = []
    for pattern in patterns:
        paths.extend(Path(".").glob(pattern))
    return sorted({path.resolve(): path for path in paths}.values())


def local_events_from_processed_csv(path: Path) -> list[PpiEvent]:
    events: list[PpiEvent] = []
    with path.open("r", newline="", encoding="utf-8", errors="ignore") as f:
        reader = csv.DictReader(f)
        for row in reader:
            if "finger_present" in row and not truthy(row.get("finger_present")):
                continue
            if not truthy(row.get("peak")):
                continue
            try:
                interval_ms = float(row.get("peak_interval_ms") or "")
            except ValueError:
                continue
            if not (300.0 <= interval_ms <= 2200.0):
                continue
            time_text = row.get("sample_time_ms") or row.get("t_ms") or ""
            try:
                time_s = float(time_text) / 1000.0
            except ValueError:
                try:
                    time_s = float(row.get("sample_count") or "0") * 0.01
                except ValueError:
                    continue
            events.append(PpiEvent(time_s=time_s, interval_ms=interval_ms, state=None))
    return events


def unlabeled_windows_from_events(
    events: list[PpiEvent],
    source: str,
    subject: str,
    window_s: float,
    step_s: float,
    min_intervals: int,
) -> list[StressWindow]:
    if not events:
        return []
    start_s = math.floor(events[0].time_s / step_s) * step_s
    end_limit = events[-1].time_s
    windows: list[StressWindow] = []
    while start_s + window_s <= end_limit:
        end_s = start_s + window_s
        selected = [event for event in events if start_s <= event.time_s < end_s]
        if len(selected) >= min_intervals:
            features = compute_hrv_features([event.interval_ms for event in selected])
            if features is not None:
                windows.append(
                    StressWindow(
                        source=source,
                        subject=subject,
                        state_name="local_normal",
                        start_s=start_s,
                        end_s=end_s,
                        label=None,
                        features=features,
                    )
                )
        start_s += step_s
    return windows


def evaluate_local_ppg(
    paths: list[Path],
    model: dict[str, Any],
    window_s: float,
    step_s: float,
    min_intervals: int,
    detail_path: Path,
) -> dict[str, Any]:
    detail_path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "file",
        "window_idx",
        "start_s",
        "end_s",
        "stress_probability",
        "stress_index",
        "level",
        *MODEL_FEATURES,
        "error",
    ]
    all_indexes: list[float] = []
    file_summaries: list[dict[str, Any]] = []
    with detail_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for path in paths:
            try:
                events = local_events_from_processed_csv(path)
                windows = unlabeled_windows_from_events(events, "local_ppg", str(path.parent), window_s, step_s, min_intervals)
                indexes: list[float] = []
                for idx, window in enumerate(windows):
                    probability = predict_probability(model, window.features)
                    stress_index = stress_index_from_probability(model, probability)
                    indexes.append(float(stress_index))
                    all_indexes.append(float(stress_index))
                    row: dict[str, Any] = {
                        "file": str(path),
                        "window_idx": idx,
                        "start_s": round(window.start_s, 3),
                        "end_s": round(window.end_s, 3),
                        "stress_probability": round(probability, 6),
                        "stress_index": stress_index,
                        "level": stress_level(stress_index),
                        "error": "",
                    }
                    row.update({feature: round(window.features[feature], 6) for feature in MODEL_FEATURES})
                    writer.writerow(row)
                file_summaries.append({"file": str(path), "windows": len(windows), "stress_index": summarize(indexes)})
            except Exception as exc:
                writer.writerow({"file": str(path), "error": str(exc)})
                file_summaries.append({"file": str(path), "windows": 0, "error": str(exc)})

    return {
        "files": len(paths),
        "total_windows": int(summarize(all_indexes)["count"]),
        "stress_index": summarize(all_indexes),
        "file_summaries": file_summaries,
        "detail_csv": str(detail_path),
    }


def model_digest(windows: list[StressWindow]) -> str:
    h = hashlib.sha256()
    for window in windows:
        h.update(f"{window.source},{window.subject},{window.state_name},{window.start_s:.3f},{window.label}".encode())
        for feature in MODEL_FEATURES:
            h.update(f",{window.features[feature]:.6f}".encode())
        h.update(b"\n")
    return h.hexdigest()


def c_float(value: float) -> str:
    return f"{value:.9g}f"


def write_c_header(path: Path, model: dict[str, Any]) -> None:
    features = list(model["features"])
    path.parent.mkdir(parents=True, exist_ok=True)
    lines: list[str] = []
    lines.append("#pragma once")
    lines.append("")
    lines.append("/* Auto-generated by scripts/train_stress_hrv_model.py. */")
    lines.append("#include <stdint.h>")
    lines.append("")
    lines.append(f"#define STRESS_HRV_FEATURE_COUNT {len(features)}U")
    lines.append("#define STRESS_INDEX_MIN 1U")
    lines.append("#define STRESS_INDEX_MAX 99U")
    lines.append("")
    lines.append("static const float STRESS_HRV_SCALER_MEAN[STRESS_HRV_FEATURE_COUNT] = {")
    lines.append("  " + ", ".join(c_float(float(v)) for v in model["scaler_mean"]))
    lines.append("};")
    lines.append("static const float STRESS_HRV_SCALER_SCALE[STRESS_HRV_FEATURE_COUNT] = {")
    lines.append("  " + ", ".join(c_float(float(v)) for v in model["scaler_scale"]))
    lines.append("};")
    lines.append("static const float STRESS_HRV_COEF[STRESS_HRV_FEATURE_COUNT] = {")
    lines.append("  " + ", ".join(c_float(float(v)) for v in model["coef"]))
    lines.append("};")
    lines.append(f"static const float STRESS_HRV_INTERCEPT = {c_float(float(model['intercept']))};")
    cal = model["index_calibration"]
    lines.append(f"static const float STRESS_HRV_PROB_NONSTRESS_MEDIAN = {c_float(float(cal['prob_nonstress_median']))};")
    lines.append(f"static const float STRESS_HRV_PROB_STRESS_MEDIAN = {c_float(float(cal['prob_stress_median']))};")
    lines.append(f"static const float STRESS_HRV_INDEX_NONSTRESS_TARGET = {c_float(float(cal['index_nonstress_target']))};")
    lines.append(f"static const float STRESS_HRV_INDEX_STRESS_TARGET = {c_float(float(cal['index_stress_target']))};")
    lines.append(f"static const float STRESS_HRV_DISPLAY_INDEX_BIAS = {c_float(float(cal.get('display_index_bias', 0.0)))};")
    lines.append("")
    for i, feature in enumerate(features):
        lines.append(f"/* {i}: {feature} */")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_model_json(path: Path, model: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(model, indent=2, ensure_ascii=False), encoding="utf-8")


def main() -> int:
    args = parse_args()
    derived_dir = args.base_dir / "derived"
    model_dir = args.base_dir / "models"
    report_dir = args.base_dir / "reports"
    local_patterns = args.local_ppg_glob or DEFAULT_LOCAL_PPG_GLOBS

    try:
        windows = load_wesad_windows(args)
        if not windows:
            print("ERROR: no WESAD stress windows were created", file=sys.stderr)
            return 1
        train_windows, test_windows = split_by_subject(windows, args.seed, args.test_fraction)
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    model = fit_model(train_windows, args)
    model["metadata"]["all_windows"] = len(windows)
    model["metadata"]["test_windows"] = len(test_windows)
    model["metadata"]["input_digest_sha256"] = model_digest(train_windows)
    model["metadata"]["train_subjects"] = sorted({window.subject for window in train_windows})
    model["metadata"]["test_subjects"] = sorted({window.subject for window in test_windows})

    reports: dict[str, Any] = {
        "wesad_all": evaluate_windows("wesad_all", windows, model),
        "wesad_train_by_subject": evaluate_windows("wesad_train_by_subject", train_windows, model),
        "wesad_test_by_subject": evaluate_windows("wesad_test_by_subject", test_windows, model),
    }

    local_paths = local_ppg_paths(local_patterns)
    local_report_csv = report_dir / "local_ppg_stress_index.csv"
    reports["local_ppg_normal_smoke"] = evaluate_local_ppg(
        local_paths, model, args.window_s, args.step_s, args.min_intervals, local_report_csv
    )

    model["validation"] = reports

    windows_csv = derived_dir / "stress_hrv_windows.csv"
    model_json = model_dir / "stress_hrv_model.json"
    model_header = model_dir / "stress_hrv_model.h"
    report_json = report_dir / "stress_hrv_validation.json"
    chinese_model_json = model_dir / "压力HRV模型参数.json"
    chinese_model_header = model_dir / "压力HRV模型参数_单片机头文件.h"
    chinese_report_json = report_dir / "压力HRV模型训练验证结果.json"
    chinese_local_csv = report_dir / "本地PPG压力指数验证.csv"

    write_windows_csv(windows_csv, windows)
    write_model_json(model_json, model)
    write_c_header(model_header, model)
    write_model_json(chinese_model_json, model)
    write_c_header(chinese_model_header, model)
    report_json.parent.mkdir(parents=True, exist_ok=True)
    report_json.write_text(json.dumps(reports, indent=2, ensure_ascii=False), encoding="utf-8")
    chinese_report_json.write_text(json.dumps(reports, indent=2, ensure_ascii=False), encoding="utf-8")
    if local_report_csv.exists():
        chinese_local_csv.write_text(local_report_csv.read_text(encoding="utf-8"), encoding="utf-8")

    print("")
    print("Stress HRV validation summary")
    for key in ("wesad_all", "wesad_train_by_subject", "wesad_test_by_subject"):
        report = reports[key]
        print(
            f"{key}: count={report['count']} auc={report['auc']} "
            f"acc={report['accuracy']} f1={report['f1']} "
            f"nonstress_median={report['stress_index_nonstress']['median']} "
            f"stress_median={report['stress_index_stress']['median']}"
        )
    local_report = reports["local_ppg_normal_smoke"]
    print(
        f"local_ppg_normal_smoke: files={local_report['files']} windows={local_report['total_windows']} "
        f"median={local_report['stress_index']['median']} max={local_report['stress_index']['max']}"
    )
    print("")
    print(f"wrote {windows_csv}")
    print(f"wrote {model_json}")
    print(f"wrote {model_header}")
    print(f"wrote {report_json}")
    print(f"wrote {local_report_csv}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
