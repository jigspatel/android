#!/usr/bin/env node
// JoyNuts "make it crunch." — keyed animation tracks, baked per frame.
//
// Everything that moves is described here as keyed tracks (time in seconds,
// eased segments) plus a few procedural layers (walk cycle, sparks). The
// result is sampled once per frame and written to baked.json, which build.py
// reads to build and key the Blender scene. The studio layout lives here too
// so the animation and the geometry share one source of truth.
//
// Units: 1 Blender unit = 1 cm. The hero is 2.6 cm tall.
//
//   node tracks.js [out.json]

'use strict';
const fs = require('fs');
const path = require('path');

const FPS = 60;
const DURATION = 17;
const FRAMES = FPS * DURATION; // 1020; frame f (1-based) is t = (f - 1) / FPS
const TAU = Math.PI * 2;

// ---------------------------------------------------------------- helpers

const clamp = (x, a = 0, b = 1) => Math.min(b, Math.max(a, x));
const lerp = (a, b, u) => a + (b - a) * u;
const smooth = (a, b, x) => { const u = clamp((x - a) / (b - a)); return u * u * (3 - 2 * u); };
const pulse = (t, t0, rise, hold, fall) =>
  smooth(t0, t0 + rise, t) * (1 - smooth(t0 + rise + hold, t0 + rise + hold + fall, t));

const EASE = {
  linear: u => u,
  inOut: u => u * u * u * (u * (u * 6 - 15) + 10),
  in: u => u * u * u,
  out: u => 1 - Math.pow(1 - u, 3),
  back: u => { const c = 1.9; const v = u - 1; return 1 + (c + 1) * v * v * v + c * v * v; },
  step: u => (u < 1 ? 0 : 1),
  // trapezoid velocity: accelerate 20 %, cruise, decelerate 30 %
  cruise: u => {
    const a = 0.2, d = 0.3, vmax = 1 / (1 - a / 2 - d / 2);
    if (u < a) return 0.5 * vmax * u * u / a;
    if (u < 1 - d) return vmax * (a / 2 + (u - a));
    const r = 1 - u;
    return 1 - 0.5 * vmax * r * r / d;
  },
};

// track([[t, value, ease], ...]) -> f(t). value is a number or an array;
// ease applies to the segment that ends at that key.
function track(keys, defEase = 'inOut') {
  return t => {
    if (t <= keys[0][0]) return keys[0][1];
    for (let i = 1; i < keys.length; i++) {
      const [t1, v1, e] = keys[i];
      if (t <= t1) {
        const [t0, v0] = keys[i - 1];
        const u = EASE[e || defEase](t1 > t0 ? (t - t0) / (t1 - t0) : 1);
        return Array.isArray(v0) ? v0.map((a, j) => lerp(a, v1[j], u)) : lerp(v0, v1, u);
      }
    }
    return keys[keys.length - 1][1];
  };
}

// deterministic RNG so every bake is identical
function rng(seed) {
  let s = seed >>> 0;
  return () => { s = (s + 0x6D2B79F5) >>> 0; let t = s; t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61); return ((t ^ (t >>> 14)) >>> 0) / 4294967296; };
}

const v3 = {
  add: (a, b) => [a[0] + b[0], a[1] + b[1], a[2] + b[2]],
  sub: (a, b) => [a[0] - b[0], a[1] - b[1], a[2] - b[2]],
  mul: (a, s) => [a[0] * s, a[1] * s, a[2] * s],
  len: a => Math.hypot(a[0], a[1], a[2]),
  norm: a => { const l = Math.hypot(a[0], a[1], a[2]) || 1; return [a[0] / l, a[1] / l, a[2] / l]; },
  lerp: (a, b, u) => [lerp(a[0], b[0], u), lerp(a[1], b[1], u), lerp(a[2], b[2], u)],
};

// Yaw for a hero whose local forward is +Y: forward = (-sin yaw, cos yaw).
const yawTo = (from, to) => Math.atan2(-(to[0] - from[0]), to[1] - from[1]);

