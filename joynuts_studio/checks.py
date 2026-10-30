"""Per-frame checks for the JoyNuts shot.

Runs on every frame of the built scene and fails if:
  * the hero's body or nub arms enter the plinth, turntable, jar, any light
    stand / softbox / boom, the tripod or prop camera, or the monitor cart;
  * a shot camera passes through the hero or a prop while it is live;
  * the floating words do not face the shot-1 camera or read mirrored.

Geometry is tested on the evaluated meshes in world space: an exact BVH
triangle-overlap test, plus a signed nearest-surface distance from sampled
hero vertices (negative = inside), which also catches full containment.

    python checks.py                      # builds the scene first, then checks
    blender -b build/joynuts.blend -P checks.py
"""

import json
import math
import os
import sys

import bpy
from mathutils import Vector
from mathutils.bvhtree import BVHTree

HERE = os.path.dirname(os.path.abspath(__file__))

HERO_PARTS = ['HeroBody', 'HeroFace', 'HeroArmR', 'HeroArmL']
OBSTACLE_GROUPS = {
    'plinth': ['Plinth'],
    'turntable': ['Turntable'],
    'jar': ['JarCollider'],
    'stand_key': ['Stand_Key', 'Softbox_KeyShell', 'Yoke_Key'],
    'stand_rim': ['Stand_Rim', 'Softbox_RimShell', 'Yoke_Rim'],
    'stand_boom': ['Stand_Boom', 'Stand_BoomArm', 'Softbox_TopShell', 'Boom_Counterweight'],
    'tripod': ['Tripod', 'PropCamera'],
    'cart': ['MonitorCart', 'Monitor'],
}
# Closed, single-shell meshes get a signed distance (negative = inside). Stands,
# tripods and carts are unions of overlapping tubes and boxes, and the softbox
# shell is open at the front, so they use unsigned distance plus the exact
# triangle-overlap test (a hero part can't get inside them without crossing a surface).
CLOSED = {'Plinth', 'Turntable', 'JarCollider', 'HeroBody', 'HeroArmR', 'HeroArmL'}
HERO_MARGIN = 0.0     # any penetration fails
CAMERA_CLEARANCE = 0.5
SAMPLE_STEP = 3       # every n-th hero vertex for the distance probe
NEAR = 1.5            # only probe obstacles whose bounds are this close


def world_mesh(ob, dg):
    ev = ob.evaluated_get(dg)
    me = ev.to_mesh()
    mw = ev.matrix_world
    verts = [mw @ v.co for v in me.vertices]
    polys = [tuple(p.vertices) for p in me.polygons]
    ev.to_mesh_clear()
    return verts, polys


def bounds(verts):
    xs, ys, zs = zip(*verts)
    return Vector((min(xs), min(ys), min(zs))), Vector((max(xs), max(ys), max(zs)))


def boxes_near(a, b, pad):
    return all(a[0][i] - pad <= b[1][i] and b[0][i] - pad <= a[1][i] for i in range(3))


class Shape:
    def __init__(self, name, verts, polys):
        self.name = name
        self.verts = verts
        self.bvh = BVHTree.FromPolygons(verts, polys, all_triangles=False)
        self.box = bounds(verts)
        self.closed = name in CLOSED

    def signed(self, p):
        hit, n, _, d = self.bvh.find_nearest(p)
        if hit is None:
            return math.inf
        return d if not self.closed or (p - hit).dot(n) >= 0 else -d


def snapshot(names, dg):
    out = []
    for nm in names:
        ob = bpy.data.objects.get(nm)
        if ob is None:
            raise RuntimeError(f'checks: object {nm!r} missing from scene')
        v, p = world_mesh(ob, dg)
        out.append(Shape(nm, v, p))
    return out


def shot_of(L, f):
    t = (f - 1) / L['fps']
    for k, c in enumerate(L['cameras']):
        if c['start'] <= t < c['end'] or (k == len(L['cameras']) - 1 and t >= c['start']):
            return k, c
    return 0, L['cameras'][0]


