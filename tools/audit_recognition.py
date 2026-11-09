"""Audit manually labelled scenes and controlled changes; no desktop input."""
from pathlib import Path
import csv
import json
import sys
import time
import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from PySide6.QtGui import QImage
from lisha_overlay.vision import VisionEngine, _qimage_bgr, _pixel_rect
from lisha_overlay.team_layout import TeamLayout


def read(path):
    return json.loads((ROOT / path).read_text(encoding="utf-8"))


def load_case(case):
    layout = TeamLayout.from_dict(read(case["settings"])["team_layout"]) if "settings" in case else (
        TeamLayout.from_dict(read(case["layout"])) if "layout" in case else TeamLayout())
    image = _qimage_bgr(QImage(str(ROOT / case["image"])))
    if "expected_file" in case:
        expected = read(case["expected_file"])
    else:
        expected = []
        for index, token in enumerate(" ".join(case["rows"]).split()):
            expected.append({"key": f"g{index//5+1}p{index%5+1}",
                             "color": "red" if token.startswith("r") else "black" if token.startswith("b") else "none",
                             "stacks": int(token[1:]) if token[0] in "rb" else 0,
                             "alive": token != "dead"})
    return layout, image, expected


def health_flash(image, layout, colour):
    """Recolour only the largest contiguous HP-fill component, not Buffs."""
    image = image.copy()
    for cell in layout.cells(QImage(image.shape[1], image.shape[0], QImage.Format.Format_RGB32).size()):
        left, top, right, bottom = _pixel_rect(cell.rect, image)
        roi = image[top:bottom, left:right]
        hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)
        green = ((hsv[:, :, 0] >= 35) & (hsv[:, :, 0] <= 90)
                 & (hsv[:, :, 1] > 65) & (hsv[:, :, 2] > 35)).astype(np.uint8)
        green[:round(roi.shape[0] * .38)] = 0
        count, labels, stats, _ = cv2.connectedComponentsWithStats(green)
        if count < 2:
            continue
        component = 1 + int(np.argmax(stats[1:, cv2.CC_STAT_AREA]))
        if stats[component, cv2.CC_STAT_AREA] >= 100:
            roi[labels == component] = colour
    return image


def variants(image, layout):
    yield "raw", image
    for name, colour in (("hp_red", (45,65,205)), ("hp_purple", (155,55,125)),
                         ("hp_grey", (155,155,155)), ("hp_orange", (45,165,220))):
        yield name, health_flash(image, layout, colour)
    for dx, dy in ((1,0),(0,1)):
        yield f"shift_{dx}_{dy}", cv2.warpAffine(image, np.float32([[1,0,dx],[0,1,dy]]), (image.shape[1],image.shape[0]))
    for gain in (.9, 1.1):
        yield f"gain_{gain}", np.clip(image.astype(np.float32)*gain,0,255).astype(np.uint8)
    for name,colour in (("resource_empty",(25,25,25)),("resource_grey",(95,95,95))):
        pixels = image.copy()
        for cell in layout.cells(frame(image).size()):
            left, top, right, bottom = _pixel_rect(cell.rect, pixels)
            roi = pixels[top:bottom,left:right]
            hsv = cv2.cvtColor(roi,cv2.COLOR_BGR2HSV)
            blue = ((hsv[:,:,0]>95)&(hsv[:,:,0]<=130)&(hsv[:,:,1]>65)&(hsv[:,:,2]>30))
            rows = (blue.mean(axis=1) >= .45)
            rows[:round(roi.shape[0]*.72)] = False
            # Change resource-colour pixels, leaving white digits and the
            # red/purple label strokes untouched.
            roi[blue & rows[:,None]] = colour
        yield name,pixels


def frame(image):
    return QImage(image.data, image.shape[1], image.shape[0], image.strides[0], QImage.Format.Format_BGR888).copy()


def main():
    cv2.setNumThreads(1)
    cases = read("artifacts/recognition-corpus-private.json")["cases"]
    results, details = [], []
    for case in cases:
        layout, image, expected = load_case(case)
        engine = VisionEngine(layout)
        for name, pixels in variants(image, layout):
            start = time.perf_counter()
            players = engine.analyze(frame(pixels))
            elapsed = (time.perf_counter() - start) * 1000
            errors = unknown = 0
            for actual, truth in zip(players, expected):
                fields = ("color","stacks","alive") + (("isolated",) if "isolated" in truth else ())
                uncertain = actual.alive and (actual.color == "unknown" or actual.stacks is None)
                wrong = any(getattr(actual, field) != truth[field] for field in fields)
                unknown += int(uncertain)
                errors += int(wrong and not uncertain)
                if wrong:
                    details.append({"case": case["id"], "variant": name, "key": actual.player_key,
                                    "expected": [truth[k] for k in fields],
                                    "actual": [getattr(actual,k) for k in fields], "unknown": bool(uncertain)})
            results.append({"case":case["id"],"variant":name,"wrong":errors,"unknown":unknown,"ms":round(elapsed,2)})
    report = {"real_scenes":len(cases),"raw_frames":len(cases),"controlled_variants":len(results)-len(cases),
              "wrong":sum(r["wrong"] for r in results), "unknown":sum(r["unknown"] for r in results),
              "complete_frames":sum(r["wrong"]==0 and r["unknown"]==0 for r in results),
              "total_frames":len(results),"results":results,"differences":details,
              "note":"Controlled variants and repeated static captures are not independent combat accuracy samples."}
    (ROOT/"artifacts/recognition-audit-report.json").write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
    with (ROOT/"artifacts/recognition-audit-differences.csv").open("w",encoding="utf-8-sig",newline="") as output:
        writer=csv.DictWriter(output,fieldnames=["case","variant","key","expected","actual","unknown"])
        writer.writeheader();writer.writerows(details)
    print(json.dumps({k:v for k,v in report.items() if k not in ("results","differences")}))
    for item in details[:20]:
        print(json.dumps(item,ensure_ascii=True))
    return int(bool(details))


if __name__ == "__main__":
    raise SystemExit(main())