// ---------------------------------------------------------------- layout

const PLINTH = { x: 0, y: 0, r: 2.2, h: 0.9 };
const TURNTABLE = { r: 1.9, h: 0.15 };
const JAR_BASE_Z = PLINTH.h + TURNTABLE.h; // 1.05
const JAR = { r: 0.95, bodyH: 1.95, neckR: 0.86, lidR: 1.0, lidH: 0.5, H: 2.45,
  labelZ: [0.55, 1.45] };

// jar outer radius at local height z (used for the wire rings)
function jarRadius(z) {
  if (z < 0.1) return 0.85 + 0.1 * Math.sqrt(clamp(z / 0.1));
  if (z < 1.7) return JAR.r;
  if (z < 1.95) return lerp(JAR.r, JAR.neckR, smooth(1.7, 1.95, z));
  return JAR.lidR;
}
const WIRE_Z = [0.04, 0.3, 0.6, 0.9, 1.2, 1.5, 1.8, 2.08, 2.4];
const WIRE_RINGS = WIRE_Z.map(z => ({ z, r: jarRadius(z) + 0.025 }));

const HERO_SPOT = [0, -4.2];        // where it stands in front of the plinth
const HERO_ASIDE = [-2.55, -3.95];  // where it steps for the ta-da

const STANDS = {
  key: { x: -5.6, y: -4.6, top: 4.4, box: [2.6, 3.2, 1.2] },  // w, h, depth
  rim: { x: 6.7, y: 6.3, top: 4.0, box: [1.1, 3.6, 0.9] },
  boom: { x: -7.2, y: 5.8, top: 7.6, box: [3.0, 3.0, 0.9], hang: [0, 0, 7.0] },
};

const CAM_RIGS = [
  { name: 'Shot1_BehindTurntable', start: 0, end: 4, lens: 30 },
  { name: 'Shot2_FarOrbit', start: 4, end: 10, lens: 38 },
  { name: 'Shot3_Front', start: 10, end: 14, lens: 24 },
  { name: 'Shot4_PullBack', start: 14, end: 17, lens: 26 },
];

// ---------------------------------------------------------------- timing

const T = {
  walkStart: 0.15, walkEnd: 1.6,
  throat: [1.78, 1.98],                   // two little coughs
  words: [                                // spoken, ring emitted, word lands
    { text: 'make', say: [2.24, 2.44] },
    { text: 'it', say: [2.5, 2.62] },
    { text: 'crunch.', say: [2.7, 3.05] },
  ],
  ringFlight: 0.42,
  burst: 3.5,
  spiralStart: 4.12,
  ringFirst: 4.95, ringStep: 0.16,
  clay: [6.45, 7.05], kraft: [7.25, 7.75], glass: [7.95, 8.55], final: [8.85, 9.45],
  spin: [6.55, 9.9],
  glow: 9.45,
  lights: { key: 10.3, rim: 10.9, top: 11.5 },
  nod: 12.0, aside: [12.55, 13.3], tada: 13.35,
  glance: [14.25, 14.75], turnToUs: [15.0, 15.6], wave: 15.65,
  fade: [16.1, 17.0],
};

// ---------------------------------------------------------------- cameras

const cam1Pos = track([[0, [2.3, 5.7, 3.95]], [4, [1.85, 5.05, 3.7]]], 'linear');
const cam1Tgt = track([[0, [-0.5, -5.6, 1.5]], [1.6, [-0.1, -3.6, 1.75]], [2.3, [0, -2.2, 2.35]], [4, [0, -1.8, 2.5]]]);

const ORBIT_R = 6.6;
const cam2Ang = track([[4, 0.55], [10, Math.PI - 0.55]], 'inOut'); // +Y half only
const cam2Z = track([[4, 3.7], [7, 3.1], [10, 3.4]]);
const cam2Pos = t => { const a = cam2Ang(t); return [ORBIT_R * Math.cos(a), ORBIT_R * Math.sin(a), cam2Z(t)]; };
const cam2Tgt = track([[4, [0, 0, 2.9]], [6.4, [0, 0, 2.3]], [10, [0, -0.4, 2.2]]]);