def run(L=None, report=None):
    sc = bpy.context.scene
    if L is None:
        with open(os.path.join(HERE, 'build', 'baked.json')) as f:
            L = json.load(f)['layout']
    dg = bpy.context.evaluated_depsgraph_get()
    static = {g: None for g in OBSTACLE_GROUPS}
    moving = {'turntable', 'jar'}
    stats = {g: {'min_clearance': math.inf, 'frame': None} for g in OBSTACLE_GROUPS}
    cam_stats = {}
    failures = []
    word_obs = [ob for ob in bpy.data.objects if ob.type == 'FONT' and ob.name.startswith('Word')]

    for f in range(sc.frame_start, sc.frame_end + 1):
        sc.frame_set(f)
        dg = bpy.context.evaluated_depsgraph_get()
        hero = snapshot(HERO_PARTS, dg)
        hero_box = bounds([v for h in hero for v in h.verts])
        samples = [v for h in hero for v in h.verts[::SAMPLE_STEP]]
        for g, names in OBSTACLE_GROUPS.items():
            if static[g] is None or g in moving:
                static[g] = snapshot(names, dg)
            for obs in static[g]:
                if not boxes_near(hero_box, obs.box, NEAR):
                    continue
                for h in hero:
                    if h.bvh.overlap(obs.bvh):
                        failures.append({'frame': f, 'hero': h.name, 'obstacle': obs.name, 'kind': 'intersect'})
                d = min(obs.signed(p) for p in samples)
                if d < stats[g]['min_clearance']:
                    stats[g] = {'min_clearance': round(d, 4), 'frame': f, 'part': obs.name}
                if d < HERO_MARGIN:
                    failures.append({'frame': f, 'obstacle': obs.name, 'kind': 'inside', 'depth': round(-d, 4)})

        # live camera: clear of the hero and of every prop
        k, rig = shot_of(L, f)
        cam = bpy.data.objects[rig['name']].evaluated_get(dg)
        cp = cam.matrix_world.translation.copy()
        clear = min(h.signed(cp) for h in hero)
        for g in OBSTACLE_GROUPS:
            for obs in static[g]:
                if boxes_near((cp, cp), obs.box, CAMERA_CLEARANCE + 0.5):
                    clear = min(clear, obs.signed(cp))
        cs = cam_stats.setdefault(rig['name'], {'min_clearance': math.inf, 'frame': None})
        if clear < cs['min_clearance']:
            cam_stats[rig['name']] = {'min_clearance': round(clear, 4), 'frame': f}
        if clear < CAMERA_CLEARANCE:
            failures.append({'frame': f, 'camera': rig['name'], 'kind': 'camera_clearance', 'clearance': clear})

        # words: face the camera and read left-to-right from it
        if k == 0:
            cam_right = cam.matrix_world.to_3x3().col[0]
            for ob in word_obs:
                ev = ob.evaluated_get(dg)
                if ev.matrix_world.to_scale().x < 1e-3:
                    continue
                m = ev.matrix_world.to_3x3().normalized()
                facing = m.col[2].dot((cp - ev.matrix_world.translation).normalized())
                reading = m.col[0].dot(cam_right)
                if facing < 0.5 or reading < 0.5:
                    failures.append({'frame': f, 'word': ob.name, 'kind': 'word_orientation',
                                     'facing': round(facing, 3), 'reading': round(reading, 3)})
        if f % 120 == 0:
            print(f'[checks] frame {f}/{sc.frame_end}  failures so far: {len(failures)}', flush=True)

    ok = not failures
    summary = {}
    for fl in failures:
        k = f"{fl['kind']}:{fl.get('obstacle') or fl.get('camera') or fl.get('word')}"
        s = summary.setdefault(k, {'count': 0, 'first': fl['frame'], 'last': fl['frame']})
        s['count'] += 1
        s['last'] = fl['frame']
    result = {'summary': summary, 'ok': ok, 'frames': sc.frame_end - sc.frame_start + 1, 'hero_clearance': stats,
              'camera_clearance': cam_stats, 'failures': failures[:200], 'failure_count': len(failures)}
    if report:
        with open(report, 'w') as fh:
            json.dump(result, fh, indent=2, default=str)
    print('[checks] hero min clearance per obstacle (cm):')
    for g, s in stats.items():
        print(f'   {g:12s} ' + (f'{s["min_clearance"]:>8} at frame {s["frame"]}' if s['frame'] else f'never within {NEAR} cm'))
    print('[checks] camera min clearance (cm):')
    for g, s in cam_stats.items():
        print(f'   {g:24s} {s["min_clearance"]:>8} at frame {s["frame"]}')
    for k, s in summary.items():
        print(f'[checks]   {k}: {s["count"]} frames ({s["first"]}-{s["last"]})')
    print(f'[checks] {"PASS" if ok else "FAIL"}: {len(failures)} failures over {result["frames"]} frames')
    return ok


if __name__ == '__main__':
    if 'HeroBody' not in bpy.data.objects:
        sys.path.insert(0, HERE)
        import build
        opts = build.parse_args()
        opts['save'] = None
        L, _ = build.build(opts)
        ok = run(L, report=os.path.join(HERE, 'build', 'checks_report.json'))
    else:
        ok = run(report=os.path.join(HERE, 'build', 'checks_report.json'))
    sys.exit(0 if ok else 1)
