#!/usr/bin/env python3
# 中文说明：
#   生成不同程度的模拟 PPI/RR 间期序列，并用已训练的朴素贝叶斯模型计算 AF 风险。
#   它不参与训练，只用于行为 sanity check：正常节律应低风险，越来越不规则的节律应逐步升高。
#   输入数据类型和单片机可获得的数据一致，都是一段 pulse-to-pulse interval 毫秒列表。
#   输出目录通常是 training_dataset/reports/simulated_ppi。
"""Simulate PPI/RR windows and evaluate AF-risk with the trained NB model.

The generated inputs are intentionally the same kind of data the MCU can
provide later: a short list of pulse-to-pulse intervals in milliseconds. This is
not used for training; it is a behavior sanity check for the exported weights.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import random
from pathlib import Path
from typing import Callable

import train_af_naive_bayes as afnb


ScenarioFn = Callable[[random.Random, float], list[float]]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--model",
        type=Path,
        default=Path("training_dataset/models/af_nb_model.json"),
        help="Trained model JSON",
    )
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=Path("training_dataset/reports/simulated_ppi"),
        help="Output directory",
    )
    parser.add_argument("--window-s", type=float, default=30.0)
    parser.add_argument("--samples-per-scenario", type=int, default=200)
    parser.add_argument("--seed", type=int, default=411)
    return parser.parse_args()


def clipped(value: float, low: float = 320.0, high: float = 1800.0) -> float:
    return max(low, min(high, value))


def fill_window(rng: random.Random, window_s: float, next_rr: Callable[[], float]) -> list[float]:
    intervals: list[float] = []
    total_s = 0.0
    while total_s < window_s:
        rr = clipped(next_rr())
        intervals.append(rr)
        total_s += rr / 1000.0
    return intervals


def normal_stable(rng: random.Random, window_s: float) -> list[float]:
    baseline = rng.uniform(720.0, 850.0)
    return fill_window(rng, window_s, lambda: rng.gauss(baseline, 18.0))


def normal_respiratory(rng: random.Random, window_s: float) -> list[float]:
    baseline = rng.uniform(700.0, 880.0)
    phase = rng.uniform(0.0, math.tau)
    idx = 0

    def next_rr() -> float:
        nonlocal idx
        idx += 1
        wave = 35.0 * math.sin(phase + idx * 0.55)
        return rng.gauss(baseline + wave, 18.0)

    return fill_window(rng, window_s, next_rr)


def normal_single_outlier(rng: random.Random, window_s: float) -> list[float]:
    intervals = normal_respiratory(rng, window_s)
    if len(intervals) > 8:
        idx = rng.randrange(4, len(intervals) - 4)
        intervals[idx] = clipped(intervals[idx] * rng.choice([0.55, 1.65]))
    return intervals


def ectopy_low(rng: random.Random, window_s: float) -> list[float]:
    baseline = rng.uniform(720.0, 880.0)
    intervals: list[float] = []
    total_s = 0.0
    while total_s < window_s:
        if rng.random() < 0.08:
            short = clipped(baseline * rng.uniform(0.45, 0.65))
            comp = clipped(baseline * rng.uniform(1.25, 1.55))
            intervals.extend([short, comp])
            total_s += (short + comp) / 1000.0
        else:
            rr = clipped(rng.gauss(baseline, 22.0))
            intervals.append(rr)
            total_s += rr / 1000.0
    return intervals


def ectopy_high(rng: random.Random, window_s: float) -> list[float]:
    baseline = rng.uniform(700.0, 880.0)
    intervals: list[float] = []
    total_s = 0.0
    while total_s < window_s:
        if rng.random() < 0.18:
            short = clipped(baseline * rng.uniform(0.42, 0.65))
            comp = clipped(baseline * rng.uniform(1.25, 1.80))
            intervals.extend([short, comp])
            total_s += (short + comp) / 1000.0
        else:
            rr = clipped(rng.gauss(baseline, 35.0))
            intervals.append(rr)
            total_s += rr / 1000.0
    return intervals


def af_like_mild(rng: random.Random, window_s: float) -> list[float]:
    mean_rr = rng.uniform(680.0, 860.0)
    return fill_window(
        rng,
        window_s,
        lambda: rng.lognormvariate(math.log(mean_rr), 0.13) + rng.gauss(0.0, 30.0),
    )


def irregular_very_low(rng: random.Random, window_s: float) -> list[float]:
    mean_rr = rng.uniform(700.0, 880.0)
    return fill_window(
        rng,
        window_s,
        lambda: rng.lognormvariate(math.log(mean_rr), 0.055) + rng.gauss(0.0, 12.0),
    )


def irregular_low(rng: random.Random, window_s: float) -> list[float]:
    mean_rr = rng.uniform(700.0, 880.0)
    return fill_window(
        rng,
        window_s,
        lambda: rng.lognormvariate(math.log(mean_rr), 0.085) + rng.gauss(0.0, 18.0),
    )


def irregular_borderline(rng: random.Random, window_s: float) -> list[float]:
    mean_rr = rng.uniform(680.0, 860.0)
    return fill_window(
        rng,
        window_s,
        lambda: rng.lognormvariate(math.log(mean_rr), 0.105) + rng.gauss(0.0, 24.0),
    )


def af_like_moderate(rng: random.Random, window_s: float) -> list[float]:
    mean_rr = rng.uniform(620.0, 820.0)

    def next_rr() -> float:
        rr = rng.lognormvariate(math.log(mean_rr), 0.22)
        if rng.random() < 0.18:
            rr += rng.choice([-140.0, 180.0]) + rng.gauss(0.0, 45.0)
        return rr

    return fill_window(rng, window_s, next_rr)


def af_like_high(rng: random.Random, window_s: float) -> list[float]:
    mean_rr = rng.uniform(560.0, 800.0)

    def next_rr() -> float:
        rr = rng.lognormvariate(math.log(mean_rr), 0.33)
        if rng.random() < 0.30:
            rr += rng.choice([-190.0, 260.0]) + rng.gauss(0.0, 70.0)
        return rr

    return fill_window(rng, window_s, next_rr)


SCENARIOS: list[tuple[str, str, ScenarioFn]] = [
    ("normal_stable", "Normal stable sinus-like PPI", normal_stable),
    ("normal_respiratory", "Normal with smooth respiratory sinus variation", normal_respiratory),
    ("normal_single_outlier", "Normal with one isolated bad/missed PPG interval", normal_single_outlier),
    ("ectopy_low", "Low ectopy-like early/compensatory pairs", ectopy_low),
    ("ectopy_high", "Frequent ectopy-like early/compensatory pairs", ectopy_high),
    ("irregular_very_low", "Very low random irregularity", irregular_very_low),
    ("irregular_low", "Low random irregularity", irregular_low),
    ("irregular_borderline", "Borderline random irregularity", irregular_borderline),
    ("af_like_mild", "Mild AF-like random irregularity", af_like_mild),
    ("af_like_moderate", "Moderate AF-like random irregularity", af_like_moderate),
    ("af_like_high", "High AF-like random irregularity", af_like_high),
]


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


def main() -> int:
    args = parse_args()
    model = json.loads(args.model.read_text(encoding="utf-8"))
    args.out_dir.mkdir(parents=True, exist_ok=True)
    rng = random.Random(args.seed)

    detail_rows: list[dict[str, object]] = []
    summary_rows: list[dict[str, object]] = []
    summary_json: dict[str, object] = {
        "model": str(args.model),
        "window_s": args.window_s,
        "samples_per_scenario": args.samples_per_scenario,
        "seed": args.seed,
        "scenarios": {},
    }

    for scenario_name, description, fn in SCENARIOS:
        risks: list[float] = []
        valid_counts: list[float] = []
        for sample_idx in range(args.samples_per_scenario):
            intervals = fn(rng, args.window_s)
            features = afnb.compute_features(intervals)
            if features is None:
                continue
            risk, scores = afnb.predict_one(model, features)
            risks.append(risk)
            valid_counts.append(features["valid_interval_count"])

            row: dict[str, object] = {
                "scenario": scenario_name,
                "sample_idx": sample_idx,
                "risk_percent": round(risk, 6),
                "score_normal": round(scores[afnb.CLASS_NORMAL], 6),
                "score_af": round(scores[afnb.CLASS_AF], 6),
            }
            for feature in afnb.MODEL_FEATURES:
                row[feature] = round(features[feature], 6)
            detail_rows.append(row)

        risk_summary = summarize(risks)
        summary = {
            "scenario": scenario_name,
            "description": description,
            **{f"risk_{k}": v for k, v in risk_summary.items()},
            "valid_interval_median": round(afnb.median(valid_counts), 3) if valid_counts else None,
            "high_risk_ge_50_pct": round(100.0 * sum(1 for risk in risks if risk >= 50.0) / len(risks), 3)
            if risks
            else None,
            "high_risk_ge_80_pct": round(100.0 * sum(1 for risk in risks if risk >= 80.0) / len(risks), 3)
            if risks
            else None,
        }
        summary_rows.append(summary)
        summary_json["scenarios"][scenario_name] = summary

    detail_path = args.out_dir / "simulated_ppi_risk_detail.csv"
    summary_csv_path = args.out_dir / "simulated_ppi_risk_summary.csv"
    summary_json_path = args.out_dir / "simulated_ppi_risk_summary.json"

    if detail_rows:
        with detail_path.open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=list(detail_rows[0].keys()))
            writer.writeheader()
            writer.writerows(detail_rows)

    with summary_csv_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(summary_rows[0].keys()))
        writer.writeheader()
        writer.writerows(summary_rows)

    summary_json_path.write_text(json.dumps(summary_json, indent=2, ensure_ascii=False), encoding="utf-8")

    print("Scenario risk summary")
    for row in summary_rows:
        print(
            f"{row['scenario']}: median={row['risk_median']} mean={row['risk_mean']} "
            f"p25={row['risk_p25']} p75={row['risk_p75']} ge80={row['high_risk_ge_80_pct']}%"
        )
    print("")
    print(f"wrote {summary_csv_path}")
    print(f"wrote {summary_json_path}")
    print(f"wrote {detail_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