const cam3Pos = track([[10, [0.8, -12.4, 3.2]], [14, [0.6, -11.5, 3.05]]], 'linear');
const cam3Tgt = track([[10, [-0.4, -1.2, 2.9]], [12.4, [-0.6, -1.4, 2.8]], [14, [-0.9, -1.8, 2.5]]]);

const cam4Pos = track([[14, [0.6, -11.3, 3.2]], [17, [1.6, -25, 15.5]]], 'out');
const cam4Tgt = track([[14, [-1.0, -2.2, 2.0]], [17, [0, -1.2, 1.4]]], 'out');

const CAMS = [[cam1Pos, cam1Tgt], [cam2Pos, cam2Tgt], [cam3Pos, cam3Tgt], [cam4Pos, cam4Tgt]];

// ---------------------------------------------------------------- hero

const heroXY = track([
  [0, [-0.95, -8.8]],
  [T.walkStart, [-0.95, -8.8]],
  [T.walkEnd, HERO_SPOT, 'cruise'],
  [T.aside[0], HERO_SPOT],
  [T.aside[1], HERO_ASIDE, 'cruise'],
]);

const YAW_FACE_CAM3 = 2 * Math.PI + yawTo(HERO_ASIDE, cam3Pos(13.3));
const YAW_FACE_CAM4 = 2 * Math.PI + yawTo(HERO_ASIDE, cam4Pos(15.6));
const heroYaw = track([
  [0, -0.2], [1.2, -0.12], [1.75, 0],
  [5.0, 0], [5.4, 0.08], [6.3, -0.06], [7.4, 0.07], [8.6, -0.05], [9.6, 0],
  [10.25, 0], [10.45, 0.32], [10.95, -0.28], [11.55, 0.0], [11.9, 0],
  [T.aside[0], 0], [12.9, 1.45], [T.aside[1], YAW_FACE_CAM3],
  [T.glance[0], YAW_FACE_CAM3], [T.glance[1], YAW_FACE_CAM3 + 0.9],
  [T.turnToUs[0], YAW_FACE_CAM3 + 0.9], [T.turnToUs[1], YAW_FACE_CAM4],
]);

// hop: little jump of joy when the label lights, and at the ta-da
const heroHop = t => 0.26 * Math.sin(Math.PI * clamp((t - 9.55) / 0.34)) +
                     0.12 * Math.sin(Math.PI * clamp((t - (T.tada + 0.02)) / 0.24));

// lean (+ forward), tilt (+ to its right), squash (z scale)
const leanKeys = track([
  [0, 0.05], [1.5, 0.05], [1.7, -0.04], [1.78, 0],
  [2.2, 0], [2.3, -0.06], [2.7, 0.0], [2.85, 0.12], [3.1, 0.02], [3.6, -0.08], [4.0, -0.1],
  [4.6, -0.12], [5.2, 0.1], [6.4, 0.12], [7.0, 0.02], [9.5, 0.0], [10, 0],
  [10.3, 0], [10.42, -0.14], [10.8, -0.02], [11.0, -0.14], [11.4, -0.02], [11.6, -0.18], [11.95, 0],
  [12.0, 0], [12.12, 0.22], [12.24, 0], [12.36, 0.2], [12.5, 0],
  [13.35, 0], [13.5, -0.1], [14, -0.06], [15.0, 0], [15.6, 0.05], [16.2, 0.0],
]);
const coughs = t => 0.16 * (pulse(t, T.throat[0], 0.05, 0.02, 0.1) + pulse(t, T.throat[1], 0.05, 0.02, 0.12));
const heroLean = t => leanKeys(t) + coughs(t);
const heroTilt = track([
  [0, 0], [4.5, 0], [5.3, 0.12], [6.6, -0.1], [7.8, 0.14], [9.0, -0.08], [9.6, 0],
  [13.35, 0], [13.6, 0.1], [14.1, 0.06], [15.6, 0.0], [16.0, 0.12],
]);
const heroSquash = t => 1
  - 0.06 * (pulse(t, T.throat[0], 0.04, 0.02, 0.1) + pulse(t, T.throat[1], 0.04, 0.02, 0.12))
  + 0.05 * pulse(t, 2.7, 0.08, 0.15, 0.2)              // stretch on "crunch"
  - 0.08 * pulse(t, 9.47, 0.07, 0.0, 0.1)              // crouch before the hop
  + 0.06 * pulse(t, 9.6, 0.08, 0.1, 0.2)
  + 0.07 * pulse(t, T.tada, 0.1, 0.4, 0.3);

