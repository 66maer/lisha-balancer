"""Audit complete team-panel recognition after resolution/video changes.

Only full-image resampling/compression is applied. No labels or icon strokes
are painted, and translated cases are padded so that no source pixels vanish.
Synthetic variants and repeated passes are never counted as real new scenes.
"""
from __future__ import annotations

import argparse
import copy
import csv
from dataclasses import dataclass
import hashlib
import importlib
import inspect
import json
from pathlib import Path
import sys
import time
from typing import Iterator

import cv2
import numpy as np
from PySide6.QtCore import QRectF
from PySide6.QtGui import QImage

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from lisha_overlay.team_layout import TeamLayout

CALIBRATION_IDS = frozenset({"original", "many_black", "isolation"})
SMOKE_IDS = frozenset({"original", "health_flash"})


def implementation_fingerprints(factory):
    """Record the engine, its deployed recognizer and bundled model assets."""
    paths = [Path(__file__).resolve()]
    source = inspect.getsourcefile(factory)
    if source:
        paths.append(Path(source))
    runtime = ROOT / "src" / "lisha_overlay"
    paths.extend(runtime / name for name in (
        "learned_recognition.py", "learned_geometry.py", "learned_rois.py",
        "occlusion.py", "death_word.py", "scene_words.py"))
    paths.extend(path for path in (runtime / "assets" / "learned").iterdir() if path.is_file())
    paths.extend((runtime / "assets" / "buffs").glob("ui_dead*.png"))
    return {str(path.relative_to(ROOT)) if path.is_relative_to(ROOT) else str(path):
            hashlib.sha256(path.read_bytes()).hexdigest()
            for path in paths if path.is_file()}


@dataclass
class Variant:
    name: str
    image: np.ndarray
    layout: TeamLayout
    operations: list[dict]
    final_scale: tuple[float, float] = (1., 1.)
    information_scale: tuple[float, float] = (1., 1.)


def read_json(filename):
    return json.loads((ROOT / filename).read_text(encoding="utf-8"))


def load_case(case):
    filename = ROOT / case["image"]
    image = cv2.imdecode(np.fromfile(filename, np.uint8), cv2.IMREAD_COLOR)
    if image is None:
        raise ValueError(f"Could not read corpus image: {filename}")
    if "settings" in case:
        layout = TeamLayout.from_dict(read_json(case["settings"])["team_layout"])
    elif "layout" in case:
        layout = TeamLayout.from_dict(read_json(case["layout"]))
    else:
        layout = TeamLayout()
    if "expected_file" in case:
        expected = read_json(case["expected_file"])
    else:
        expected = []
        for index, token in enumerate(" ".join(case["rows"]).split()):
            expected.append({"key": f"g{index//5+1}p{index%5+1}",
                             "color": "red" if token.startswith("r") else "black" if token.startswith("b") else "none",
                             "stacks": int(token[1:]) if token[0] in "rb" else 0,
                             "alive": token != "dead"})
    return filename, image, layout, expected


def qimage(image):
    return QImage(image.data, image.shape[1], image.shape[0], image.strides[0],
                  QImage.Format.Format_BGR888).copy()


def resize(image, sx, sy=None):
    sy = sx if sy is None else sy
    size = max(1, round(image.shape[1]*sx)), max(1, round(image.shape[0]*sy))
    interpolation = cv2.INTER_AREA if sx < 1 and sy < 1 else cv2.INTER_CUBIC
    return cv2.resize(image, size, interpolation=interpolation)


def jpeg(image, quality):
    ok, encoded = cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, quality])
    if not ok:
        raise ValueError("JPEG diagnostic encoding failed")
    return cv2.imdecode(encoded, cv2.IMREAD_COLOR)


