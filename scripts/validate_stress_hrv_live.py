#!/usr/bin/env python3
# 中文说明：
#   电脑端按当前 MCU 参数回放“PPG-HRV 压力指数”稳态刷新窗口。
#   它读取 scripts/train_stress_hrv_model.py 导出的模型，按本地数据分组验证：
#   理想静息、正常扰动、强按/低质量边界，并把质量不可信窗口显示为 --。
"""Validate parameter-aligned steady-state PPG-HRV stress-index windows."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import train_stress_hrv_model as stress


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


DEFAULT_GROUPS: dict[str, list[str]] = {
    "baseline_ideal": [
        "testing dataset/10_轻触最佳稳定参考/processed.csv",
        "testing dataset/11_轻触重复确认/processed.csv",
        "testing dataset/12_轻触最终检查/processed.csv",
    ],
    "current_baseline": [
        "testing dataset/11*_subject_A_pressure_baseline_light_stable_*/raw_processed.csv",
    ],
    "normal_live": [
        "testing dataset/99_手指放上移开完整实测/raw_processed.csv",
        "testing dataset/105_随机放手移手实测_心率确认后/raw_processed.csv",
        "testing dataset/108_40秒实测波形绘图/raw_processed.csv",
    ],
    "quality_edge": [
        "testing dataset/06_强按低质量血氧空白/processed.csv",
        "testing dataset/06_强按低质量血氧空白/replay_v5_processed.csv",
        "testing dataset/09_强按血氧空白复核/processed.csv",
        "testing dataset/09_强按血氧空白复核/replay_v2_processed.csv",
    ],
    "artifact_check": [
        "testing dataset/114_subject_B_af_high_review_*/raw_processed.csv",
    ],
}

PPI_FILTER_MIN_HR_RATIO = 0.70
PPI_FILTER_MAX_HR_RATIO = 1.45
PPI_FILTER_SLOW_MIN_HR_RATIO = 0.58
PPI_FILTER_SLOW_MAX_HR_RATIO = 1.65
PPI_FILTER_FAST_MIN_HR_RATIO = 0.64
PPI_FILTER_FAST_MAX_HR_RATIO = 1.38
PPI_ARTIFACT_FRAC_GATE = 0.20
STRESS_HRV_WINDOW_S = 40.0
STRESS_HRV_MIN_INTERVAL_COUNT = 28
STRESS_HRV_FIRST_UPDATE_STEP_S = 10.0
STRESS_HRV_REFRESH_UPDATE_STEP_S = 30.0
STRESS_HRV_HIGH_HR_BPM = 120.0


@dataclass
class FileStats:
    rows: int = 0
    finger_rows: int = 0
    peak_rows: int = 0
    valid_peak_rows: int = 0
    first_time_s: float | None = None
    last_time_s: float | None = None
    first_peak_s: float | None = None
    last_peak_s: float | None = None


@dataclass
class PpiArtifactMark:
    time_s: float
    rejected: int


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", type=Path, default=Path("training_dataset/models/stress_hrv_model.json"))
    parser.add_argument("--out-dir", type=Path, default=Path("training_dataset/reports/stress_hrv_local_validation"))
    parser.add_argument("--ppg-glob", action="append", help="Processed local PPG CSV glob to validate. Can be repeated; overrides default groups.")
    parser.add_argument("--ppg-group", default="local_ppg", help="Group label used when --ppg-glob is provided.")
    parser.add_argument("--window-s", type=float, default=STRESS_HRV_WINDOW_S)
    parser.add_argument("--step-s", type=float, default=STRESS_HRV_REFRESH_UPDATE_STEP_S)
    parser.add_argument("--min-intervals", type=int, default=STRESS_HRV_MIN_INTERVAL_COUNT)
    parser.add_argument("--disable-quality-gate", action="store_true")
    parser.add_argument("--finger-present-gate", type=float, default=0.95)
    parser.add_argument("--spo2-valid-gate", type=float, default=0.60)
    parser.add_argument("--finger-score-min-gate", type=float, default=4.0)
    parser.add_argument("--p80-delta-gate-ms", type=float, default=9999.0)
    parser.add_argument("--p95-delta-gate-ms", type=float, default=9999.0)
    parser.add_argument("--outlier-frac-gate", type=float, default=0.30)
    parser.add_argument("--cv-gate", type=float, default=0.60)
    parser.add_argument("--disable-ppi-filter", action="store_true")
    parser.add_argument("--ppi-min-hr-ratio", type=float, default=PPI_FILTER_MIN_HR_RATIO)
    parser.add_argument("--ppi-max-hr-ratio", type=float, default=PPI_FILTER_MAX_HR_RATIO)
    parser.add_argument("--ppi-slow-min-hr-ratio", type=float, default=PPI_FILTER_SLOW_MIN_HR_RATIO)
    parser.add_argument("--ppi-slow-max-hr-ratio", type=float, default=PPI_FILTER_SLOW_MAX_HR_RATIO)
    parser.add_argument("--ppi-fast-min-hr-ratio", type=float, default=PPI_FILTER_FAST_MIN_HR_RATIO)
    parser.add_argument("--ppi-fast-max-hr-ratio", type=float, default=PPI_FILTER_FAST_MAX_HR_RATIO)
    parser.add_argument("--ppi-artifact-frac-gate", type=float, default=PPI_ARTIFACT_FRAC_GATE)
    return parser.parse_args()


def file_stats(path: Path) -> FileStats:
    stats = FileStats()
    with path.open("r", newline="", encoding="utf-8", errors="ignore") as f:
        reader = csv.DictReader(f)
        for row in reader:
            stats.rows += 1
            time_s = row_time_s(row)
            if time_s is not None:
                if stats.first_time_s is None or time_s < stats.first_time_s:
                    stats.first_time_s = time_s
                if stats.last_time_s is None or time_s > stats.last_time_s:
                    stats.last_time_s = time_s

            finger = stress.truthy(row.get("finger_present")) if "finger_present" in row else True
            if finger:
                stats.finger_rows += 1
            if stress.truthy(row.get("peak")):
                stats.peak_rows += 1
                try:
                    interval_ms = float(row.get("peak_interval_ms") or "")
                except ValueError:
                    continue
                if finger and 300.0 <= interval_ms <= 2200.0:
                    stats.valid_peak_rows += 1
                    if time_s is not None:
                        if stats.first_peak_s is None or time_s < stats.first_peak_s:
                            stats.first_peak_s = time_s
                        if stats.last_peak_s is None or time_s > stats.last_peak_s:
                            stats.last_peak_s = time_s
    return stats


def row_time_s(row: dict[str, str]) -> float | None:
    time_text = row.get("sample_time_ms") or row.get("t_ms") or ""
    try:
        return float(time_text) / 1000.0
    except ValueError:
        pass
    try:
        return float(row.get("sample_count") or "0") * 0.01
    except ValueError:
        return None


def expand_group_paths(args: argparse.Namespace) -> list[tuple[str, Path]]:
    items: list[tuple[str, Path]] = []
    groups = {args.ppg_group: args.ppg_glob} if args.ppg_glob else DEFAULT_GROUPS
    for group, patterns in groups.items():
        for pattern in patterns:
            paths = sorted(Path(".").glob(pattern))
            if not paths:
                items.append((group, Path(pattern)))
            else:
                items.extend((group, path) for path in paths)
    return items


def parse_optional_float(text: str | None) -> float | None:
    if text is None:
        return None
    value = text.strip()
    if value == "" or value.lower() in {"none", "nan", "--"}:
        return None
    try:
        parsed = float(value)
    except ValueError:
        return None
    if parsed != parsed:
        return None
    return parsed


def ppi_ratio_limits(hr_bpm: float, args: argparse.Namespace) -> tuple[float, float]:
    if hr_bpm < 65.0:
        return args.ppi_slow_min_hr_ratio, args.ppi_slow_max_hr_ratio
    if hr_bpm > 115.0:
        return args.ppi_fast_min_hr_ratio, args.ppi_fast_max_hr_ratio
    return args.ppi_min_hr_ratio, args.ppi_max_hr_ratio


def ppi_is_usable(interval_ms: float, hr_bpm: float | None, args: argparse.Namespace) -> bool:
    if not (300.0 <= interval_ms <= 2200.0):
        return False
    if args.disable_ppi_filter or hr_bpm is None or hr_bpm <= 0.0:
        return True
    expected_ppi_ms = 60000.0 / hr_bpm
    min_ratio, max_ratio = ppi_ratio_limits(hr_bpm, args)
    return (
        interval_ms >= expected_ppi_ms * min_ratio
        and interval_ms <= expected_ppi_ms * max_ratio
    )


def filtered_local_events_from_processed_csv(
    path: Path,
    args: argparse.Namespace,
) -> tuple[list[stress.PpiEvent], list[PpiArtifactMark]]:
    events: list[stress.PpiEvent] = []
    artifact_marks: list[PpiArtifactMark] = []
    with path.open("r", newline="", encoding="utf-8", errors="ignore") as f:
        reader = csv.DictReader(f)
        for row in reader:
            if "finger_present" in row and not stress.truthy(row.get("finger_present")):
                continue
            if not stress.truthy(row.get("peak")):
                continue
            time_s = row_time_s(row)
            if time_s is None:
                continue
            interval_ms = parse_optional_float(row.get("peak_interval_ms"))
            if interval_ms is None:
                continue
            hr_bpm = parse_optional_float(row.get("hr_bpm"))
            usable = ppi_is_usable(interval_ms, hr_bpm, args)
            artifact_marks.append(PpiArtifactMark(time_s=time_s, rejected=0 if usable else 1))
            if usable:
                events.append(stress.PpiEvent(time_s=time_s, interval_ms=interval_ms, state=None))
    return events, artifact_marks


def ppi_artifact_quality(
    artifact_marks: list[PpiArtifactMark],
    start_s: float,
    end_s: float,
) -> dict[str, float]:
    selected = [mark for mark in artifact_marks if start_s <= mark.time_s < end_s]
    rejected = sum(mark.rejected for mark in selected)
    count = len(selected)
    return {
        "ppi_candidate_count": float(count),
        "ppi_rejected_count": float(rejected),
        "ppi_artifact_frac": (rejected / count) if count else 0.0,
    }


def window_quality(path: Path, start_s: float, end_s: float) -> dict[str, float | None]:
    rows = 0
    finger_rows = 0
    spo2_rows = 0
    hr_rows = 0
    finger_scores: list[float] = []
    ir_rms_values: list[float] = []
    with path.open("r", newline="", encoding="utf-8", errors="ignore") as f:
        reader = csv.DictReader(f)
        for row in reader:
            time_s = row_time_s(row)
            if time_s is None or time_s < start_s or time_s >= end_s:
                continue
            rows += 1
            if stress.truthy(row.get("finger_present")) if "finger_present" in row else True:
                finger_rows += 1
            if parse_optional_float(row.get("spo2")) is not None:
                spo2_rows += 1
            if parse_optional_float(row.get("hr_bpm")) is not None:
                hr_rows += 1
            score = parse_optional_float(row.get("finger_score"))
            if score is not None:
                finger_scores.append(score)
            ir_rms = parse_optional_float(row.get("ir_rms"))
            if ir_rms is not None:
                ir_rms_values.append(ir_rms)

    return {
        "rows": float(rows),
        "finger_present_frac": (finger_rows / rows) if rows else None,
        "spo2_valid_frac": (spo2_rows / rows) if rows else None,
        "hr_valid_frac": (hr_rows / rows) if rows else None,
        "finger_score_min": min(finger_scores) if finger_scores else None,
        "finger_score_median": stress.median(finger_scores) if finger_scores else None,
        "ir_rms_median": stress.median(ir_rms_values) if ir_rms_values else None,
    }


def gate_window(
    features: dict[str, float],
    quality: dict[str, float | None],
    args: argparse.Namespace,
) -> tuple[bool, str]:
    if args.disable_quality_gate:
        return False, "ok"
    finger_frac = quality.get("finger_present_frac")
    if finger_frac is not None and finger_frac < args.finger_present_gate:
        return True, "finger_unstable"
    spo2_frac = quality.get("spo2_valid_frac")
    if spo2_frac is not None and spo2_frac < args.spo2_valid_gate:
        return True, "spo2_unstable"
    score_min = quality.get("finger_score_min")
    if score_min is not None and score_min < args.finger_score_min_gate:
        return True, "finger_score_low"
    artifact_frac = quality.get("ppi_artifact_frac")
    if (
        artifact_frac is not None
        and not args.disable_ppi_filter
        and artifact_frac > args.ppi_artifact_frac_gate
    ):
        return True, "ppi_artifact_frac"
    if features["outlier_frac"] > args.outlier_frac_gate:
        return True, "outlier_frac"
    if features["p80_abs_delta_ms"] > args.p80_delta_gate_ms:
        return True, "p80_abs_delta"
    if features["p95_abs_delta_ms"] > args.p95_delta_gate_ms:
        return True, "p95_abs_delta"
    if features["cv_ppi"] > args.cv_gate:
        return True, "cv_ppi"
    return False, "ok"


def summarize(values: list[float]) -> dict[str, Any]:
    return stress.summarize(values)


def file_no_window_reason(stats: FileStats, window_s: float, min_intervals: int) -> str:
    if stats.rows == 0:
        return "empty_or_missing"
    if stats.valid_peak_rows < min_intervals:
        return "not_enough_valid_peaks"
    if stats.first_peak_s is None or stats.last_peak_s is None:
        return "no_valid_peak_time"
    if (stats.last_peak_s - stats.first_peak_s) < window_s:
        return "peak_span_short"
    return "window_alignment_or_feature_fail"


def write_reports(
    out_dir: Path,
    detail_rows: list[dict[str, Any]],
    file_rows: list[dict[str, Any]],
    config: dict[str, Any],
) -> dict[str, str]:
    out_dir.mkdir(parents=True, exist_ok=True)
    detail_path = out_dir / "stress_hrv_detail.csv"
    file_path = out_dir / "stress_hrv_file_summary.csv"
    group_path = out_dir / "stress_hrv_group_summary.csv"
    json_path = out_dir / "stress_hrv_summary.json"

    detail_fields = [
        "group",
        "file",
        "window_idx",
        "start_s",
        "end_s",
        "stress_probability",
        "raw_stress_index",
        "display_stress_index",
        "level",
        "reason",
        "finger_present_frac",
        "spo2_valid_frac",
        "hr_valid_frac",
        "finger_score_min",
        "finger_score_median",
        "ir_rms_median",
        "ppi_candidate_count",
        "ppi_rejected_count",
        "ppi_artifact_frac",
        *stress.MODEL_FEATURES,
    ]
    with detail_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=detail_fields)
        writer.writeheader()
        writer.writerows(detail_rows)

    file_fields = [
        "group",
        "file",
        "rows",
        "duration_s",
        "finger_rows",
        "valid_peak_rows",
        "peak_span_s",
        "windows",
        "displayed_windows",
        "raw_median",
        "display_median",
        "display_max",
        "reason_counts",
        "no_window_reason",
    ]
    with file_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=file_fields)
        writer.writeheader()
        writer.writerows(file_rows)

    by_group: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in detail_rows:
        by_group[str(row["group"])].append(row)

    group_rows: list[dict[str, Any]] = []
    summary_json: dict[str, Any] = {"config": config, "groups": {}, "files": file_rows}
    for group in sorted({row["group"] for row in file_rows} | set(by_group)):
        rows = by_group.get(str(group), [])
        raw = [float(row["raw_stress_index"]) for row in rows if row["raw_stress_index"] != ""]
        displayed = [float(row["display_stress_index"]) for row in rows if row["display_stress_index"] != ""]
        reason_counts = Counter(str(row["reason"]) for row in rows)
        file_count = sum(1 for row in file_rows if row["group"] == group)
        row = {
            "group": group,
            "files": file_count,
            "windows": len(rows),
            "displayed_windows": len(displayed),
            "display_rate_pct": round(100.0 * len(displayed) / len(rows), 3) if rows else None,
            "raw_median": summarize(raw)["median"],
            "raw_max": summarize(raw)["max"],
            "display_median": summarize(displayed)["median"],
            "display_max": summarize(displayed)["max"],
            "normal_or_lower_pct": round(100.0 * sum(1 for v in displayed if v <= 59.0) / len(displayed), 3)
            if displayed
            else None,
            "medium_or_high_pct": round(100.0 * sum(1 for v in displayed if v >= 60.0) / len(displayed), 3)
            if displayed
            else None,
            "reason_counts": json.dumps(dict(reason_counts), ensure_ascii=False),
        }
        group_rows.append(row)
        summary_json["groups"][str(group)] = row

    with group_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(group_rows[0].keys()) if group_rows else [])
        if group_rows:
            writer.writeheader()
            writer.writerows(group_rows)

    json_path.write_text(json.dumps(summary_json, indent=2, ensure_ascii=False), encoding="utf-8")
    return {
        "detail_csv": str(detail_path),
        "file_summary_csv": str(file_path),
        "group_summary_csv": str(group_path),
        "summary_json": str(json_path),
    }


def main() -> int:
    args = parse_args()
    model = json.loads(args.model.read_text(encoding="utf-8"))
    metadata = model.get("metadata", {})
    window_s = float(args.window_s)
    step_s = float(args.step_s)
    min_intervals = int(args.min_intervals)
    model_window_s = float(metadata.get("window_s", window_s))
    model_min_intervals = int(metadata.get("min_intervals", min_intervals))
    if model_window_s != window_s or model_min_intervals != min_intervals:
        raise ValueError(
            "model/runtime window mismatch: "
            f"model={model_window_s:g}s/{model_min_intervals} PPI, "
            f"runtime={window_s:g}s/{min_intervals} PPI"
        )

    detail_rows: list[dict[str, Any]] = []
    file_rows: list[dict[str, Any]] = []
    for group, path in expand_group_paths(args):
        if not path.exists():
            file_rows.append(
                {
                    "group": group,
                    "file": str(path),
                    "rows": 0,
                    "duration_s": 0,
                    "finger_rows": 0,
                    "valid_peak_rows": 0,
                    "peak_span_s": 0,
                    "windows": 0,
                    "displayed_windows": 0,
                    "raw_median": None,
                    "display_median": None,
                    "display_max": None,
                    "reason_counts": json.dumps({"missing": 1}),
                    "no_window_reason": "missing",
                }
            )
            continue

        stats = file_stats(path)
        events, artifact_marks = filtered_local_events_from_processed_csv(path, args)
        windows = stress.unlabeled_windows_from_events(events, "local_ppg", str(path.parent), window_s, step_s, min_intervals)
        raw_indexes: list[float] = []
        display_indexes: list[float] = []
        reasons: Counter[str] = Counter()

        for idx, window in enumerate(windows):
            probability = stress.predict_probability(model, window.features)
            raw_index = stress.stress_index_from_probability(model, probability)
            quality = window_quality(path, window.start_s, window.end_s)
            quality.update(ppi_artifact_quality(artifact_marks, window.start_s, window.end_s))
            rejected, reason = gate_window(window.features, quality, args)
            display_index: int | None = None if rejected else raw_index
            if display_index is not None:
                display_indexes.append(float(display_index))
            raw_indexes.append(float(raw_index))
            reasons[reason] += 1

            row: dict[str, Any] = {
                "group": group,
                "file": str(path),
                "window_idx": idx,
                "start_s": round(window.start_s, 3),
                "end_s": round(window.end_s, 3),
                "stress_probability": round(probability, 6),
                "raw_stress_index": raw_index,
                "display_stress_index": "" if display_index is None else display_index,
                "level": stress.stress_level(display_index),
                "reason": reason,
                "finger_present_frac": "" if quality["finger_present_frac"] is None else round(float(quality["finger_present_frac"]), 6),
                "spo2_valid_frac": "" if quality["spo2_valid_frac"] is None else round(float(quality["spo2_valid_frac"]), 6),
                "hr_valid_frac": "" if quality["hr_valid_frac"] is None else round(float(quality["hr_valid_frac"]), 6),
                "finger_score_min": "" if quality["finger_score_min"] is None else round(float(quality["finger_score_min"]), 6),
                "finger_score_median": "" if quality["finger_score_median"] is None else round(float(quality["finger_score_median"]), 6),
                "ir_rms_median": "" if quality["ir_rms_median"] is None else round(float(quality["ir_rms_median"]), 6),
                "ppi_candidate_count": round(float(quality["ppi_candidate_count"]), 6),
                "ppi_rejected_count": round(float(quality["ppi_rejected_count"]), 6),
                "ppi_artifact_frac": round(float(quality["ppi_artifact_frac"]), 6),
            }
            row.update({feature: round(window.features[feature], 6) for feature in stress.MODEL_FEATURES})
            detail_rows.append(row)

        duration_s = (
            round(stats.last_time_s - stats.first_time_s, 3)
            if stats.first_time_s is not None and stats.last_time_s is not None
            else 0
        )
        peak_span_s = (
            round(stats.last_peak_s - stats.first_peak_s, 3)
            if stats.first_peak_s is not None and stats.last_peak_s is not None
            else 0
        )
        file_rows.append(
            {
                "group": group,
                "file": str(path),
                "rows": stats.rows,
                "duration_s": duration_s,
                "finger_rows": stats.finger_rows,
                "valid_peak_rows": stats.valid_peak_rows,
                "peak_span_s": peak_span_s,
                "windows": len(windows),
                "displayed_windows": len(display_indexes),
                "raw_median": summarize(raw_indexes)["median"],
                "display_median": summarize(display_indexes)["median"],
                "display_max": summarize(display_indexes)["max"],
                "reason_counts": json.dumps(dict(reasons), ensure_ascii=False),
                "no_window_reason": "" if windows else file_no_window_reason(stats, window_s, min_intervals),
            }
        )

    config = {
        "replay_mode": "parameter_aligned_steady_state_windows",
        "validator_sha256": sha256_file(Path(__file__)),
        "model": str(args.model),
        "model_sha256": sha256_file(args.model),
        "main_c": "Core/Src/main.c",
        "main_c_sha256": sha256_file(Path("Core/Src/main.c")),
        "ppg_glob": args.ppg_glob,
        "ppg_group": args.ppg_group,
        "window_s": window_s,
        "window_generation_step_s": step_s,
        "min_intervals": min_intervals,
        "first_update_step_s": STRESS_HRV_FIRST_UPDATE_STEP_S,
        "refresh_update_step_s": STRESS_HRV_REFRESH_UPDATE_STEP_S,
        "high_hr_bpm": STRESS_HRV_HIGH_HR_BPM,
        "quality_gate_enabled": not args.disable_quality_gate,
        "finger_present_gate": args.finger_present_gate,
        "spo2_valid_gate": args.spo2_valid_gate,
        "finger_score_min_gate": args.finger_score_min_gate,
        "p80_delta_gate_ms": args.p80_delta_gate_ms,
        "p95_delta_gate_ms": args.p95_delta_gate_ms,
        "outlier_frac_gate": args.outlier_frac_gate,
        "cv_gate": args.cv_gate,
        "ppi_filter_enabled": not args.disable_ppi_filter,
        "ppi_min_hr_ratio": args.ppi_min_hr_ratio,
        "ppi_max_hr_ratio": args.ppi_max_hr_ratio,
        "ppi_slow_min_hr_ratio": args.ppi_slow_min_hr_ratio,
        "ppi_slow_max_hr_ratio": args.ppi_slow_max_hr_ratio,
        "ppi_fast_min_hr_ratio": args.ppi_fast_min_hr_ratio,
        "ppi_fast_max_hr_ratio": args.ppi_fast_max_hr_ratio,
        "ppi_artifact_frac_gate": args.ppi_artifact_frac_gate,
    }
    paths = write_reports(args.out_dir, detail_rows, file_rows, config)

    print("Stress HRV local validation summary")
    summary = json.loads(Path(paths["summary_json"]).read_text(encoding="utf-8"))["groups"]
    for group, row in summary.items():
        print(
            f"{group}: displayed={row['displayed_windows']}/{row['windows']} "
            f"display_median={row['display_median']} display_max={row['display_max']} "
            f"reasons={row['reason_counts']}"
        )
    print("")
    print(f"wrote {paths['group_summary_csv']}")
    print(f"wrote {paths['file_summary_csv']}")
    print(f"wrote {paths['detail_csv']}")
    print(f"wrote {paths['summary_json']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