// nub arms: raise = out/up to the side, fwd = forward swing (radians)
const armRaiseL = track([
  [0, 0], [2.65, 0], [2.85, 0.65], [3.2, 0.3], [3.7, 0.1], [9.5, 0], [9.65, 0.9], [9.95, 0.2],
  [13.3, 0], [13.55, 2.1, 'back'], [14.1, 1.9], [14.5, 0.3], [15.0, 0.1],
]);
const armRaiseR = track([
  [0, 0], [1.68, 0], [1.78, -0.35], [2.12, -0.35], [2.25, 0], [2.65, 0], [2.85, 0.65], [3.2, 0.3], [3.7, 0.1],
  [9.5, 0], [9.65, 0.9], [9.95, 0.2],
  [13.3, 0], [13.55, 2.1, 'back'], [14.1, 1.9], [14.5, 0.3], [15.6, 0.2], [15.8, 1.9], [16.6, 1.8], [16.9, 0.5],
]);
const armFwdL = track([[0, 0], [5.0, 0], [5.3, 0.5], [6.5, 0.55], [6.8, 0], [12.5, 0]]);
const armFwdR = track([
  [0, 0], [1.68, 0], [1.8, 1.5], [2.1, 1.5], [2.3, 0],
  [5.0, 0], [5.3, 0.5], [6.5, 0.55], [6.8, 0],
]);
// wave: wiggle the raised right nub
const armWaveR = t => 0.35 * Math.sin((t - T.wave) * TAU * 2.6) * pulse(t, T.wave + 0.1, 0.15, 0.6, 0.25);

const mouthOpen = t => {
  let m = 0.25 * (pulse(t, T.throat[0], 0.03, 0.04, 0.06) + pulse(t, T.throat[1], 0.03, 0.04, 0.06));
  const amp = [0.75, 0.5, 1.0];
  T.words.forEach((w, i) => { const d = w.say[1] - w.say[0]; m += amp[i] * pulse(t, w.say[0], d * 0.3, d * 0.3, d * 0.4); });
  m += 0.55 * pulse(t, T.tada, 0.08, 0.35, 0.3);   // "ta-da!"
  return clamp(m);
};
const mouthSmile = track([
  [0, 0.35], [1.7, 0.3], [2.1, 0.15], [3.1, 0.15], [3.6, 0.5], [5.2, 0.6], [9.4, 0.55], [9.7, 1.0], [10.6, 0.8],
  [11.8, 0.85], [13.3, 0.9], [13.5, 1.0], [14.2, 0.9], [15.2, 0.8], [15.6, 1.0],
]);
const eyesHappy = track([[0, 0], [9.5, 0], [9.75, 0.6], [10.5, 0.2], [13.35, 0.2], [13.55, 0.8], [14.3, 0.4], [15.4, 0.3], [15.7, 0.9]]);
const BLINKS = [0.9, 3.85, 6.1, 8.2, 11.2, 12.95, 14.85];
const eyesBlink = t => clamp(BLINKS.reduce((a, b) => a + pulse(t, b, 0.05, 0.02, 0.07), 0)
  + 0.7 * pulse(t, T.throat[0], 0.03, 0.2, 0.08));
