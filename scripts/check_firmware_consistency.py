#!/usr/bin/env python3
"""Check that firmware constants, model files, reports, and docs share one snapshot."""

from __future__ import annotations

import hashlib
import json
import math
import re
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
MAIN_C = ROOT / "Core/Src/main.c"
AF_MODEL = ROOT / "training_dataset/models/af_nb_model.json"
AF_MODEL_CN = ROOT / "training_dataset/models/房颤朴素贝叶斯模型参数.json"
AF_HEADER = ROOT / "training_dataset/models/af_nb_model.h"
AF_HEADER_CN = ROOT / "training_dataset/models/房颤朴素贝叶斯模型参数_单片机头文件.h"
STRESS_MODEL = ROOT / "training_dataset/models/stress_hrv_model.json"
STRESS_MODEL_CN = ROOT / "training_dataset/models/压力HRV模型参数.json"
STRESS_HEADER = ROOT / "training_dataset/models/stress_hrv_model.h"
STRESS_HEADER_CN = ROOT / "training_dataset/models/压力HRV模型参数_单片机头文件.h"
AF_REPORT = ROOT / "training_dataset/reports/full_af_validation_20260629_current/mcu_live_af_summary.json"
STRESS_REPORT = ROOT / "training_dataset/reports/full_stress_live_validation_20260629_current/stress_hrv_summary.json"
STRESS_TRAIN_MODEL = ROOT / "training_dataset/reports/full_stress_train_validation_20260629_current/models/stress_hrv_model.json"
AF_VALIDATOR = ROOT / "scripts/validate_mcu_af_live.py"
STRESS_VALIDATOR = ROOT / "scripts/validate_stress_hrv_live.py"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def c_define(source: str, name: str) -> float:
    match = re.search(rf"^#define\s+{re.escape(name)}\s+([-+]?\d+(?:\.\d+)?)", source, re.MULTILINE)
    if not match:
        raise ValueError(f"missing C define: {name}")
    return float(match.group(1))


def c_float(source: str, name: str) -> float:
    match = re.search(
        rf"static const float\s+{re.escape(name)}\s*=\s*([-+]?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?)f;",
        source,
    )
    if not match:
        raise ValueError(f"missing C float: {name}")
    return float(match.group(1))


def c_array(source: str, name: str) -> list[float]:
    match = re.search(
        rf"static const float\s+{re.escape(name)}(?:\[[^\]]+\])+\s*=\s*\{{(?P<body>.*?)\}};",
        source,
        re.DOTALL,
    )
    if not match:
        raise ValueError(f"missing C array: {name}")
    return [
        float(item)
        for item in re.findall(r"[-+]?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?", match.group("body"))
    ]


def close(left: float, right: float) -> bool:
    return math.isclose(float(left), float(right), rel_tol=1e-7, abs_tol=1e-6)