def padded_translation(image, layout, dx, dy, padding=3):
    """Translate within a larger canvas; reproject all layout coordinates."""
    if padding < max(abs(dx), abs(dy)):
        raise ValueError("Padding cannot crop translated source pixels")
    height, width = image.shape[:2]
    out_w, out_h = width+2*padding, height+2*padding
    x, y = padding+dx, padding+dy
    moved = cv2.warpAffine(image, np.float32([[1,0,x],[0,1,y]]), (out_w,out_h),
                           flags=cv2.INTER_LINEAR, borderValue=(16,24,32))
    data = copy.deepcopy(layout.to_dict())
    for group in data["groups"]:
        gx, gy, gw, gh = group["rect"]
        group["rect"] = [(gx*width+x)/out_w, (gy*height+y)/out_h,
                         gw*width/out_w, gh*height/out_h]
    options = data["grid_options"]
    options.update(left=(options["left"]*width+x)/out_w,
                   right=(options["right"]*width+out_w-width-x)/out_w,
                   top=(options["top"]*height+y)/out_h,
                   bottom=(options["bottom"]*height+out_h-height-y)/out_h,
                   column_gap=options["column_gap"]*width/out_w,
                   row_gap=options["row_gap"]*height/out_h,
                   footer=options["footer"]*height/out_h)
    return moved, TeamLayout.from_dict(data)


def resolution_variants(image, layout, full=False) -> Iterator[Variant]:
    yield Variant("raw", image, layout, [])
    scales = (.5,.65,.8,1.25,1.5) if full else (.8,1.25)
    for scale in scales:
        pixels = resize(image, scale)
        actual = pixels.shape[1]/image.shape[1], pixels.shape[0]/image.shape[0]
        yield Variant(f"scale_{scale:g}", pixels, layout,
                      [{"resize": actual}], actual, tuple(min(1.,s) for s in actual))
    # The displayed frame may recover its old dimensions but cannot recover
    # information discarded by the smaller recording resolution.
    chains = ((.65,1.),(.8,1.25)) if full else ((.65,1.),)
    for video_scale, display_scale in chains:
        video = resize(image, video_scale)
        final = cv2.resize(video, (round(image.shape[1]*display_scale),round(image.shape[0]*display_scale)),
                           interpolation=cv2.INTER_CUBIC)
        actual_video = video.shape[1]/image.shape[1], video.shape[0]/image.shape[0]
        actual_final = final.shape[1]/image.shape[1], final.shape[0]/image.shape[0]
        yield Variant(f"video_{video_scale:g}_display_{display_scale:g}",final,layout,
                      [{"resize":actual_video},{"resize_to_display_scale":actual_final}],
                      actual_final,tuple(min(1.,v,d) for v,d in zip(actual_video,actual_final)))
    if not full:
        return
    # Many players/recorders use bilinear sampling rather than area/cubic.
    # Keep both paths explicit: identical dimensions can contain different ink.
    video = cv2.resize(image,(round(image.shape[1]*.8),round(image.shape[0]*.8)),
                       interpolation=cv2.INTER_LINEAR)
    linear_scale = video.shape[1]/image.shape[1],video.shape[0]/image.shape[0]
    yield Variant("linear_scale_0.8",video,layout,
                  [{"resize":linear_scale,"interpolation":"linear"}],linear_scale,linear_scale)
    restored = cv2.resize(video,(image.shape[1],image.shape[0]),interpolation=cv2.INTER_LINEAR)
    yield Variant("linear_video_0.8_display_1",restored,layout,
                  [{"resize":linear_scale,"interpolation":"linear"},
                   {"resize_to_display_scale":[1.,1.],"interpolation":"linear"}],
                  (1.,1.),linear_scale)
    pixels = resize(image,1.10,.95)
    actual = pixels.shape[1]/image.shape[1],pixels.shape[0]/image.shape[0]
    yield Variant("anisotropic_1.10_0.95",pixels,layout,[{"resize":actual}],actual,
                  tuple(min(1.,s) for s in actual))
    for quality in (85,70):
        yield Variant(f"jpeg_{quality}",jpeg(image,quality),layout,[{"jpeg_quality":quality}])
    for sigma in (.35,.6):
        yield Variant(f"blur_{sigma:g}",cv2.GaussianBlur(image,(5,5),sigma),layout,[{"gaussian_sigma":sigma}])
    for dx,dy in ((1.,1.),(.5,.5)):
        pixels,projected = padded_translation(image,layout,dx,dy)
        yield Variant(f"padded_translation_{dx:g}_{dy:g}",pixels,projected,
                      [{"padded_translation":[dx,dy],"padding":3}])
    video = resize(image,.8)
    video = jpeg(video,85)
    pixels = cv2.resize(video,(image.shape[1],image.shape[0]),interpolation=cv2.INTER_CUBIC)
    scale = video.shape[1]/image.shape[1],video.shape[0]/image.shape[0]
    yield Variant("video_0.8_jpeg85_display_1",pixels,layout,
                  [{"resize":scale},{"jpeg_quality":85},{"resize_to_display_scale":[1.,1.]}],
                  (1.,1.),scale)


