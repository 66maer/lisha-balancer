"""Read-only audit of automatic grid detection on additional resize paths.

The earlier quick audit is reused verbatim; its saved bounds cannot recover
per-cell geometry, so the new variants carry the full IoU/error measurements.
Stored layouts are comparison references, not independent pixel ground truth.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys
import time

import cv2
import numpy as np
from PySide6.QtCore import QRectF

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from lisha_overlay import grid_detection
from audit_recognition_scales import load_case, qimage, resize


def _hash(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _rect(rect):
    return [rect.x(), rect.y(), rect.width(), rect.height()]


def _iou(first, second):
    intersection = first.intersected(second)
    area = max(0., intersection.width()) * max(0., intersection.height())
    total = first.width()*first.height() + second.width()*second.height() - area
    return area / total if total else 0.


def _metrics(layout, expected, width, height, sx, sy):
    frame = QRectF(0, 0, width, height)
    detected = {cell.key: cell.rect for cell in layout.cells(frame)}
    reference = {cell.key: cell.rect for cell in expected.cells(frame)}
    rows = []
    for key, truth in reference.items():
        if key not in detected:
            continue
        actual = detected[key]
        dx, dy = actual.center().x()-truth.center().x(), actual.center().y()-truth.center().y()
        errors = np.array([actual.left()-truth.left(), actual.top()-truth.top(),
                           actual.right()-truth.right(), actual.bottom()-truth.bottom()])
        rows.append({"key": key, "iou": _iou(actual, truth),
                     "center_error_px": float(np.hypot(dx, dy)),
                     "center_error_source_px": float(np.hypot(dx/sx, dy/sy)),
                     "mean_edge_error_px": float(np.abs(errors).mean()),
                     "mean_edge_error_source_px": float((np.abs(errors)/[sx,sy,sx,sy]).mean()),
                     "detected": _rect(actual), "reference": _rect(truth)})
    return {"cell_count": len(detected), "matching_keys": len(rows),
            "median_cell_iou": float(np.median([row["iou"] for row in rows])) if rows else None,
            "minimum_cell_iou": min((row["iou"] for row in rows), default=None),
            "median_center_error_px": float(np.median([row["center_error_px"] for row in rows])) if rows else None,
            "median_center_error_source_px": float(np.median([row["center_error_source_px"] for row in rows])) if rows else None,
            "median_mean_edge_error_source_px": float(np.median([row["mean_edge_error_source_px"] for row in rows])) if rows else None,
            "cells": rows}


def _detect(pixels):
    """Observe existing private pipeline stages without changing their values."""
    stages = {}
    columns_fn, hypotheses_fn = grid_detection._column_candidates, grid_detection._hypotheses

    def columns(*args):
        result = columns_fn(*args)
        stages["candidate_column_count"] = len(result)
        stages["candidate_column_left_positions"] = sorted({round(column.left, 1) for column in result})
        return result

    def hypotheses(*args):
        result = hypotheses_fn(*args)
        stages["complete_hypothesis_count"] = len(result)
        stages["top_hypothesis_scores"] = [float(item[0]) for item in result[:3]]
        return result

    grid_detection._column_candidates, grid_detection._hypotheses = columns, hypotheses
    try:
        start = time.perf_counter()
        result = grid_detection.detect_team_grid(qimage(pixels))
        elapsed = (time.perf_counter()-start)*1000
    finally:
        grid_detection._column_candidates, grid_detection._hypotheses = columns_fn, hypotheses_fn
    reason = None
    if result is None:
        scores = stages.get("top_hypothesis_scores", [])
        if not stages.get("candidate_column_count"):
            reason = "no_supported_five_row_columns"
        elif not scores:
            reason = "columns_do_not_form_one_complete_five_column_layout"
        elif scores[0] < .75:
            reason = "best_complete_layout_below_0.75_confidence"
        elif len(scores) > 1 and scores[1] >= .75:
            reason = "multiple_supported_complete_layouts_are_ambiguous"
        else:
            reason = "detector_rejected_input_or_empty_bounds"
    return result, elapsed, stages, reason


def add_comparison_breakdowns(report):
    """Keep approximate default-grid comparisons out of calibrated metrics."""
    rows = [*report["prior_quick"], *report["new_cases"]]
    calibrated = [item for item in report["new_cases"]
                  if item["detected"] and not item["reference_is_approximate"]]
    report["summary"]["by_variant"] = {
        name: {"cases": sum(item["variant"] == name for item in rows),
               "detected": sum(item["variant"] == name and item["detected"] for item in rows)}
        for name in dict.fromkeys(item["variant"] for item in rows)}
    report["summary"]["new_accepted_saved_layout_only"] = {
        "cases": len(calibrated),
        "median_center_error_source_px": float(np.median([item["metrics"]["median_center_error_source_px"] for item in calibrated])),
        "median_cell_iou": float(np.median([item["metrics"]["median_cell_iou"] for item in calibrated])),
        "maximum_median_center_error_source_px": max(item["metrics"]["median_center_error_source_px"] for item in calibrated)}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=ROOT/"artifacts"/"grid-scale-audit.json")
    parser.add_argument("--quick", type=Path, default=ROOT/"artifacts"/"reference-grid-scale-private.json")
    args = parser.parse_args()
    corpus_path = ROOT/"artifacts"/"recognition-corpus-private.json"
    corpus = json.loads(corpus_path.read_text(encoding="utf-8"))["cases"]
    old = json.loads(args.quick.read_text(encoding="utf-8"))
    source_path = Path(grid_detection.__file__)
    fingerprint = _hash(source_path)
    report = {"schema": 1, "pipeline": "grid_detection.detect_team_grid",
              "implementation_sha256": fingerprint, "corpus_sha256": _hash(corpus_path),
              "prior_quick_sha256": _hash(args.quick),
              "method": "Detect from each full supplied image without frozen-layout hints; compare results to the same saved layouts used by the recognition audit.",
              "limitations": [
                  "Seven real team-panel crops, not seven independently captured full desktops; synthetic resizes are not new real scenes.",
                  "Stored layouts are existing calibration references and may contain their own alignment error; no manually annotated pixel ground truth was created here.",
                  "original has no saved layout in the corpus and uses TeamLayout defaults; its error values are approximate calibration differences, not detector ground-truth error.",
                  "Crops omit surrounding game UI. They cannot establish false-positive or ambiguity behavior on a full desktop containing other panels.",
                  "A returned 25-cell layout alone does not prove correct alignment; IoU and center errors are reported separately.",
                  "Prior quick records saved bounds only, so their per-cell IoU cannot be reconstructed without rerunning; they are reused without rerunning."
              ], "prior_quick": old, "new_cases": []}
    for case in corpus:
        filename, original, reference, _ = load_case(case)
        oh, ow = original.shape[:2]
        reference_source = case.get("layout") or (case.get("settings")+":team_layout" if case.get("settings") else "TeamLayout() defaults (approximate)")
        context = QRectF(0,0,ow,oh)
        bounds = QRectF()
        for cell in reference.cells(context):
            bounds = bounds.united(cell.rect)
        margins = [bounds.left(), bounds.top(), ow-bounds.right(), oh-bounds.bottom()]
        variants = [("scale_0.65", resize(original,.65), [{"resize":.65}]),
                    ("scale_1.5", resize(original,1.5), [{"resize":1.5}]),
                    ("video_0.8_display_1", cv2.resize(resize(original,.8),(ow,oh),interpolation=cv2.INTER_CUBIC),
                     [{"resize":.8},{"resize_to_original_size":True}])]
        for name, pixels, operations in variants:
            height, width = pixels.shape[:2]
            result, elapsed, stages, reason = _detect(pixels)
            entry = {"case":case["id"],"variant":name,"source":str(filename),
                     "source_size":[ow,oh],"image_size":[width,height],
                     "reference_source":reference_source,"reference_is_approximate":not ("layout" in case or "settings" in case),
                     "reference_context_margins_source_px":margins,"operations":operations,
                     "detected":result is not None,"ms":round(elapsed,3),"failure_reason":reason,
                     "stages":stages}
            if result is not None:
                entry.update(confidence=result.confidence, bounds=_rect(result.bounds),
                             formation=result.layout.grid_options["formation"],
                             metrics=_metrics(result.layout,reference,width,height,width/ow,height/oh))
            report["new_cases"].append(entry)
            print(case["id"],name,"25" if result is not None else "reject",round(elapsed,1),flush=True)
    assert fingerprint == _hash(source_path), "Read-only audit must not alter the detector"
    new = report["new_cases"]
    all_rows = [*old,*new]
    accepted = [item for item in new if item["detected"]]
    report["summary"] = {"real_scenes":len(corpus),"prior_quick_cases":len(old),
                         "prior_detected":sum(item["detected"] for item in old),
                         "additional_cases":len(new),"additional_detected":len(accepted),
                         "total_cases":len(all_rows),"total_detected":sum(item["detected"] for item in all_rows),
                         "accepted_25_cells":sum(item["metrics"]["cell_count"] == 25 for item in accepted),
                         "accepted_median_center_error_source_px":float(np.median([item["metrics"]["median_center_error_source_px"] for item in accepted])),
                         "accepted_median_cell_iou":float(np.median([item["metrics"]["median_cell_iou"] for item in accepted])),
                         "failure_counts":{reason:sum(item.get("failure_reason")==reason for item in new) for reason in sorted({item["failure_reason"] for item in new if item["failure_reason"]})}}
    add_comparison_breakdowns(report)
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps(report["summary"],ensure_ascii=False),flush=True)


if __name__ == "__main__":
    main()
