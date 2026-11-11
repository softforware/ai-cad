"""Deterministic CAD layer: tested part generators + a sandboxed runner for custom build123d code.

The LLM never does geometry math itself. It picks parameters for these functions,
or (as a fallback) writes build123d code that runs in a separate process.
"""

import json
import math
import subprocess
import sys
import uuid
from pathlib import Path

from build123d import Align, Box, Cylinder, Face, Pos, Wire, export_step, export_stl, extrude

OUTPUT_DIR = Path(__file__).parent / "output"
OUTPUT_DIR.mkdir(exist_ok=True)
_C, _MIN = Align.CENTER, Align.MIN


# ---------------------------------------------------------------- gear math

def _inv(r: float, rb: float) -> float:
    """Involute polar angle at radius r for base radius rb."""
    t = math.sqrt(max((r / rb) ** 2 - 1, 0.0))
    return t - math.atan(t)


def spur_gear_profile(module: float, teeth: int, pressure_angle: float = 20.0,
                      samples: int = 12) -> list[tuple[float, float]]:
    """Closed 2D outline of a standard involute spur gear (no profile shift)."""
    alpha = math.radians(pressure_angle)
    rp = module * teeth / 2          # pitch radius
    rb = rp * math.cos(alpha)        # base radius
    ra = rp + module                 # tip (addendum) radius
    rf = rp - 1.25 * module          # root (dedendum) radius
    inv_alpha = math.tan(alpha) - alpha

    def half_angle(r):  # half angular tooth thickness at radius r
        return math.pi / (2 * teeth) + inv_alpha - _inv(r, rb)

    if half_angle(ra) <= 0:
        raise ValueError(f"{teeth} teeth at {pressure_angle}° gives pointed teeth; use more teeth.")

    r_start = max(rb, rf)
    flank_r = [r_start + (ra - r_start) * i / samples for i in range(samples + 1)]
    pitch = 2 * math.pi / teeth
    pts = []

    def polar(r, a):
        return (r * math.cos(a), r * math.sin(a))

    for i in range(teeth):
        c = i * pitch
        root_half = half_angle(r_start)
        # root point below base circle (radial flank), then rising flank
        if rf < rb:
            pts.append(polar(rf, c - root_half))
        pts += [polar(r, c - half_angle(r)) for r in flank_r]
        # tip land
        tip = half_angle(ra)
        pts += [polar(ra, c - tip + 2 * tip * k / 4) for k in range(1, 4)]
        # falling flank
        pts += [polar(r, c + half_angle(r)) for r in reversed(flank_r)]
        if rf < rb:
            pts.append(polar(rf, c + root_half))
        # root land to next tooth
        a0, a1 = c + root_half, c + pitch - root_half
        pts += [polar(rf, a0 + (a1 - a0) * k / 4) for k in range(1, 4)]
    return pts


# ---------------------------------------------------------------- generators

def make_spur_gear(module: float, teeth: int, pressure_angle: float = 20.0,
                   face_width: float = 10.0, bore_diameter: float = 0.0,
                   keyway_width: float = 0.0, keyway_depth: float = 0.0,
                   hub_diameter: float = 0.0, hub_length: float = 0.0):
    if module <= 0 or face_width <= 0:
        raise ValueError("module and face_width must be positive")
    if teeth < 8:
        raise ValueError("teeth must be at least 8")

    rf = module * teeth / 2 - 1.25 * module
    outline = Wire.make_polygon(spur_gear_profile(module, teeth, pressure_angle), close=True)
    part = extrude(Face(outline), amount=face_width)

    if hub_diameter and hub_length:
        if hub_diameter / 2 >= rf:
            raise ValueError("hub_diameter must be smaller than the root diameter "
                             f"({2 * rf:.2f} mm)")
        part += Pos(0, 0, face_width) * Cylinder(hub_diameter / 2, hub_length,
                                                 align=(_C, _C, _MIN))
    total_len = face_width + (hub_length if hub_diameter else 0)

    if bore_diameter:
        limit = (hub_diameter / 2 if hub_diameter else rf) - 1
        if bore_diameter / 2 >= limit:
            raise ValueError(f"bore_diameter too large; must be under {2 * limit:.2f} mm")
        part -= Cylinder(bore_diameter / 2, total_len * 3)
        if keyway_width and keyway_depth:
            part -= Pos(bore_diameter / 2, 0, total_len / 2) * Box(
                keyway_depth * 2, keyway_width, total_len * 3)

    derived = {
        "pitch_diameter_mm": round(module * teeth, 4),
        "outside_diameter_mm": round(module * (teeth + 2), 4),
        "root_diameter_mm": round(module * (teeth - 2.5), 4),
        "base_diameter_mm": round(module * teeth * math.cos(math.radians(pressure_angle)), 4),
        "circular_pitch_mm": round(math.pi * module, 4),
    }
    return part, derived



# ---------------------------------------------------------------- export + checks

def _metrics(shape) -> dict:
    bb = shape.bounding_box()
    return {
        "valid_solid": bool(shape.is_valid),
        "volume_mm3": round(shape.volume, 2),
        "bounding_box_mm": [round(v, 3) for v in (bb.size.X, bb.size.Y, bb.size.Z)],
    }


def save_part(shape, name: str) -> dict:
    pid = uuid.uuid4().hex[:10]
    stl, step = OUTPUT_DIR / f"{pid}.stl", OUTPUT_DIR / f"{pid}.step"
    export_stl(shape, str(stl), tolerance=0.01, angular_tolerance=0.1)
    export_step(shape, str(step))
    return {"id": pid, "name": name, "stl": f"/output/{pid}.stl",
            "step": f"/output/{pid}.step", **_metrics(shape)}


# ---------------------------------------------------------------- custom code sandbox

_RUNNER = r'''
import json, sys
from build123d import *
from build123d import export_step, export_stl
src, stl, step = sys.argv[1], sys.argv[2], sys.argv[3]
ns = {}
exec("from build123d import *\n" + open(src).read(), ns)
result = ns.get("result")
if result is None:
    raise SystemExit("code must assign the final shape to a variable named `result`")
if hasattr(result, "part"):  # BuildPart context
    result = result.part
export_stl(result, stl, tolerance=0.01, angular_tolerance=0.1)
export_step(result, step)
bb = result.bounding_box()
print(json.dumps({"valid_solid": bool(result.is_valid), "volume_mm3": round(result.volume, 2),
                  "bounding_box_mm": [round(v, 3) for v in (bb.size.X, bb.size.Y, bb.size.Z)]}))
'''


def run_custom_code(code: str, name: str, timeout: int = 60) -> dict:
    """Run LLM-written build123d code in a separate process with a timeout."""
    pid = uuid.uuid4().hex[:10]
    src = OUTPUT_DIR / f"{pid}.py"
    src.write_text(code)
    stl, step = OUTPUT_DIR / f"{pid}.stl", OUTPUT_DIR / f"{pid}.step"
    try:
        proc = subprocess.run(
            [sys.executable, "-c", _RUNNER, str(src), str(stl), str(step)],
            capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"code timed out after {timeout}s")
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip()[-2000:] or proc.stdout.strip()[-2000:])
    metrics = json.loads(proc.stdout.strip().splitlines()[-1])
    return {"id": pid, "name": name, "stl": f"/output/{pid}.stl",
            "step": f"/output/{pid}.step", **metrics}