def score_players(players, expected, diagnostics):
    actual_by_key = {player.player_key:player for player in players}
    expected_by_key = {truth["key"]:truth for truth in expected}
    missing = sorted(set(expected_by_key)-set(actual_by_key))
    extra = sorted(set(actual_by_key)-set(expected_by_key))
    duplicate_count = len(players)-len(actual_by_key)
    differences = []
    wrong_accepted = unknown = isolation_unknown = 0
    for key,truth in expected_by_key.items():
        if key not in actual_by_key:
            continue
        actual = actual_by_key[key]
        uncertain = actual.alive and (actual.color == "unknown" or actual.stacks is None)
        unknown += int(uncertain)
        fields = ("color","stacks","alive")
        mismatch = [field for field in fields if getattr(actual,field) != truth[field]]
        accepted_mismatch = bool(mismatch) and not uncertain
        isolation_uncertain = False
        if truth.get("alive") and "isolated" in truth:
            confirmed = diagnostics.get(key,{}).get("isolation_confirmed") is True
            if not confirmed:
                isolation_unknown += 1
                isolation_uncertain = True
            elif actual.isolated != truth["isolated"]:
                mismatch.append("isolated")
                accepted_mismatch = True
        wrong_accepted += int(accepted_mismatch)
        if mismatch or uncertain or isolation_uncertain:
            differences.append({"key":key,"mismatch_fields":mismatch,"rejected":bool(uncertain),
                                "expected":{field:truth[field] for field in fields},
                                "actual":{field:getattr(actual,field) for field in fields},
                                **({"expected_isolated":truth["isolated"],
                                    "actual_isolated":actual.isolated,
                                    "isolation_confirmed":diagnostics.get(key,{}).get("isolation_confirmed")}
                                   if "isolated" in truth else {})})
    malformed = len(missing)+len(extra)+duplicate_count
    return {"wrong_accepted_cells":wrong_accepted,"unknown_buff_cells":unknown,
            "unconfirmed_isolation_cells":isolation_unknown,"missing_keys":missing,
            "extra_keys":extra,"duplicate_keys_count":duplicate_count,
            "complete_buff_frame":not(wrong_accepted or unknown or malformed),
            "complete_all_labelled_fields":not(wrong_accepted or unknown or isolation_unknown or malformed),
            "differences":differences}