def main() -> int:
    errors: list[str] = []
    source = MAIN_C.read_text(encoding="utf-8")
    af_model = load_json(AF_MODEL)
    stress_model = load_json(STRESS_MODEL)
    stress_train_model = load_json(STRESS_TRAIN_MODEL)
    af_report = load_json(AF_REPORT)["config"]
    stress_report = load_json(STRESS_REPORT)["config"]

    def expect(label: str, actual: Any, expected: Any) -> None:
        if isinstance(actual, (int, float)) and isinstance(expected, (int, float)):
            valid = close(float(actual), float(expected))
        else:
            valid = actual == expected
        if not valid:
            errors.append(f"{label}: actual={actual!r}, expected={expected!r}")

    af_checks = {
        "af_min_ppi_count": c_define(source, "AF_MIN_PPI_COUNT"),
        "af_window_target_ms": c_define(source, "AF_WINDOW_TARGET_MS"),
        "af_fast_step_s": c_define(source, "AF_RISK_FAST_STEP_MS") / 1000.0,
        "af_slow_step_s": c_define(source, "AF_RISK_SLOW_STEP_MS") / 1000.0,
        "af_stable_risk_threshold": c_define(source, "AF_STABLE_RISK_THRESHOLD_PERCENT"),
        "af_max_up_jump_percent": c_define(source, "AF_MAX_UP_JUMP_PERCENT"),
        "af_live_hr_tolerance_bpm": c_define(source, "AF_LIVE_HR_TOLERANCE_BPM"),
        "hr_jump_gate_bpm": c_define(source, "PPG_HR_MAX_DISPLAY_JUMP_BPM"),
        "hr_jump_accept_s": c_define(source, "PPG_HR_JUMP_ACCEPT_TIMEOUT_MS") / 1000.0,
    }
    for key, expected in af_checks.items():
        expect(f"AF report {key}", af_report.get(key), expected)
    expect("AF report main.c hash", af_report.get("main_c_sha256"), sha256_file(MAIN_C))
    expect("AF report model hash", af_report.get("model_sha256"), sha256_file(AF_MODEL))
    expect("AF report validator hash", af_report.get("validator_sha256"), sha256_file(AF_VALIDATOR))
    expect("English/Chinese AF JSON", AF_MODEL.read_bytes(), AF_MODEL_CN.read_bytes())
    expect("English/Chinese AF header", AF_HEADER.read_bytes(), AF_HEADER_CN.read_bytes())

    af_priors = c_array(source, "af_nb_class_log_prior")
    expect("AF normal class prior", af_model["class_log_prior"]["0"], af_priors[0])
    expect("AF positive class prior", af_model["class_log_prior"]["1"], af_priors[1])
    af_bin_edges: list[float] = []
    af_normal_prob: list[float] = []
    af_positive_prob: list[float] = []
    for feature in af_model["features"]:
        edges = list(af_model["feature_bins"][feature])
        af_bin_edges.extend(edges + [0.0] * (9 - len(edges)))
        normal = list(af_model["feature_log_prob"][feature]["0"])
        positive = list(af_model["feature_log_prob"][feature]["1"])
        af_normal_prob.extend(normal + [0.0] * (10 - len(normal)))
        af_positive_prob.extend(positive + [0.0] * (10 - len(positive)))
    for label, actual, expected in (
        ("AF bin edges", af_bin_edges, c_array(source, "af_nb_bin_edges")),
        ("AF normal probabilities", af_normal_prob, c_array(source, "af_nb_log_prob_normal")),
        ("AF positive probabilities", af_positive_prob, c_array(source, "af_nb_log_prob_af")),
    ):
        expect(f"{label} length", len(actual), len(expected))
        for index, (actual_value, expected_value) in enumerate(zip(actual, expected)):
            expect(f"{label}[{index}]", actual_value, expected_value)

    stress_checks = {
        "window_s": c_define(source, "STRESS_HRV_WINDOW_TARGET_MS") / 1000.0,
        "min_intervals": c_define(source, "STRESS_HRV_MIN_INTERVAL_COUNT"),
        "first_update_step_s": c_define(source, "STRESS_HRV_FIRST_STEP_MS") / 1000.0,
        "refresh_update_step_s": c_define(source, "STRESS_HRV_REFRESH_STEP_MS") / 1000.0,
        "high_hr_bpm": c_define(source, "STRESS_HRV_HIGH_HR_BPM"),
    }
    for key, expected in stress_checks.items():
        expect(f"Stress report {key}", stress_report.get(key), expected)
    expect(
        "Stress report replay mode",
        stress_report.get("replay_mode"),
        "parameter_aligned_steady_state_windows",
    )
    expect(
        "Stress report window generation step",
        stress_report.get("window_generation_step_s"),
        stress_checks["refresh_update_step_s"],
    )
    expect("Stress report main.c hash", stress_report.get("main_c_sha256"), sha256_file(MAIN_C))
    expect("Stress report model hash", stress_report.get("model_sha256"), sha256_file(STRESS_MODEL))
    expect("Stress report validator hash", stress_report.get("validator_sha256"), sha256_file(STRESS_VALIDATOR))
    expect("Stress model metadata window", stress_model["metadata"]["window_s"], stress_checks["window_s"])
    expect("Stress model metadata step", stress_model["metadata"]["step_s"], stress_checks["first_update_step_s"])
    expect("Stress model metadata minimum", stress_model["metadata"]["min_intervals"], stress_checks["min_intervals"])

    array_pairs = {
        "stress_hrv_scaler_mean": "scaler_mean",
        "stress_hrv_scaler_scale": "scaler_scale",
        "stress_hrv_coef": "coef",
    }
    for c_name, json_name in array_pairs.items():
        c_values = c_array(source, c_name)
        json_values = stress_model[json_name]
        expect(f"{c_name} length", len(json_values), len(c_values))
        for index, (c_value, json_value) in enumerate(zip(c_values, json_values)):
            expect(f"{c_name}[{index}]", json_value, c_value)

    scalar_pairs = {
        "stress_hrv_intercept": ("intercept", stress_model),
        "stress_hrv_prob_nonstress_median": ("prob_nonstress_median", stress_model["index_calibration"]),
        "stress_hrv_prob_stress_median": ("prob_stress_median", stress_model["index_calibration"]),
        "stress_hrv_index_nonstress_target": ("index_nonstress_target", stress_model["index_calibration"]),
        "stress_hrv_index_stress_target": ("index_stress_target", stress_model["index_calibration"]),
        "stress_hrv_display_index_bias": ("display_index_bias", stress_model["index_calibration"]),
    }
    for c_name, (json_name, container) in scalar_pairs.items():
        expect(c_name, container[json_name], c_float(source, c_name))

    expect("English/Chinese Stress JSON", STRESS_MODEL.read_bytes(), STRESS_MODEL_CN.read_bytes())
    expect("English/Chinese Stress header", STRESS_HEADER.read_bytes(), STRESS_HEADER_CN.read_bytes())
    for key in ("scaler_mean", "scaler_scale", "coef", "intercept", "metadata", "index_calibration"):
        expect(f"formal/training Stress model {key}", stress_model[key], stress_train_model[key])

    stale_tokens = (
        "full_af_validation_20260617_current",
        "full_stress_live_validation_20260617_current",
        "full_stress_train_validation_20260617_current",
        "--step-s 60",
    )
    for path in ROOT.rglob("*.md"):
        if any(part in {"build", "output", "tools"} for part in path.parts):
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        for token in stale_tokens:
            if token in text:
                errors.append(f"stale documentation token {token!r} in {path.relative_to(ROOT)}")

    if errors:
        print("Firmware consistency check FAILED")
        for error in errors:
            print(f"- {error}")
        return 1

    print("Firmware consistency check PASSED")
    print("- AF firmware constants and embedded weights match model/report artifacts")
    print("- Stress firmware constants and embedded weights match model/report artifacts")
    print("- Current report paths and documentation contain no known stale tokens")
    return 0


if __name__ == "__main__":
    sys.exit(main())
