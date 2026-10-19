"""Compare the ECHO offsets XtalFlow produces against XtalViewer 2.0's.

The implementation sequence asked for this before the worksheet coordinates
were trusted, and it had never been run.  Only the ECHO worksheet carries
coordinates; the SHIFTER worksheet carries well addresses, so nothing there
can disagree.

    python codex/golden_comparison.py                       # the test fixtures
    python codex/golden_comparison.py /path/to/WellImages 2070

Both programs do the same arithmetic with the same axes:

    legacy     um = (px - centre) * wellRadius / guideRadius     sv3.py:1733
    XtalFlow   mm = (px - centre) * diameter_mm / (2 * radius)   calibration.py:128

and both subtract 700 um from X on the d lens (sv3.py:1106, plate_format.py
LensDefinition(3, "d", ..., echo_x_correction_um=-700)).  So the axes, the
origin's meaning and the lens correction agree by construction, and only the
scale can differ:

    legacy     1400 / 485 um/px, fixed, from a guide circle the user aligns
    XtalFlow   2770 / (2 * r) um/px, from the circle detected in that image

What this cannot check is the origin itself.  The legacy's zero is wherever
the user dragged the guide circle; XtalFlow's is the centre it detected and
the user confirmed.  Comparing those needs a plate someone aligned by hand in
the legacy, with its info CSV kept.
"""

from __future__ import annotations

import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from xtalflow.domain.plate_format import SWISSCI_MIDI_3_LENS
from xtalflow.infrastructure.rmserver import RockMakerImageRepository
from xtalflow.infrastructure.well_detector import OpenCVWellDetector

# src_xtalviewer_2_0_legacy/sv3.py:127-128
LEGACY_WELL_RADIUS_UM = 1400
LEGACY_GUIDE_RADIUS_PX = 485
LEGACY_UM_PER_PX = LEGACY_WELL_RADIUS_UM / LEGACY_GUIDE_RADIUS_PX

# How far from the centre a crystal is typically clicked, in units of the
# well radius: the middle of the drop, half way out, and at the rim.
SAMPLE_FRACTIONS = (0.25, 0.5, 1.0)


def main(argv: list[str]) -> int:
    root = Path(argv[1]) if len(argv) > 1 else (
        Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "rmserver"
    )
    plate_code = argv[2] if len(argv) > 2 else "2070"
    diameter_mm = SWISSCI_MIDI_3_LENS.lenses[0].physical_diameter_mm

    repository = RockMakerImageRepository(root)
    detector = OpenCVWellDetector()
    plate = repository.load_plate(plate_code)
    print(f"images:   {root}")
    print(f"plate:    {plate.plate_code} batch {plate.batch_id} · {len(plate.images)} images")
    print(f"legacy:   {LEGACY_WELL_RADIUS_UM} um / {LEGACY_GUIDE_RADIUS_PX} px "
          f"= {LEGACY_UM_PER_PX:.4f} um/px, the same for every image")
    print(f"XtalFlow: {diameter_mm} mm across the circle detected in each image\n")

    scales, radii, failures = [], [], 0
    for image in plate.images:
        try:
            boundary = detector.detect(image)
        except Exception:
            failures += 1
            continue
        radius = (boundary.radius_x_px + boundary.radius_y_px) / 2
        radii.append(radius)
        scales.append(diameter_mm * 1000 / (2 * radius))

    if not scales:
        print("no wells detected")
        return 1
    scales.sort()
    radii.sort()
    median_scale = statistics.median(scales)
    print(f"detected in {len(scales)} of {len(plate.images)} images"
          + (f" ({failures} failed)" if failures else ""))
    print(f"  radius px   median {statistics.median(radii):7.1f}   "
          f"p10 {radii[len(radii) // 10]:7.1f}   p90 {radii[-len(radii) // 10]:7.1f}")
    print(f"  um/px       median {median_scale:7.4f}   "
          f"p10 {scales[len(scales) // 10]:7.4f}   p90 {scales[-len(scales) // 10]:7.4f}")
    print(f"\nXtalFlow is {(median_scale / LEGACY_UM_PER_PX - 1) * 100:+.2f}% "
          "of the legacy scale at the median.\n")

    # What that is worth where crystals actually sit.
    print("difference in the ECHO offset for one target, by distance from the centre:")
    print(f"  {'from centre':>14}   {'legacy um':>10}   {'XtalFlow um':>12}   {'diff um':>8}")
    for fraction in SAMPLE_FRACTIONS:
        offsets = []
        for radius, scale in zip(radii, scales):
            pixels = radius * fraction
            offsets.append((pixels * LEGACY_UM_PER_PX, pixels * scale))
        legacy_um = statistics.median(value for value, _ in offsets)
        ours_um = statistics.median(value for _, value in offsets)
        label = "rim" if fraction == 1.0 else f"{fraction:.0%} out"
        print(f"  {label:>14}   {legacy_um:10.1f}   {ours_um:12.1f}   "
              f"{ours_um - legacy_um:+8.1f}")

    print("\nsame in both, so nothing to compare:")
    print("  axes          right is +X, down the image is +Y")
    print("  d lens        X - 700 um          (sv3.py:1106 · plate_format.py)")
    print("  SHIFTER       carries no coordinates, only well addresses")
    print("\nnot checked here: the origin. The legacy's is where the user dragged")
    print("the guide circle; ours is the detected centre the user confirmed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