// pupils look: x (to its right), z (up) in -1..1
const eyesLook = track([
  [0, [0, -0.1]], [1.6, [0, 0.2]], [2.0, [0, -0.4]], [2.3, [0, 0.5]], [3.4, [0, 1]], [4.1, [0, 0.4]], [4.6, [0, -0.2]],
  [6.4, [0, 0.4]], [9.6, [0, 0.5]], [10.3, [-0.6, 0.9]], [10.9, [0.7, 0.8]], [11.5, [0, 1]], [12.0, [0, 0]],
  [14.2, [0, 0]], [14.6, [-0.7, 0.2]], [15.1, [0, 0]],
]);

// ------------------------------------------- walk cycle (procedural layer)

// Legs follow the distance travelled (and turning), so they only step
// when the hero actually moves.
const STRIDE = 0.85;
function bakeWalk(times) {
  let phase = 0, prev = null, prevYaw = null, amp = 0;
  return times.map(t => {
    const p = heroXY(t), y = heroYaw(t);
    let move = 0;
    if (prev) move = Math.hypot(p[0] - prev[0], p[1] - prev[1]) + Math.abs(y - prevYaw) * 0.55;
    prev = p; prevYaw = y;
    phase += move / STRIDE * TAU;
    const speed = move * FPS;
    amp = lerp(amp, clamp(speed / 1.2), 0.25);
    return { phase, amp };
  });
}

// ---------------------------------------------------- words, rings, sparks

const WORD_SIZE = 0.62;
const WORD_CENTER = [0, 0.25, 3.75];
const CHAR_W = 0.56 * WORD_SIZE;
// words face the shot-1 camera and read left-to-right from it
const camAtWords = cam1Pos(2.8);
const toCam = v3.norm([camAtWords[0] - WORD_CENTER[0], camAtWords[1] - WORD_CENTER[1], 0]);
const WORD_YAW = Math.atan2(toCam[0], -toCam[1]);
const RIGHT = [Math.cos(WORD_YAW), Math.sin(WORD_YAW), 0];   // reading direction seen from camera
const words = (() => {
  const widths = T.words.map(w => w.text.length * CHAR_W);
  const gap = 0.32;
  const total = widths.reduce((a, b) => a + b, 0) + gap * (widths.length - 1);
  let cursor = -total / 2;
  return T.words.map((w, i) => {
    const c = cursor + widths[i] / 2; cursor += widths[i] + gap;
    return { text: w.text, width: widths[i], height: WORD_SIZE * 0.7,
      center: v3.add(WORD_CENTER, v3.mul(RIGHT, c)), lands: w.say[0] + T.ringFlight };
  });
})();

function wordScale(i, t) {
  const w = words[i];
  const appear = EASE.back(clamp((t - w.lands) / 0.16));
  const swell = 0.12 * smooth(T.burst - 0.06, T.burst, t);
  const gone = smooth(T.burst + 0.02, T.burst + 0.09, t);
  return t < w.lands ? 0 : (appear + swell) * (1 - gone);
}
function wordPos(i, t) {
  const w = words[i];
  return v3.add(w.center, [0, 0, 0.06 * Math.sin(TAU * 0.9 * t + i * 1.7)]);
}

// mouth position in world space at time t (local face point rotated by yaw)
function mouthWorld(t) {
  const p = heroXY(t), y = heroYaw(t);
  const local = [0, 0.66, 1.25];
  return [p[0] + local[0] * Math.cos(y) - local[1] * Math.sin(y),
          p[1] + local[0] * Math.sin(y) + local[1] * Math.cos(y), local[2] + heroHop(t)];
}

const soundRings = T.words.map((w, i) => {
  const t0 = w.say[0];
  const from = mouthWorld(t0), to = words[i].center;
  return { t0, from, to, dir: v3.norm(v3.sub(to, from)) };
});
function soundRingState(i, t) {
  const r = soundRings[i];
  const u = (t - r.t0) / (T.ringFlight + 0.25);
  if (u <= 0 || u >= 1) return { pos: r.from, scale: 0, alpha: 0 };
  const e = EASE.out(u);
  return { pos: v3.lerp(r.from, r.to, e), scale: lerp(0.18, 1.5, e), alpha: 0.9 * (1 - u) * smooth(0, 0.08, u) };
}