def run_case(case, factory, kwargs, full=False, repeats=1, selected_variants=None):
    filename,image,layout,expected = load_case(case)
    source_cells = layout.cells(QRectF(0,0,image.shape[1],image.shape[0]))
    source_icon_sizes = [cell.rect.height()*.49 for cell in source_cells]
    results = []
    for variant in resolution_variants(image,layout,full):
        if selected_variants and variant.name not in selected_variants:
            continue
        start = time.perf_counter()
        engine = factory(variant.layout,**kwargs)
        setup_ms = (time.perf_counter()-start)*1000
        times,signatures = [],[]
        initial = None
        initial_diagnostics = {}
        for index in range(repeats):
            start = time.perf_counter()
            players = engine.analyze(qimage(variant.image))
            times.append((time.perf_counter()-start)*1000)
            signatures.append(tuple((p.player_key,p.color,p.stacks,p.alive,p.isolated) for p in players))
            if index == 0:
                initial = score_players(players,expected,getattr(engine,"diagnostics",{}))
                # Keep the exact evidence behind failed cells for tuning;
                # successful cells do not need to inflate the private report.
                initial_diagnostics = {difference["key"]:getattr(engine,"diagnostics",{}).get(difference["key"],{})
                                       for difference in initial["differences"]}
        effective = [size*min(variant.information_scale) for size in source_icon_sizes]
        final_x = [size*variant.final_scale[0] for size in source_icon_sizes]
        final_y = [size*variant.final_scale[1] for size in source_icon_sizes]
        result = {"case":case["id"],"split":"calibration" if case["id"] in CALIBRATION_IDS else "holdout",
                  "variant":variant.name,"synthetic":variant.name!="raw","operations":variant.operations,
                  "source_shape":list(image.shape[:2]),"input_shape":list(variant.image.shape[:2]),
                  "source_icon_px_min":round(min(source_icon_sizes),2),
                  "effective_icon_px_min":round(min(effective),2),
                  "display_icon_width_px_min":round(min(final_x),2),"display_icon_height_px_min":round(min(final_y),2),
                  "information_below_12px":min(effective)<12,
                  "support_eligible":min(effective)>=12,
                  "icon_size_by_cell":{
                      cell.key:{"source_px":round(size,2),"display_width_px":round(fx,2),
                                "display_height_px":round(fy,2),"effective_px":round(info,2)}
                      for cell,size,fx,fy,info in zip(source_cells,source_icon_sizes,final_x,final_y,effective)},
                  "setup_ms":round(setup_ms,2),"first_recognition_ms":round(times[0],2),
                  "warm_recognition_ms":round(float(np.median(times[1:])),2) if len(times)>1 else None,
                  "repeated_passes":repeats,"same_input_state_changed":len(set(signatures))>1,
                  "failure_diagnostics":initial_diagnostics,
                  **initial}
        results.append(result)
    if selected_variants:
        missing_variants = set(selected_variants)-{item["variant"] for item in results}
        if missing_variants:
            raise ValueError(f"Unknown/unavailable variants (some require --full): {sorted(missing_variants)}")
    labelled_classes = {f"{truth['color']}_{truth['stacks']}" for truth in expected
                        if truth.get("alive") and truth["color"] in ("red","black")}
    if any(truth.get("alive") and truth.get("isolated") is True for truth in expected):
        labelled_classes.add("isolation")
    return {"id":case["id"],"source":str(filename),"real_labelled_target_classes":sorted(labelled_classes),
            "sha256":hashlib.sha256(filename.read_bytes()).hexdigest(),"results":results}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--quick",action="store_true",help="Representative calibration/holdout smoke; default")
    mode.add_argument("--full",action="store_true",help="All seven scenes and complete scale/video matrix")
    parser.add_argument("--cases",help="Comma-separated case IDs; overrides representative selection")
    parser.add_argument("--variants",help="Comma-separated variant names; use --full --variants raw for all seven raw frames")
    parser.add_argument("--factory",default="lisha_overlay.vision:VisionEngine")
    parser.add_argument("--factory-kwargs",default="{}",help="JSON arguments for the selected engine constructor")
    parser.add_argument("--label",default="engine")
    parser.add_argument("--repeats",type=int,default=1)
    parser.add_argument("--output",default="artifacts/recognition-scale-audit.json")
    args = parser.parse_args(argv)
    if args.repeats < 1:
        parser.error("--repeats must be positive")
    module,name = args.factory.split(":",1)
    factory = getattr(importlib.import_module(module),name)
    initial_fingerprints = implementation_fingerprints(factory)
    kwargs = json.loads(args.factory_kwargs)
    cases = read_json("artifacts/recognition-corpus-private.json")["cases"]
    selected = set(args.cases.split(",")) if args.cases else (None if args.full else SMOKE_IDS)
    if selected:
        missing = selected-{case["id"] for case in cases}
        if missing:
            parser.error(f"Unknown corpus case IDs: {sorted(missing)}")
        cases = [case for case in cases if case["id"] in selected]
    cv2.setNumThreads(1)
    scenes=[]
    for case in cases:
        scene = run_case(case,factory,kwargs,args.full,args.repeats,
                         set(args.variants.split(",")) if args.variants else None)
        scenes.append(scene)
        print(json.dumps({"case":case["id"],"variants":len(scene["results"]),
                          "complete":sum(r["complete_buff_frame"] for r in scene["results"]),
                          "wrong":sum(r["wrong_accepted_cells"] for r in scene["results"]),
                          "unknown":sum(r["unknown_buff_cells"] for r in scene["results"])}),flush=True)
    rows=[row for scene in scenes for row in scene["results"]]
    raw=[row for row in rows if not row["synthetic"]]
    eligible=[row for row in rows if row["support_eligible"]]
    stress=[row for row in rows if not row["support_eligible"]]
    labelled_classes = set().union(*(set(scene["real_labelled_target_classes"]) for scene in scenes))
    target_classes = {f"{colour}_{count}" for colour in ("red","black") for count in range(1,5)} | {"isolation"}
    summary={"label":args.label,"source_scenes":len(scenes),
             "unique_source_images":len({scene["sha256"] for scene in scenes}),
             "real_labelled_target_classes":sorted(labelled_classes),
             "target_classes_missing_real_labels":sorted(target_classes-labelled_classes),
             "raw_frames":len(raw),"raw_complete_frames":sum(r["complete_buff_frame"] for r in raw),
             "synthetic_variants":len(rows)-len(raw),"total_frames":len(rows),
             "complete_buff_frames":sum(r["complete_buff_frame"] for r in rows),
             "complete_all_labelled_frames":sum(r["complete_all_labelled_fields"] for r in rows),
             "supported_frames":len(eligible),
             "supported_complete_frames":sum(r["complete_buff_frame"] for r in eligible),
             "supported_complete_all_labelled_frames":sum(r["complete_all_labelled_fields"] for r in eligible),
             "below_information_floor_frames":len(stress),
             "below_floor_rejected_frames":sum(r["unknown_buff_cells"]>0 for r in stress),
             "supported_wrong_accepted_cells":sum(r["wrong_accepted_cells"] for r in eligible),
             "below_floor_wrong_accepted_cells":sum(r["wrong_accepted_cells"] for r in stress),
             "wrong_accepted_cells":sum(r["wrong_accepted_cells"] for r in rows),
             "unknown_buff_cells":sum(r["unknown_buff_cells"] for r in rows),
             "unconfirmed_isolation_cells":sum(r["unconfirmed_isolation_cells"] for r in rows),
             "median_first_recognition_ms":round(float(np.median([r["first_recognition_ms"] for r in rows])),2),
             "p95_first_recognition_ms":round(float(np.percentile([r["first_recognition_ms"] for r in rows],95)),2)}
    source=inspect.getsourcefile(factory)
    final_fingerprints = implementation_fingerprints(factory)
    report={"summary":summary,"factory":args.factory,"factory_kwargs":kwargs,
            "engine_source_sha256":hashlib.sha256(Path(source).read_bytes()).hexdigest() if source else None,
            "implementation_fingerprints":initial_fingerprints,
            "implementation_changed_during_audit":initial_fingerprints != final_fingerprints,
            "methodology":{"calibration_ids":sorted(CALIBRATION_IDS),
                           "labels":"Frozen manually verified logical player states; no prediction-generated labels.",
                           "raw_vs_synthetic":"Resamples, JPEG variants and repeated passes are not independent combat scenes.",
                           "timing":"Recognition includes QImage conversion and all 25 cells, excludes capture, solver, UI and import time; constructor is separate.",
                           "information_limit":"Effective pixel size records the smallest video stage. Upsampling does not restore erased strokes; <12 px is flagged, not claimed supported.",
                           "support_eligible":"A diagnostic size bucket, not a guarantee or empirically established support boundary; recognition results establish what actually passed.",
                           "failures":"Wrong accepted labels and rejected Buff states are reported separately. Unconfirmed isolation is also separate."},
            "scenes":scenes}
    output=ROOT/args.output
    output.parent.mkdir(parents=True,exist_ok=True)
    output.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
    with output.with_suffix(".csv").open("w",encoding="utf-8-sig",newline="") as handle:
        fields=["case","split","variant","effective_icon_px_min","wrong_accepted_cells","unknown_buff_cells",
                "unconfirmed_isolation_cells","complete_buff_frame","first_recognition_ms","warm_recognition_ms"]
        writer=csv.DictWriter(handle,fieldnames=fields,extrasaction="ignore")
        writer.writeheader();writer.writerows(rows)
    print(json.dumps(summary),flush=True)
    # Correct rejection below the declared information floor is not a failed
    # supported case, but is never counted as successful scale support either.
    return int(any(not row["complete_all_labelled_fields"] for row in eligible)
               or any(row["wrong_accepted_cells"] for row in stress)
               or report["implementation_changed_during_audit"])


if __name__ == "__main__":
    raise SystemExit(main())
