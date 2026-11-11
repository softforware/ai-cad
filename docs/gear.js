// Involute spur gear math, ported from cad.py so the demo runs entirely in the browser.
// Kept free of three.js so it can be checked against the Python version in Node.

const inv = (r, rb) => {
  const t = Math.sqrt(Math.max((r / rb) ** 2 - 1, 0));
  return t - Math.atan(t);
};

/** Closed 2D outline [[x, y], ...] of a standard involute spur gear (no profile shift). */
export function spurGearProfile(module, teeth, pressureAngle = 20, samples = 12) {
  const alpha = (pressureAngle * Math.PI) / 180;
  const rp = (module * teeth) / 2;
  const rb = rp * Math.cos(alpha);
  const ra = rp + module;
  const rf = rp - 1.25 * module;
  const invAlpha = Math.tan(alpha) - alpha;
  const half = (r) => Math.PI / (2 * teeth) + invAlpha - inv(r, rb);

  if (half(ra) <= 0) {
    throw new Error(`${teeth} teeth at ${pressureAngle}° gives pointed teeth; use more teeth.`);
  }

  const rStart = Math.max(rb, rf);
  const flankR = Array.from({ length: samples + 1 }, (_, i) => rStart + ((ra - rStart) * i) / samples);
  const pitch = (2 * Math.PI) / teeth;
  const polar = (r, a) => [r * Math.cos(a), r * Math.sin(a)];
  const pts = [];

  for (let i = 0; i < teeth; i++) {
    const c = i * pitch;
    const rootHalf = half(rStart);
    if (rf < rb) pts.push(polar(rf, c - rootHalf));
    for (const r of flankR) pts.push(polar(r, c - half(r)));
    const tip = half(ra);
    for (let k = 1; k < 4; k++) pts.push(polar(ra, c - tip + (2 * tip * k) / 4));
    for (const r of [...flankR].reverse()) pts.push(polar(r, c + half(r)));
    if (rf < rb) pts.push(polar(rf, c + rootHalf));
    const a0 = c + rootHalf, a1 = c + pitch - rootHalf;
    for (let k = 1; k < 4; k++) pts.push(polar(rf, a0 + ((a1 - a0) * k) / 4));
  }
  return pts;
}

/** Same input checks as cad.make_spur_gear; throws with a readable message. */
export function validateGear(p) {
  if (!(p.module > 0) || !(p.face_width > 0)) throw new Error("module and face_width must be positive");
  if (!Number.isInteger(p.teeth) || p.teeth < 8) throw new Error("teeth must be a whole number, at least 8");
  if (p.teeth > 400) throw new Error("teeth must be 400 or fewer in the demo");
  const rf = (p.module * p.teeth) / 2 - 1.25 * p.module;
  const hasHub = p.hub_diameter > 0 && p.hub_length > 0;
  if (hasHub && p.hub_diameter / 2 >= rf) {
    throw new Error(`hub_diameter must be smaller than the root diameter (${(2 * rf).toFixed(2)} mm)`);
  }
  if (p.bore_diameter > 0) {
    const limit = (hasHub ? p.hub_diameter / 2 : rf) - 1;
    if (p.bore_diameter / 2 >= limit) {
      throw new Error(`bore_diameter too large; must be under ${(2 * limit).toFixed(2)} mm`);
    }
    if (p.keyway_width > 0 && p.keyway_width >= p.bore_diameter) {
      throw new Error("keyway_width must be smaller than bore_diameter");
    }
  }
}

/** Key dimensions, matching the `derived` dict in cad.py. */
export function derivedDims({ module, teeth, pressure_angle }) {
  const r4 = (v) => Math.round(v * 1e4) / 1e4;
  return {
    pitch_diameter_mm: r4(module * teeth),
    outside_diameter_mm: r4(module * (teeth + 2)),
    root_diameter_mm: r4(module * (teeth - 2.5)),
    base_diameter_mm: r4(module * teeth * Math.cos((pressure_angle * Math.PI) / 180)),
    circular_pitch_mm: r4(Math.PI * module),
  };
}

/** Outline of the bore, with a keyway notch on +X when width and depth are set. */
export function boreOutline(boreDiameter, keywayWidth = 0, keywayDepth = 0, segments = 64) {
  const r = boreDiameter / 2;
  if (!(keywayWidth > 0 && keywayDepth > 0)) {
    return Array.from({ length: segments }, (_, i) => {
      const a = (2 * Math.PI * i) / segments;
      return [r * Math.cos(a), r * Math.sin(a)];
    });
  }
  const hw = keywayWidth / 2;
  const a0 = Math.asin(hw / r);
  const x0 = Math.sqrt(r * r - hw * hw);
  const pts = [];
  for (let i = 0; i <= segments; i++) {
    const a = a0 + ((2 * Math.PI - 2 * a0) * i) / segments;
    pts.push([r * Math.cos(a), r * Math.sin(a)]);
  }
  // the arc ends at (x0, -hw); notch out to the keyway depth, path closes back to (x0, hw)
  pts.push([r + keywayDepth, -hw], [r + keywayDepth, hw]);
  return pts;
}