// sparks: born from the words at the burst, hover, then spiral down onto
// the turntable where each one lands on a wire ring of the jar.
const N_SPARKS = 90;
const sparks = (() => {
  const R = rng(20261030);
  const weights = words.map(w => w.text.length);
  const wsum = weights.reduce((a, b) => a + b, 0);
  const perRing = N_SPARKS / WIRE_RINGS.length;
  const list = [];
  for (let i = 0; i < N_SPARKS; i++) {
    let pick = R() * wsum, wi = 0;
    while (pick > weights[wi]) { pick -= weights[wi]; wi++; }
    const w = words[wi];
    const p0 = v3.add(v3.add(w.center, v3.mul(RIGHT, (R() - 0.5) * w.width)), [0, 0, (R() - 0.5) * w.height]);
    const out = v3.norm(v3.add(v3.sub(p0, WORD_CENTER), [(R() - 0.5) * 1.5, (R() - 0.5) * 1.5, 0.6 + R()]));
    const vel = v3.mul(out, 1.4 + R() * 1.8);
    const ring = Math.floor(i / perRing);
    const slot = i % perRing;
    list.push({ p0, vel, ring, theta: (slot / perRing) * TAU + ring * 0.37, size: 0.028 + R() * 0.026,
      twinkle: R() * TAU, arrive: T.ringFirst + ring * T.ringStep - 0.05 + (R() - 0.5) * 0.08,
      turns: 1 + (R() < 0.5 ? 0 : 1) });
  }
  return list;
})();

function sparkHover(s, t) {
  const tau = Math.max(0, t - T.burst - 0.03), k = 3.2;
  const d = (1 - Math.exp(-k * tau)) / k;
  return v3.add(v3.add(s.p0, v3.mul(s.vel, d)),
    [0.05 * Math.sin(3 * t + s.twinkle), 0.05 * Math.cos(2.6 * t + s.twinkle), -0.12 * tau * tau]);
}
const wrap = a => a - TAU * Math.floor((a + Math.PI) / TAU);
function sparkState(s, t) {
  const born = T.burst + 0.03;
  if (t < born) return { pos: s.p0, scale: 0 };
  const tw = 0.75 + 0.25 * Math.sin(17 * t + s.twinkle);
  const pop = smooth(born, born + 0.06, t);
  if (t <= T.spiralStart) return { pos: sparkHover(s, t), scale: s.size * pop * tw };
  const start = sparkHover(s, T.spiralStart);
  const ring = WIRE_RINGS[s.ring];
  const target = [ring.r * Math.cos(s.theta), ring.r * Math.sin(s.theta), JAR_BASE_Z + ring.z];
  const u = clamp((t - T.spiralStart) / (s.arrive - T.spiralStart));
  const e = EASE.inOut(u);
  const r0 = Math.hypot(start[0], start[1]), a0 = Math.atan2(start[1], start[0]);
  const a1 = a0 + wrap(s.theta - a0) + TAU * s.turns;
  const r = lerp(r0, ring.r, e) + 0.9 * Math.sin(Math.PI * e);
  const a = lerp(a0, a1, e);
  const z = lerp(start[2], target[2], EASE.inOut(clamp(u * 1.1)));
  const fade = 1 - smooth(s.arrive, s.arrive + 0.14, t);
  return { pos: [r * Math.cos(a), r * Math.sin(a), z], scale: s.size * tw * (fade * (1 + 0.6 * smooth(s.arrive - 0.1, s.arrive, t))) };
}

