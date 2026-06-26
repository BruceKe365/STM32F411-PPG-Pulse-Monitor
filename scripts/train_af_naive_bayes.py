#!/usr/bin/env python3
# 中文说明：
#   从公开 RR/PPI 间期数据训练轻量级房颤风险朴素贝叶斯模型。
#   训练特征严格限制为单片机能从 PPG 峰值间期得到的指标，例如 CV、RMSSD、pNNx、最大最小间期比等。
#   训练来源可包含 AFDB/LTAFDB/NSR2DB/MITDB/NSRDB 等公开数据，但不使用 ECG 形态学波形。
#   输出模型 JSON 和 MCU 头文件参数，供 scripts/validate_mcu_af_live.py 和固件移植使用。
"""Train a lightweight AF-risk Naive Bayes model from RR/PPI intervals.

The model is intentionally limited to features an STM32 can compute from PPG
beat times:

- valid interval count in a window
- mean/std/CV of PPI/RR intervals
- RMSSD and pNNx irregularity statistics
- min/max ratio and simple delta statistics

The public training source is ECG annotation timing, but no ECG morphology is
used. This keeps the trained model compatible with a later PPG-only MCU port.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import random
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any


AFDB_DIR = Path("training_dataset/physionet/afdb/1.0.0")
NSR2DB_DIR = Path("training_dataset/physionet/nsr2db/1.0.0")
BEAT_ANNOT_EXT = {
    "afdb": ".qrs",
    "ltafdb": ".atr",
    "nsr2db": ".ecg",
    "mitdb": ".atr",
    "nsrdb": ".atr",
}
RHYTHM_ANNOT_EXT = {"afdb": ".atr", "ltafdb": ".atr", "mitdb": ".atr"}

CLASS_NORMAL = 0
CLASS_AF = 1
CLASS_NAMES = {CLASS_NORMAL: "normal", CLASS_AF: "af"}

# WFDB annotation codes that represent beats. This covers the normal MIT beat
# symbols and common variants while excluding rhythm/noise/comment annotations.
BEAT_CODES = {
    1,   # N
    2,   # L
    3,   # R
    4,   # a
    5,   # V
    6,   # F
    7,   # J
    8,   # A
    9,   # S
    10,  # E
    11,  # j
    12,  # /
    13,  # Q
    25,  # e
    30,  # f
    34,  # ?
    35,  # B
    38,  # r
    41,  # n
}

ANNOT_SKIP = 59
ANNOT_NUM = 60
ANNOT_SUB = 61
ANNOT_CHN = 62
ANNOT_AUX = 63

MODEL_FEATURES = [
    "valid_interval_count",
    "mean_ppi_ms",
    "iqr_ppi_ms",
    "trimmed_std_ppi_ms",
    "trimmed_cv_ppi",
    "median_abs_delta_ms",
    "p80_abs_delta_ms",
    "p95_abs_delta_ms",
    "pnn50_pct",
    "pnn80_pct",
    "pnn120_pct",
]

# Upper bin edges. Bin index is the first edge greater than the feature value,
# or the final overflow bin.
FEATURE_BINS: dict[str, list[float]] = {
    "valid_interval_count": [20, 30, 40, 50, 70, 100],
    "mean_ppi_ms": [450, 550, 650, 750, 850, 1000, 1200, 1600],
    "iqr_ppi_ms": [20, 40, 60, 90, 130, 180, 260, 400, 700],
    "trimmed_std_ppi_ms": [10, 20, 35, 55, 80, 120, 180, 260, 420],
    "trimmed_cv_ppi": [0.02, 0.04, 0.06, 0.09, 0.13, 0.18, 0.25, 0.35, 0.55],
    "median_abs_delta_ms": [5, 10, 20, 35, 55, 80, 120, 180, 280],
    "p80_abs_delta_ms": [20, 40, 60, 90, 130, 180, 260, 400, 700],
    "p95_abs_delta_ms": [40, 80, 120, 180, 260, 400, 700, 1000, 1600],
    "pnn50_pct": [1, 5, 10, 20, 35, 50, 70, 90],
    "pnn80_pct": [1, 5, 10, 20, 35, 50, 70, 90],
    "pnn120_pct": [1, 5, 10, 20, 35, 50, 70, 90],
}


@dataclass
class Annotation:
    sample: int
    code: int
    aux: str = ""


@dataclass
class IntervalEvent:
    time_s: float
    interval_ms: float
    rhythm: str


@dataclass
class Window:
    source: str
    record: str
    start_s: float
    end_s: float
    label: int | None
    features: dict[str, float]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-dir", type=Path, default=Path("training_dataset"))
    parser.add_argument("--window-s", type=float, default=30.0)
    parser.add_argument("--step-s", type=float, default=10.0)
    parser.add_argument("--min-intervals", type=int, default=20)
    parser.add_argument("--label-purity", type=float, default=0.80)
    parser.add_argument("--test-fraction", type=float, default=0.25)
    parser.add_argument("--seed", type=int, default=411)
    parser.add_argument("--alpha", type=float, default=1.0, help="Laplace smoothing")
    parser.add_argument(
        "--ppg-glob",
        default="testing dataset/1[0-2]_*/processed.csv",
        help="Local high-quality normal PPG processed CSV glob for normal-risk smoke tests",
    )
    parser.add_argument(
        "--include-synthetic-report",
        action="store_true",
        default=True,
        help="Include synthetic normal/AF stress-test windows in the report",
    )
    return parser.parse_args()


def parse_header_fs(path: Path) -> float:
    line = path.read_text(encoding="utf-8", errors="ignore").splitlines()[0]
    parts = line.split()
    if len(parts) < 3:
        raise ValueError(f"bad WFDB header: {path}")
    return float(parts[2])


def read_records_file(path: Path) -> list[str]:
    text = path.read_text(encoding="utf-8").strip()
    return [item.strip() for item in text.split() if item.strip()]


def read_wfdb_annotations(path: Path) -> list[Annotation]:
    data = path.read_bytes()
    annotations: list[Annotation] = []
    sample = 0
    i = 0
    last: Annotation | None = None

    while i + 1 < len(data):
        b0 = data[i]
        b1 = data[i + 1]
        i += 2
        interval = b0 + ((b1 & 0x03) << 8)
        code = b1 >> 2

        if code == 0 and interval == 0:
            break

        if code == ANNOT_SKIP:
            if i + 3 >= len(data):
                break
            # Long skip intervals are rare in beat annotation streams. WFDB uses
            # four bytes after a SKIP pair; big-endian is correct for MIT files.
            sample += int.from_bytes(data[i : i + 4], "big", signed=True)
            i += 4
            last = None
            continue

        if code in (ANNOT_NUM, ANNOT_SUB, ANNOT_CHN):
            if i + 1 < len(data):
                i += 2
            continue

        if code == ANNOT_AUX:
            aux_len = interval
            raw_aux = data[i : i + aux_len]
            i += aux_len
            if aux_len % 2:
                i += 1
            aux = raw_aux.decode("latin-1", errors="ignore").replace("\x00", "").strip()
            if last is not None:
                last.aux = aux
            continue

        sample += interval
        last = Annotation(sample=sample, code=code, aux="")
        annotations.append(last)

    return annotations


def normalize_rhythm(aux: str) -> str:
    clean = aux.replace("\x00", "").strip()
    if clean.startswith("("):
        clean = clean[1:]
    clean = clean.upper()
    if clean.startswith("AFIB"):
        return "AFIB"
    if clean.startswith("AFL"):
        return "AFL"
    if clean.startswith("N"):
        return "N"
    if clean.startswith("J"):
        return "J"
    return clean or "UNKNOWN"


def median(values: list[float]) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    n = len(ordered)
    mid = n // 2
    if n % 2:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid]) * 0.5


def percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    pos = (len(ordered) - 1) * pct
    low = int(math.floor(pos))
    high = int(math.ceil(pos))
    if low == high:
        return ordered[low]
    frac = pos - low
    return ordered[low] * (1.0 - frac) + ordered[high] * frac


def compute_features(intervals_ms: list[float]) -> dict[str, float] | None:
    intervals = [float(v) for v in intervals_ms if 300.0 <= float(v) <= 2200.0]
    n = len(intervals)
    if n < 2:
        return None

    mean_v = sum(intervals) / n
    variance = sum((v - mean_v) ** 2 for v in intervals) / max(1, n - 1)
    std_v = math.sqrt(variance)
    deltas = [intervals[i] - intervals[i - 1] for i in range(1, n)]
    abs_deltas = [abs(v) for v in deltas]
    rmssd = math.sqrt(sum(v * v for v in deltas) / len(deltas)) if deltas else 0.0
    pnn50 = 100.0 * sum(1 for v in abs_deltas if v > 50.0) / len(abs_deltas) if abs_deltas else 0.0
    pnn80 = 100.0 * sum(1 for v in abs_deltas if v > 80.0) / len(abs_deltas) if abs_deltas else 0.0
    pnn120 = 100.0 * sum(1 for v in abs_deltas if v > 120.0) / len(abs_deltas) if abs_deltas else 0.0
    min_v = min(intervals)
    max_v = max(intervals)
    q25 = percentile(intervals, 0.25)
    q75 = percentile(intervals, 0.75)
    sorted_intervals = sorted(intervals)
    trim = int(math.floor(n * 0.10))
    if n >= 10 and trim > 0 and (2 * trim) < n:
        trimmed = sorted_intervals[trim:-trim]
    else:
        trimmed = sorted_intervals
    trimmed_mean = sum(trimmed) / len(trimmed)
    trimmed_variance = sum((v - trimmed_mean) ** 2 for v in trimmed) / max(1, len(trimmed) - 1)
    trimmed_std = math.sqrt(trimmed_variance)

    return {
        "valid_interval_count": float(n),
        "mean_ppi_ms": mean_v,
        "std_ppi_ms": std_v,
        "rmssd_ms": rmssd,
        "pnn50_pct": pnn50,
        "pnn80_pct": pnn80,
        "pnn120_pct": pnn120,
        "cv_ppi": std_v / mean_v if mean_v > 0.0 else 0.0,
        "min_ppi_ms": min_v,
        "max_ppi_ms": max_v,
        "max_min_ratio": max_v / min_v if min_v > 0.0 else 0.0,
        "range_ppi_ms": max_v - min_v,
        "iqr_ppi_ms": q75 - q25,
        "median_abs_delta_ms": median(abs_deltas),
        "p80_abs_delta_ms": percentile(abs_deltas, 0.80),
        "p95_abs_delta_ms": percentile(abs_deltas, 0.95),
        "trimmed_std_ppi_ms": trimmed_std,
        "trimmed_cv_ppi": trimmed_std / trimmed_mean if trimmed_mean > 0.0 else 0.0,
    }


def events_from_record(dataset: str, dataset_dir: Path, record: str) -> list[IntervalEvent]:
    fs = parse_header_fs(dataset_dir / f"{record}.hea")
    beat_annotations = read_wfdb_annotations(dataset_dir / f"{record}{BEAT_ANNOT_EXT[dataset]}")
    beat_samples = [ann.sample for ann in beat_annotations if ann.code in BEAT_CODES]

    rhythms: list[tuple[int, str]] = []
    if dataset in RHYTHM_ANNOT_EXT:
        rhythm_annotations = read_wfdb_annotations(dataset_dir / f"{record}{RHYTHM_ANNOT_EXT[dataset]}")
        for ann in rhythm_annotations:
            rhythm = normalize_rhythm(ann.aux)
            if rhythm != "UNKNOWN":
                rhythms.append((ann.sample, rhythm))
        rhythms.sort(key=lambda item: item[0])

    events: list[IntervalEvent] = []
    rhythm_index = 0
    current_rhythm = "N" if dataset in {"nsr2db", "nsrdb"} else "UNKNOWN"

    for i in range(1, len(beat_samples)):
        beat_sample = beat_samples[i]
        if dataset in RHYTHM_ANNOT_EXT:
            while rhythm_index < len(rhythms) and rhythms[rhythm_index][0] <= beat_sample:
                current_rhythm = rhythms[rhythm_index][1]
                rhythm_index += 1

        interval_ms = (beat_samples[i] - beat_samples[i - 1]) * 1000.0 / fs
        if 300.0 <= interval_ms <= 2200.0:
            events.append(
                IntervalEvent(
                    time_s=beat_sample / fs,
                    interval_ms=interval_ms,
                    rhythm=current_rhythm,
                )
            )
    return events


def windows_from_events(
    source: str,
    record: str,
    events: list[IntervalEvent],
    window_s: float,
    step_s: float,
    min_intervals: int,
    label_purity: float,
    fixed_label: int | None = None,
) -> list[Window]:
    if not events:
        return []

    start = math.floor(events[0].time_s / step_s) * step_s
    end = events[-1].time_s
    windows: list[Window] = []

    event_index = 0
    while start + window_s <= end:
        stop = start + window_s
        while event_index < len(events) and events[event_index].time_s < start:
            event_index += 1
        j = event_index
        selected: list[IntervalEvent] = []
        while j < len(events) and events[j].time_s < stop:
            selected.append(events[j])
            j += 1

        intervals = [event.interval_ms for event in selected]
        if len(intervals) >= min_intervals:
            features = compute_features(intervals)
            if features is not None:
                label = fixed_label
                if fixed_label is None:
                    counts = Counter(event.rhythm for event in selected)
                    total = sum(counts.values())
                    af_frac = counts.get("AFIB", 0) / total if total else 0.0
                    normal_frac = counts.get("N", 0) / total if total else 0.0
                    if af_frac >= label_purity:
                        label = CLASS_AF
                    elif normal_frac >= label_purity:
                        label = CLASS_NORMAL
                if label is not None:
                    windows.append(Window(source, record, start, stop, label, features))

        start += step_s

    return windows


def load_public_windows(args: argparse.Namespace) -> list[Window]:
    configs = [
        ("afdb", args.base_dir / "physionet" / "afdb" / "1.0.0", None),
        ("ltafdb", args.base_dir / "physionet" / "ltafdb" / "1.0.0", None),
        ("nsr2db", args.base_dir / "physionet" / "nsr2db" / "1.0.0", CLASS_NORMAL),
    ]
    all_windows: list[Window] = []
    for dataset, dataset_dir, fixed_label in configs:
        records_path = dataset_dir / "RECORDS"
        if not records_path.exists():
            raise FileNotFoundError(f"missing {records_path}; run scripts/download_af_training_data.py first")
        records = read_records_file(records_path)
        for record in records:
            try:
                events = events_from_record(dataset, dataset_dir, record)
                windows = windows_from_events(
                    source=dataset,
                    record=record,
                    events=events,
                    window_s=args.window_s,
                    step_s=args.step_s,
                    min_intervals=args.min_intervals,
                    label_purity=args.label_purity,
                    fixed_label=fixed_label,
                )
                all_windows.extend(windows)
                label_counts = Counter(w.label for w in windows)
                print(
                    f"{dataset}/{record}: events={len(events)} windows={len(windows)} "
                    f"normal={label_counts.get(CLASS_NORMAL, 0)} af={label_counts.get(CLASS_AF, 0)}"
                )
            except Exception as exc:
                print(f"WARN: failed {dataset}/{record}: {exc}", file=sys.stderr)
    return all_windows


def record_group_key(window: Window) -> str:
    return f"{window.source}/{window.record}"


def split_by_record(
    windows: list[Window], seed: int, test_fraction: float
) -> tuple[list[Window], list[Window]]:
    groups = sorted({record_group_key(window) for window in windows})
    rng = random.Random(seed)
    rng.shuffle(groups)
    test_count = max(1, int(round(len(groups) * test_fraction)))
    test_groups = set(groups[:test_count])
    train = [window for window in windows if record_group_key(window) not in test_groups]
    test = [window for window in windows if record_group_key(window) in test_groups]

    def has_both_classes(items: list[Window]) -> bool:
        labels = {item.label for item in items}
        return CLASS_NORMAL in labels and CLASS_AF in labels

    if not has_both_classes(train) or not has_both_classes(test):
        raise RuntimeError("record split did not contain both classes in train/test; adjust --seed")
    return train, test


def bin_index(feature: str, value: float) -> int:
    edges = FEATURE_BINS[feature]
    for i, edge in enumerate(edges):
        if value < edge:
            return i
    return len(edges)


def fit_discrete_nb(windows: list[Window], alpha: float, metadata: dict[str, Any]) -> dict[str, Any]:
    class_counts = {CLASS_NORMAL: 0, CLASS_AF: 0}
    counts: dict[str, dict[int, list[float]]] = {}
    for feature in MODEL_FEATURES:
        bin_count = len(FEATURE_BINS[feature]) + 1
        counts[feature] = {
            CLASS_NORMAL: [alpha for _ in range(bin_count)],
            CLASS_AF: [alpha for _ in range(bin_count)],
        }

    for window in windows:
        assert window.label is not None
        class_counts[window.label] += 1
        for feature in MODEL_FEATURES:
            idx = bin_index(feature, window.features[feature])
            counts[feature][window.label][idx] += 1.0

    if class_counts[CLASS_NORMAL] <= 0 or class_counts[CLASS_AF] <= 0:
        raise RuntimeError(f"need both classes for training, got {class_counts}")

    total_windows = class_counts[CLASS_NORMAL] + class_counts[CLASS_AF]
    prior_denom = total_windows + alpha * 2.0
    class_log_prior = {
        str(cls): math.log((class_counts[cls] + alpha) / prior_denom)
        for cls in (CLASS_NORMAL, CLASS_AF)
    }

    feature_log_prob: dict[str, dict[str, list[float]]] = {}
    for feature in MODEL_FEATURES:
        feature_log_prob[feature] = {}
        for cls in (CLASS_NORMAL, CLASS_AF):
            denom = sum(counts[feature][cls])
            feature_log_prob[feature][str(cls)] = [
                math.log(value / denom) for value in counts[feature][cls]
            ]

    return {
        "model_type": "discrete_naive_bayes",
        "class_names": {str(k): v for k, v in CLASS_NAMES.items()},
        "positive_class": CLASS_AF,
        "features": MODEL_FEATURES,
        "feature_bins": FEATURE_BINS,
        "class_counts": {str(k): v for k, v in class_counts.items()},
        "class_log_prior": class_log_prior,
        "feature_log_prob": feature_log_prob,
        "metadata": metadata,
    }


def predict_one(model: dict[str, Any], features: dict[str, float]) -> tuple[float, dict[int, float]]:
    scores: dict[int, float] = {}
    for cls in (CLASS_NORMAL, CLASS_AF):
        score = float(model["class_log_prior"][str(cls)])
        for feature in model["features"]:
            idx = bin_index(feature, features[feature])
            score += float(model["feature_log_prob"][feature][str(cls)][idx])
        scores[cls] = score
    diff = scores[CLASS_AF] - scores[CLASS_NORMAL]
    if diff >= 0:
        risk = 100.0 / (1.0 + math.exp(-diff))
    else:
        exp_v = math.exp(diff)
        risk = 100.0 * exp_v / (1.0 + exp_v)
    return risk, scores


def auc_score(labels: list[int], risks: list[float]) -> float | None:
    pairs = sorted(zip(risks, labels), key=lambda item: item[0])
    n_pos = sum(1 for _, label in pairs if label == CLASS_AF)
    n_neg = sum(1 for _, label in pairs if label == CLASS_NORMAL)
    if n_pos == 0 or n_neg == 0:
        return None
    rank_sum = 0.0
    i = 0
    while i < len(pairs):
        j = i + 1
        while j < len(pairs) and pairs[j][0] == pairs[i][0]:
            j += 1
        avg_rank = (i + 1 + j) * 0.5
        for k in range(i, j):
            if pairs[k][1] == CLASS_AF:
                rank_sum += avg_rank
        i = j
    return (rank_sum - n_pos * (n_pos + 1) * 0.5) / (n_pos * n_neg)


def summarize_risks(values: list[float]) -> dict[str, float | int | None]:
    if not values:
        return {"count": 0, "min": None, "median": None, "mean": None, "max": None}
    return {
        "count": len(values),
        "min": round(min(values), 3),
        "median": round(median(values), 3),
        "mean": round(sum(values) / len(values), 3),
        "max": round(max(values), 3),
    }


def metrics_at_threshold(labels: list[int], risks: list[float], threshold: float) -> dict[str, float | int]:
    tp = fp = tn = fn = 0
    for label, risk in zip(labels, risks):
        pred = CLASS_AF if risk >= threshold else CLASS_NORMAL
        if label == CLASS_AF and pred == CLASS_AF:
            tp += 1
        elif label == CLASS_AF and pred == CLASS_NORMAL:
            fn += 1
        elif label == CLASS_NORMAL and pred == CLASS_AF:
            fp += 1
        else:
            tn += 1
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    specificity = tn / (tn + fp) if tn + fp else 0.0
    f1 = 2.0 * precision * recall / (precision + recall) if precision + recall else 0.0
    return {
        "threshold": round(threshold, 3),
        "tp": tp,
        "fp": fp,
        "tn": tn,
        "fn": fn,
        "precision": round(precision, 4),
        "recall": round(recall, 4),
        "specificity": round(specificity, 4),
        "f1": round(f1, 4),
    }


def optimize_thresholds(labels: list[int], risks: list[float]) -> dict[str, Any]:
    if not labels:
        return {}

    candidates = [i * 0.5 for i in range(0, 201)]
    scored = [metrics_at_threshold(labels, risks, threshold) for threshold in candidates]
    best_f1 = max(scored, key=lambda item: (float(item["f1"]), float(item["specificity"])))
    best_youden = max(
        scored,
        key=lambda item: (float(item["recall"]) + float(item["specificity"]) - 1.0, float(item["f1"])),
    )
    conservative = [
        item for item in scored if float(item["specificity"]) >= 0.98 and float(item["recall"]) >= 0.20
    ]
    best_conservative = max(conservative, key=lambda item: (float(item["recall"]), float(item["f1"]))) if conservative else None
    return {
        "best_f1": best_f1,
        "best_youden": best_youden,
        "best_specificity_ge_0_98": best_conservative,
    }


def evaluate_labeled_windows(name: str, windows: list[Window], model: dict[str, Any]) -> dict[str, Any]:
    labels: list[int] = []
    risks: list[float] = []
    for window in windows:
        if window.label is None:
            continue
        risk, _ = predict_one(model, window.features)
        labels.append(window.label)
        risks.append(risk)

    tp = fp = tn = fn = 0
    for label, risk in zip(labels, risks):
        pred = CLASS_AF if risk >= 50.0 else CLASS_NORMAL
        if label == CLASS_AF and pred == CLASS_AF:
            tp += 1
        elif label == CLASS_AF and pred == CLASS_NORMAL:
            fn += 1
        elif label == CLASS_NORMAL and pred == CLASS_AF:
            fp += 1
        else:
            tn += 1

    af_risks = [risk for label, risk in zip(labels, risks) if label == CLASS_AF]
    normal_risks = [risk for label, risk in zip(labels, risks) if label == CLASS_NORMAL]
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    specificity = tn / (tn + fp) if tn + fp else 0.0
    f1 = 2.0 * precision * recall / (precision + recall) if precision + recall else 0.0
    accuracy = (tp + tn) / len(labels) if labels else 0.0
    auc = auc_score(labels, risks)

    return {
        "name": name,
        "count": len(labels),
        "class_counts": dict(Counter(labels)),
        "threshold_percent": 50.0,
        "confusion": {"tp": tp, "fp": fp, "tn": tn, "fn": fn},
        "accuracy": round(accuracy, 4),
        "precision": round(precision, 4),
        "recall": round(recall, 4),
        "specificity": round(specificity, 4),
        "f1": round(f1, 4),
        "auc": round(auc, 4) if auc is not None else None,
        "risk_percent_af": summarize_risks(af_risks),
        "risk_percent_normal": summarize_risks(normal_risks),
        "threshold_optimization": optimize_thresholds(labels, risks),
    }


def write_windows_csv(path: Path, windows: list[Window]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = ["source", "record", "start_s", "end_s", "label", *MODEL_FEATURES]
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for window in windows:
            row = {
                "source": window.source,
                "record": window.record,
                "start_s": round(window.start_s, 3),
                "end_s": round(window.end_s, 3),
                "label": window.label,
            }
            for feature in MODEL_FEATURES:
                row[feature] = round(window.features[feature], 6)
            writer.writerow(row)


def read_local_ppg_windows(
    processed_csv: Path,
    window_s: float,
    step_s: float,
    min_intervals: int,
) -> list[Window]:
    peak_events: list[IntervalEvent] = []
    first_t_ms: float | None = None
    last_peak_t_ms: float | None = None

    def positive_field(row: dict[str, str], key: str) -> bool:
        try:
            return float(str(row.get(key, "")).strip()) > 0.0
        except ValueError:
            return False

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
            # Match the intended MCU display policy: AF risk is evaluated only
            # after the regular HR and SpO2 pipeline already has valid values.
            if not (positive_field(row, "hr_bpm") and positive_field(row, "spo2")):
                continue
            if str(row.get("peak", "")).strip() not in {"1", "1.0", "True", "true"}:
                continue

            interval_text = str(row.get("peak_interval_ms", "")).strip()
            interval_ms: float | None = None
            if interval_text:
                try:
                    interval_ms = float(interval_text)
                except ValueError:
                    interval_ms = None
            if interval_ms is None and last_peak_t_ms is not None:
                interval_ms = t_ms - last_peak_t_ms
            last_peak_t_ms = t_ms

            if interval_ms is None or not (300.0 <= interval_ms <= 2200.0):
                continue
            peak_events.append(
                IntervalEvent(
                    time_s=(t_ms - first_t_ms) / 1000.0,
                    interval_ms=interval_ms,
                    rhythm="PPG",
                )
            )

    return windows_from_events(
        source="local_ppg",
        record=str(processed_csv.parent.name),
        events=peak_events,
        window_s=window_s,
        step_s=step_s,
        min_intervals=min_intervals,
        label_purity=1.0,
        fixed_label=CLASS_NORMAL,
    )


def evaluate_local_ppg(
    ppg_glob: str,
    model: dict[str, Any],
    window_s: float,
    step_s: float,
    min_intervals: int,
    report_csv: Path,
) -> dict[str, Any]:
    files = sorted(Path(".").glob(ppg_glob))
    rows: list[dict[str, Any]] = []
    all_risks: list[float] = []

    for path in files:
        try:
            windows = read_local_ppg_windows(path, window_s, step_s, min_intervals)
        except Exception as exc:
            rows.append({"file": str(path), "windows": 0, "error": str(exc)})
            continue

        risks = [predict_one(model, window.features)[0] for window in windows]
        all_risks.extend(risks)
        rows.append(
            {
                "file": str(path),
                "windows": len(windows),
                "risk_min": round(min(risks), 3) if risks else "",
                "risk_median": round(median(risks), 3) if risks else "",
                "risk_mean": round(sum(risks) / len(risks), 3) if risks else "",
                "risk_max": round(max(risks), 3) if risks else "",
                "high_risk_windows_ge50": sum(1 for risk in risks if risk >= 50.0),
            }
        )

    report_csv.parent.mkdir(parents=True, exist_ok=True)
    with report_csv.open("w", newline="", encoding="utf-8") as f:
        fieldnames = [
            "file",
            "windows",
            "risk_min",
            "risk_median",
            "risk_mean",
            "risk_max",
            "high_risk_windows_ge50",
            "error",
        ]
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)

    return {
        "files": len(files),
        "total_windows": sum(int(row.get("windows") or 0) for row in rows),
        "risk_percent": summarize_risks(all_risks),
        "csv": str(report_csv),
    }


def generate_synthetic_windows(seed: int, count: int, window_s: float) -> list[Window]:
    rng = random.Random(seed)
    windows: list[Window] = []
    for label in (CLASS_NORMAL, CLASS_AF):
        for i in range(count):
            intervals: list[float] = []
            t = 0.0
            while t < window_s:
                if label == CLASS_NORMAL:
                    rr = rng.gauss(800.0, 25.0)
                else:
                    # AF-like stress test: irregular intervals with occasional
                    # short/long swings. This is validation only, not training.
                    base = rng.lognormvariate(math.log(780.0), 0.20)
                    swing = rng.choice([0.0, rng.gauss(-140.0, 50.0), rng.gauss(180.0, 80.0)])
                    rr = base + swing
                rr = max(330.0, min(1500.0, rr))
                intervals.append(rr)
                t += rr / 1000.0
            features = compute_features(intervals)
            if features is None:
                continue
            windows.append(
                Window(
                    source="synthetic",
                    record=f"{CLASS_NAMES[label]}_{i:04d}",
                    start_s=0.0,
                    end_s=window_s,
                    label=label,
                    features=features,
                )
            )
    return windows


def write_model_json(path: Path, model: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(model, indent=2, ensure_ascii=False), encoding="utf-8")


def c_float(value: float) -> str:
    return f"{value:.9g}f"


def write_c_header(path: Path, model: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    features = list(model["features"])
    max_edges = max(len(FEATURE_BINS[feature]) for feature in features)
    max_bins = max(len(FEATURE_BINS[feature]) + 1 for feature in features)

    lines: list[str] = []
    lines.append("#pragma once")
    lines.append("")
    lines.append("/* Auto-generated by scripts/train_af_naive_bayes.py. */")
    lines.append("#include <stdint.h>")
    lines.append("")
    lines.append(f"#define AF_NB_FEATURE_COUNT {len(features)}U")
    lines.append(f"#define AF_NB_MAX_EDGE_COUNT {max_edges}U")
    lines.append(f"#define AF_NB_MAX_BIN_COUNT {max_bins}U")
    lines.append("#define AF_NB_CLASS_NORMAL 0U")
    lines.append("#define AF_NB_CLASS_AF 1U")
    lines.append("")
    lines.append("static const float AF_NB_CLASS_LOG_PRIOR[2] = {")
    lines.append(
        f"  {c_float(float(model['class_log_prior'][str(CLASS_NORMAL)]))}, "
        f"{c_float(float(model['class_log_prior'][str(CLASS_AF)]))}"
    )
    lines.append("};")
    lines.append("")
    lines.append("static const uint8_t AF_NB_EDGE_COUNT[AF_NB_FEATURE_COUNT] = {")
    lines.append("  " + ", ".join(f"{len(FEATURE_BINS[feature])}U" for feature in features))
    lines.append("};")
    lines.append("")
    lines.append("static const float AF_NB_BIN_EDGES[AF_NB_FEATURE_COUNT][AF_NB_MAX_EDGE_COUNT] = {")
    for feature in features:
        values = FEATURE_BINS[feature] + [0.0] * (max_edges - len(FEATURE_BINS[feature]))
        lines.append("  {" + ", ".join(c_float(v) for v in values) + f"}}, /* {feature} */")
    lines.append("};")
    lines.append("")

    for cls, name in ((CLASS_NORMAL, "NORMAL"), (CLASS_AF, "AF")):
        lines.append(
            f"static const float AF_NB_LOG_PROB_{name}[AF_NB_FEATURE_COUNT][AF_NB_MAX_BIN_COUNT] = {{"
        )
        for feature in features:
            values = list(model["feature_log_prob"][feature][str(cls)])
            values += [0.0] * (max_bins - len(values))
            lines.append("  {" + ", ".join(c_float(v) for v in values) + f"}}, /* {feature} */")
        lines.append("};")
        lines.append("")

    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def digest_model_inputs(windows: list[Window]) -> str:
    h = hashlib.sha256()
    for window in windows:
        h.update(f"{window.source},{window.record},{window.start_s:.3f},{window.label}".encode())
        for feature in MODEL_FEATURES:
            h.update(f",{window.features[feature]:.6f}".encode())
        h.update(b"\n")
    return h.hexdigest()


def main() -> int:
    args = parse_args()
    derived_dir = args.base_dir / "derived"
    model_dir = args.base_dir / "models"
    report_dir = args.base_dir / "reports"

    try:
        windows = load_public_windows(args)
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    if not windows:
        print("ERROR: no training windows were created", file=sys.stderr)
        return 1

    write_windows_csv(derived_dir / "af_rr_windows.csv", windows)
    label_counts = Counter(window.label for window in windows)
    source_counts = Counter(window.source for window in windows)
    print(f"public windows={len(windows)} labels={dict(label_counts)} sources={dict(source_counts)}")

    try:
        train_windows, test_windows = split_by_record(windows, args.seed, args.test_fraction)
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    metadata = {
        "window_s": args.window_s,
        "step_s": args.step_s,
        "min_intervals": args.min_intervals,
        "label_purity": args.label_purity,
        "alpha": args.alpha,
        "seed": args.seed,
        "train_windows": len(train_windows),
        "test_windows": len(test_windows),
        "input_digest_sha256": digest_model_inputs(train_windows),
        "feature_note": "RR/PPI interval features only; no ECG/PPG morphology.",
    }
    model = fit_discrete_nb(train_windows, args.alpha, metadata)

    reports: dict[str, Any] = {
        "public_all": evaluate_labeled_windows("public_all", windows, model),
        "public_train": evaluate_labeled_windows("public_train", train_windows, model),
        "public_test_by_record": evaluate_labeled_windows("public_test_by_record", test_windows, model),
    }

    if args.include_synthetic_report:
        synthetic_windows = generate_synthetic_windows(args.seed + 1, 500, args.window_s)
        reports["synthetic_stress_only_not_training"] = evaluate_labeled_windows(
            "synthetic_stress_only_not_training", synthetic_windows, model
        )

    ppg_report_csv = report_dir / "local_ppg_risk.csv"
    reports["local_ppg_normal_smoke"] = evaluate_local_ppg(
        args.ppg_glob,
        model,
        args.window_s,
        args.step_s,
        args.min_intervals,
        ppg_report_csv,
    )

    model["validation"] = reports

    model_json = model_dir / "af_nb_model.json"
    model_header = model_dir / "af_nb_model.h"
    report_json = report_dir / "af_nb_validation.json"
    write_model_json(model_json, model)
    write_c_header(model_header, model)
    report_json.parent.mkdir(parents=True, exist_ok=True)
    report_json.write_text(json.dumps(reports, indent=2, ensure_ascii=False), encoding="utf-8")

    print("")
    print("Validation summary")
    for key, report in reports.items():
        if isinstance(report, dict) and "risk_percent_af" in report:
            print(
                f"{key}: count={report['count']} auc={report['auc']} "
                f"normal_median={report['risk_percent_normal']['median']} "
                f"af_median={report['risk_percent_af']['median']} "
                f"f1={report['f1']}"
            )
        elif key == "local_ppg_normal_smoke":
            print(
                f"{key}: files={report['files']} windows={report['total_windows']} "
                f"risk_median={report['risk_percent']['median']} "
                f"risk_max={report['risk_percent']['max']}"
            )

    print("")
    print(f"wrote {derived_dir / 'af_rr_windows.csv'}")
    print(f"wrote {model_json}")
    print(f"wrote {model_header}")
    print(f"wrote {report_json}")
    print(f"wrote {ppg_report_csv}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