// wire ring k flashes in as its sparks land, and hides when clay covers it
const clayHi = track([[T.clay[0], -0.1], [T.clay[1], JAR.H + 0.1]], 'inOut');
const kraftHi = track([[T.kraft[0], -0.1], [T.kraft[1], JAR.H + 0.1]], 'inOut');
const glassHi = track([[T.glass[0], -0.1], [T.glass[1], JAR.H + 0.1]], 'inOut');
const finalHi = track([[T.final[0], -0.1], [T.final[1], JAR.H + 0.1]], 'inOut');
const ringTime = k => T.ringFirst + k * T.ringStep + 0.08;
function wireScale(k, t) {
  const appear = EASE.back(clamp((t - ringTime(k)) / 0.14));
  const covered = smooth(WIRE_RINGS[k].z - 0.02, WIRE_RINGS[k].z + 0.12, clayHi(t));
  return appear * (1 - covered);
}
const wireGlow = t => {
  let g = 0;
  WIRE_RINGS.forEach((_, k) => { g = Math.max(g, pulse(t, ringTime(k), 0.04, 0.02, 0.35)); });
  return 1.5 + 4 * g;
};

const turntable = track([[T.spin[0], 0], [T.spin[1], 2 * TAU]], 'inOut');
const labelGlow = t => smooth(T.glow, T.glow + 0.3, t) * (0.8 + 0.2 * Math.sin(TAU * 0.7 * (t - T.glow)))
  + 0.6 * pulse(t, T.glow, 0.1, 0.05, 0.5);

// ---------------------------------------------------------------- lights

// The room starts dark: a bare work bulb and a whisper of ambient. Each
// softbox is a modest step up; exposure never changes, so nothing blows out.
const lightOn = t0 => t => smooth(t0, t0 + 0.07, t) * (1 + 0.15 * pulse(t, t0, 0.04, 0.02, 0.2));
const LIGHTS = { key: lightOn(T.lights.key), rim: lightOn(T.lights.rim), top: lightOn(T.lights.top) };
// warm glow that hangs over the turntable while the jar is conjured
const jarGlow = t => smooth(4.3, 5.2, t) * (1 + 0.5 * pulse(t, 6.45, 0.2, 0.3, 0.6)) * (t < 10 ? 1 : 0);
const exposure = t => -9 * EASE.in(clamp((t - T.fade[0]) / (T.fade[1] - T.fade[0])));
const wordsLight = t => Math.max(...words.map((_, i) => wordScale(i, t))) +
  0.7 * Math.exp(-6 * Math.max(0, t - T.burst)) * (t > T.burst ? 1 : 0);

// ---------------------------------------------------------------- bake

function bake() {
  const times = Array.from({ length: FRAMES }, (_, i) => i / FPS);
  const walk = bakeWalk(times);
  const ch = {};
  const put = (name, i, v) => { (ch[name] = ch[name] || new Array(FRAMES))[i] = +v.toFixed(5); };

  times.forEach((t, i) => {
    const p = heroXY(t);
    put('hero.x', i, p[0]); put('hero.y', i, p[1]); put('hero.z', i, heroHop(t)); put('hero.yaw', i, heroYaw(t));
    const { phase, amp } = walk[i];
    put('body.bob', i, 0.045 * amp * Math.abs(Math.sin(phase)));
    put('body.lean', i, heroLean(t) + 0.06 * amp);
    put('body.tilt', i, heroTilt(t) + 0.05 * amp * Math.sin(phase));
    put('body.squash', i, heroSquash(t));
    for (let leg = 0; leg < 4; leg++) {                // 0 FL, 1 FR, 2 BL, 3 BR
      const ph = phase + (leg === 0 || leg === 3 ? 0 : Math.PI);
      const air = clamp(heroHop(t) / 0.26);
      put(`leg${leg}.lift`, i, 0.11 * amp * Math.max(0, Math.sin(ph)) + 0.05 * air);
      put(`leg${leg}.swing`, i, 0.13 * amp * Math.cos(ph));
    }
    put('armL.raise', i, armRaiseL(t) + 0.12 * amp * Math.sin(phase));
    put('armR.raise', i, armRaiseR(t) + armWaveR(t) + 0.12 * amp * Math.sin(phase + Math.PI));
    put('armL.fwd', i, armFwdL(t) + 0.25 * amp * Math.sin(phase + Math.PI));
    put('armR.fwd', i, armFwdR(t) + 0.25 * amp * Math.sin(phase));
    put('mouth.open', i, mouthOpen(t)); put('mouth.smile', i, mouthSmile(t));
    put('eyes.blink', i, eyesBlink(t)); put('eyes.happy', i, eyesHappy(t));
    const look = eyesLook(t); put('eyes.lookX', i, look[0]); put('eyes.lookZ', i, look[1]);

    words.forEach((w, k) => {
      const wp = wordPos(k, t);
      put(`word${k}.x`, i, wp[0]); put(`word${k}.y`, i, wp[1]); put(`word${k}.z`, i, wp[2]);
      put(`word${k}.scale`, i, wordScale(k, t));
    });
    put('words.light', i, wordsLight(t));
    soundRings.forEach((_, k) => {
      const s = soundRingState(k, t);
      put(`sring${k}.x`, i, s.pos[0]); put(`sring${k}.y`, i, s.pos[1]); put(`sring${k}.z`, i, s.pos[2]);
      put(`sring${k}.scale`, i, s.scale); put(`sring${k}.alpha`, i, s.alpha);
    });
    sparks.forEach((s, k) => {
      const st = sparkState(s, t);
      put(`spark${k}.x`, i, st.pos[0]); put(`spark${k}.y`, i, st.pos[1]); put(`spark${k}.z`, i, st.pos[2]);
      put(`spark${k}.scale`, i, st.scale);
    });
    WIRE_RINGS.forEach((_, k) => put(`wire${k}.scale`, i, wireScale(k, t)));
    put('wire.glow', i, wireGlow(t));
    put('skin.clay', i, clayHi(t)); put('skin.kraft', i, kraftHi(t));
    put('skin.glass', i, glassHi(t)); put('skin.final', i, finalHi(t));
    put('label.glow', i, labelGlow(t));
    put('turntable.rot', i, turntable(t));
    put('light.glow', i, jarGlow(t)); put('light.key', i, LIGHTS.key(t)); put('light.rim', i, LIGHTS.rim(t)); put('light.top', i, LIGHTS.top(t));
    put('exposure', i, exposure(t));
    CAMS.forEach(([pos, tgt], k) => {
      const a = pos(t), b = tgt(t);
      ['x', 'y', 'z'].forEach((ax, j) => { put(`cam${k}.${ax}`, i, a[j]); put(`cam${k}.t${ax}`, i, b[j]); });
    });
  });

  const layout = {
    units: 'cm', fps: FPS, frames: FRAMES, duration: DURATION,
    plinth: PLINTH, turntable: TURNTABLE, jarBaseZ: JAR_BASE_Z, jar: JAR, wireRings: WIRE_RINGS,
    heroSpot: HERO_SPOT, heroAside: HERO_ASIDE,
    stands: STANDS,
    tripod: { x: 3.4, y: -9.2, h: 3.0 },
    cart: { x: 7.6, y: -4.2, w: 2.0, d: 1.4, h: 2.4 },
    appleBoxes: { x: -8.2, y: -8.0 },
    coil: { x: 5.4, y: -11.6 },
    plant: { x: -10.6, y: 7.6 },
    bulb: { x: -6.2, y: -6.8, z: 6.2 },
    room: { x: [-24, 24], y: [-34, 16], z: 26 },
    sweep: { width: 34, front: -9, curveY: 10, radius: 5, top: 20 },
    cameras: CAM_RIGS,
    words: words.map(w => ({ text: w.text, width: w.width, size: WORD_SIZE })), wordYaw: WORD_YAW,
    soundRings: soundRings.map(r => ({ dir: r.dir })),
    nSparks: N_SPARKS,
    timing: T,
  };
  return { layout, channels: ch };
}

if (require.main === module) {
  const out = process.argv[2] || path.join(__dirname, 'build', 'baked.json');
  fs.mkdirSync(path.dirname(out), { recursive: true });
  const data = bake();
  fs.writeFileSync(out, JSON.stringify(data));
  console.log(`baked ${Object.keys(data.channels).length} channels x ${FRAMES} frames -> ${out}`);
}

module.exports = { bake };
