"""JoyNuts "make it crunch." — builds the studio, hero and jar in Blender and
keys every frame from the JS bake (build/baked.json, written by tracks.js).

Runs with the Blender binary or the bpy module:

    blender -b -P build.py -- [options]
    python build.py [options]            # with `pip install bpy`

Options:
    --baked PATH      baked tracks (default build/baked.json; run tracks.js first)
    --save PATH       save the .blend (default build/joynuts.blend)
    --check           run the per-frame collision checks after building
    --render DIR      render PNG frames into DIR
    --frames SPEC     frames to render, e.g. 1-1020, 120,400,700 (default all)
    --res PCT         resolution percentage (default 100 = 1920x1080)
    --samples N       Eevee samples (default 64)

Units: 1 Blender unit = 1 cm (scene unit scale 0.01), so the hero is 2.6 BU tall.
"""

import bpy
import bmesh
import json
import math
import os
import random
import sys
from mathutils import Matrix, Vector

HERE = os.path.dirname(os.path.abspath(__file__))
TAU = math.tau


# ------------------------------------------------------------------ args

def parse_args():
    argv = sys.argv[sys.argv.index('--') + 1:] if '--' in sys.argv else sys.argv[1:]
    opts = {'baked': os.path.join(HERE, 'build', 'baked.json'),
            'save': os.path.join(HERE, 'build', 'joynuts.blend'),
            'check': False, 'render': None, 'frames': None, 'res': 100, 'samples': 64}
    i = 0
    while i < len(argv):
        a = argv[i]
        if a == '--check':
            opts['check'] = True
        elif a in ('--baked', '--save', '--render', '--frames'):
            opts[a[2:]] = argv[i + 1]; i += 1
        elif a in ('--res', '--samples'):
            opts[a[2:]] = int(argv[i + 1]); i += 1
        i += 1
    return opts


def parse_frames(spec, last):
    if not spec:
        return list(range(1, last + 1))
    out = []
    for part in spec.split(','):
        if '-' in part:
            a, b = part.split('-')
            out += range(int(a), int(b) + 1)
        else:
            out.append(int(part))
    return out


# ------------------------------------------------------------------ helpers

def srgb(h, a=1.0):
    """'#rrggbb' -> linear RGBA."""
    h = h.lstrip('#')
    c = [int(h[i:i + 2], 16) / 255 for i in (0, 2, 4)]
    lin = [x / 12.92 if x <= 0.04045 else ((x + 0.055) / 1.055) ** 2.4 for x in c]
    return (*lin, a)


COLL = {}


def coll(name):
    if name not in COLL:
        c = bpy.data.collections.new(name)
        bpy.context.scene.collection.children.link(c)
        COLL[name] = c
    return COLL[name]


def add_obj(name, data, collection='Studio', parent=None, loc=(0, 0, 0), rot=(0, 0, 0), scale=(1, 1, 1)):
    ob = bpy.data.objects.new(name, data)
    coll(collection).objects.link(ob)
    ob.parent = parent
    ob.location = loc
    ob.rotation_euler = rot
    ob.scale = scale
    return ob


def empty(name, collection='Rig', parent=None, loc=(0, 0, 0), rot=(0, 0, 0)):
    ob = add_obj(name, None, collection, parent, loc, rot)
    ob.empty_display_size = 0.3
    return ob


def bm_to_obj(name, bm, mat=None, collection='Studio', parent=None, loc=(0, 0, 0), rot=(0, 0, 0),
              scale=(1, 1, 1), smooth=True, mats=None):
    me = bpy.data.meshes.new(name)
    bm.to_mesh(me)
    bm.free()
    if smooth:
        me.shade_smooth()
    for m in (mats or ([mat] if mat else [])):
        me.materials.append(m)
    return add_obj(name, me, collection, parent, loc, rot, scale)


def bm_sphere(r=1.0, u=24, v=12, scale=(1, 1, 1), loc=(0, 0, 0), bm=None):
    bm = bm or bmesh.new()
    res = bmesh.ops.create_uvsphere(bm, u_segments=u, v_segments=v, radius=r)
    for vt in res['verts']:
        vt.co = Vector((vt.co.x * scale[0] + loc[0], vt.co.y * scale[1] + loc[1], vt.co.z * scale[2] + loc[2]))
    return bm


def bm_cyl(r1, r2, depth, segs=32, z0=0.0, bm=None, matrix=None):
    """Cylinder/cone standing on z0 (not centred)."""
    bm = bm or bmesh.new()
    m = Matrix.Translation((0, 0, z0 + depth / 2))
    if matrix is not None:
        m = matrix @ m
    bmesh.ops.create_cone(bm, cap_ends=True, segments=segs, radius1=r1, radius2=r2, depth=depth, matrix=m)
    return bm


def bm_box(size, loc=(0, 0, 0), bm=None, matrix=None):
    bm = bm or bmesh.new()
    m = Matrix.Translation(loc) @ Matrix.Diagonal((*size, 1))
    if matrix is not None:
        m = matrix @ m
    bmesh.ops.create_cube(bm, size=1.0, matrix=m)
    return bm


def bm_torus(R, r, segs=64, rsegs=12, bm=None, matrix=None):
    bm = bm or bmesh.new()
    rings = []
    for i in range(segs):
        a = TAU * i / segs
        ring = []
        for j in range(rsegs):
            b = TAU * j / rsegs
            p = Vector(((R + r * math.cos(b)) * math.cos(a), (R + r * math.cos(b)) * math.sin(a), r * math.sin(b)))
            ring.append(bm.verts.new(matrix @ p if matrix is not None else p))
        rings.append(ring)
    for i in range(segs):
        A, B = rings[i], rings[(i + 1) % segs]
        for j in range(rsegs):
            k = (j + 1) % rsegs
            bm.faces.new((A[j], B[j], B[k], A[k]))
    return bm


def bm_tube(points, radius, rsegs=10, bm=None, cap=True):
    """Tube along a polyline of Vectors."""
    bm = bm or bmesh.new()
    rings = []
    n = len(points)
    for i, p in enumerate(points):
        t = (points[min(i + 1, n - 1)] - points[max(i - 1, 0)]).normalized()
        ref = Vector((0, 0, 1)) if abs(t.z) < 0.9 else Vector((1, 0, 0))
        u = t.cross(ref).normalized()
        w = t.cross(u)
        rings.append([bm.verts.new(p + radius * (math.cos(TAU * j / rsegs) * u + math.sin(TAU * j / rsegs) * w))
                      for j in range(rsegs)])
    for A, B in zip(rings, rings[1:]):
        for j in range(rsegs):
            k = (j + 1) % rsegs
            bm.faces.new((A[j], A[k], B[k], B[j]))
    if cap:
        bm.faces.new(list(reversed(rings[0])))
        bm.faces.new(rings[-1])
    return bm


def lathe(profile, segs=72, closed=False, bm=None):
    """Revolve [(r, z), ...] around Z. Profile should run bottom->top on the outside."""
    bm = bm or bmesh.new()
    rings = []
    for r, z in profile:
        if r < 1e-6:
            rings.append([bm.verts.new((0, 0, z))])
        else:
            rings.append([bm.verts.new((r * math.cos(TAU * j / segs), r * math.sin(TAU * j / segs), z))
                          for j in range(segs)])
    pairs = list(zip(rings, rings[1:]))
    if closed:
        pairs.append((rings[-1], rings[0]))
    for A, B in pairs:
        if len(A) == 1 and len(B) == 1:
            continue
        for j in range(segs):
            k = (j + 1) % segs
            if len(A) == 1:
                bm.faces.new((A[0], B[k], B[j]))
            elif len(B) == 1:
                bm.faces.new((A[j], A[k], B[0]))
            else:
                bm.faces.new((A[j], A[k], B[k], B[j]))
    return bm


def bevel(ob, width=0.03, segs=3):
    m = ob.modifiers.new('Bevel', 'BEVEL')
    m.width = width
    m.segments = segs
    m.limit_method = 'ANGLE'
    return m


def subsurf(ob, levels=1):
    m = ob.modifiers.new('Subsurf', 'SUBSURF')
    m.levels = levels
    m.render_levels = levels
    return m


def look_matrix(pos, target, up=Vector((0, 0, 1))):
    """Matrix whose -Z looks from pos to target (like a light or camera)."""
    f = (Vector(target) - Vector(pos)).normalized()
    if abs(f.dot(up)) > 0.999:
        up = Vector((0, 1, 0))
    x = f.cross(up).normalized()
    y = x.cross(f)
    m = Matrix((x, y, -f)).transposed().to_4x4()
    m.translation = pos
    return m


def font(*candidates):
    for p in candidates:
        if os.path.exists(p):
            try:
                return bpy.data.fonts.load(p, check_existing=True)
            except RuntimeError:
                pass
    return None


# ------------------------------------------------------------------ keying

FRAME0 = 1  # baked index i -> frame i + 1


def key(datablock, data_path, values, index=0):
    """Key one property on every frame from a list of baked values."""
    ad = datablock.animation_data or datablock.animation_data_create()
    if ad.action is None:
        ad.action = bpy.data.actions.new(f'{datablock.name}Action')
    fc = ad.action.fcurve_ensure_for_datablock(datablock, data_path, index=index)
    n = len(values)
    fc.keyframe_points.add(n)
    co = [0.0] * (2 * n)
    co[0::2] = [i + FRAME0 for i in range(n)]
    co[1::2] = values
    fc.keyframe_points.foreach_set('co', co)
    fc.keyframe_points.foreach_set('interpolation', [1] * n)  # LINEAR
    fc.update()
    return fc


# ------------------------------------------------------------------ materials

def node_mat(name):
    m = bpy.data.materials.new(name)
    nt = m.node_tree
    for n in list(nt.nodes):
        nt.nodes.remove(n)
    out = nt.nodes.new('ShaderNodeOutputMaterial')
    out.location = (600, 0)
    return m, nt, out


def principled(nt, color, rough=0.5, metal=0.0, coat=0.0, coat_rough=0.05, emit=None, emit_strength=0.0,
               sss=0.0, spec=0.5):
    p = nt.nodes.new('ShaderNodeBsdfPrincipled')
    p.inputs['Base Color'].default_value = color
    p.inputs['Roughness'].default_value = rough
    p.inputs['Metallic'].default_value = metal
    p.inputs['Coat Weight'].default_value = coat
    p.inputs['Coat Roughness'].default_value = coat_rough
    p.inputs['Specular IOR Level'].default_value = spec
    if sss:
        p.inputs['Subsurface Weight'].default_value = sss
        p.inputs['Subsurface Radius'].default_value = (0.08, 0.04, 0.02)
        p.inputs['Subsurface Scale'].default_value = 0.1
    if emit:
        p.inputs['Emission Color'].default_value = emit
        p.inputs['Emission Strength'].default_value = emit_strength
    return p


def bump(nt, bsdf, scale=60.0, strength=0.1, stretch=(1, 1, 1), detail=4.0, kind='noise'):
    tc = nt.nodes.new('ShaderNodeTexCoord')
    mp = nt.nodes.new('ShaderNodeMapping')
    mp.inputs['Scale'].default_value = stretch
    nt.links.new(tc.outputs['Object'], mp.inputs['Vector'])
    if kind == 'wave':
        tx = nt.nodes.new('ShaderNodeTexWave')
        tx.inputs['Scale'].default_value = scale
        tx.inputs['Distortion'].default_value = 4.0
        tx.inputs['Detail'].default_value = detail
    else:
        tx = nt.nodes.new('ShaderNodeTexNoise')
        tx.inputs['Scale'].default_value = scale
        tx.inputs['Detail'].default_value = detail
    nt.links.new(mp.outputs['Vector'], tx.inputs['Vector'])
    b = nt.nodes.new('ShaderNodeBump')
    b.inputs['Strength'].default_value = strength
    nt.links.new(tx.outputs['Fac'], b.inputs['Height'])
    nt.links.new(b.outputs['Normal'], bsdf.inputs['Normal'])
    return tx


def simple_mat(name, color, rough=0.5, metal=0.0, coat=0.0, emit=None, emit_strength=0.0, sss=0.0,
               bump_args=None):
    m, nt, out = node_mat(name)
    p = principled(nt, color, rough, metal, coat, emit=emit, emit_strength=emit_strength, sss=sss)
    nt.links.new(p.outputs[0], out.inputs['Surface'])
    if bump_args:
        bump(nt, p, **bump_args)
    return m


def emission_mat(name, color, strength):
    m, nt, out = node_mat(name)
    e = nt.nodes.new('ShaderNodeEmission')
    e.inputs['Color'].default_value = color
    e.inputs['Strength'].default_value = strength
    e.name = 'EMIT'
    nt.links.new(e.outputs[0], out.inputs['Surface'])
    return m


def alpha_emission_mat(name, color, strength):
    """Emission mixed with transparency; node 'ALPHA' (Value) is keyed."""
    m, nt, out = node_mat(name)
    e = nt.nodes.new('ShaderNodeEmission')
    e.inputs['Color'].default_value = color
    e.inputs['Strength'].default_value = strength
    tr = nt.nodes.new('ShaderNodeBsdfTransparent')
    a = nt.nodes.new('ShaderNodeValue')
    a.name = 'ALPHA'
    mix = nt.nodes.new('ShaderNodeMixShader')
    nt.links.new(a.outputs[0], mix.inputs[0])
    nt.links.new(tr.outputs[0], mix.inputs[1])
    nt.links.new(e.outputs[0], mix.inputs[2])
    nt.links.new(mix.outputs[0], out.inputs['Surface'])
    return m


def glass_shader(nt, tint=(1, 1, 1, 1), rough=0.03):
    """Thin clear glass: fresnel-weighted gloss over transparency (lets the nuts show)."""
    lw = nt.nodes.new('ShaderNodeLayerWeight')
    lw.inputs['Blend'].default_value = 0.18
    gl = nt.nodes.new('ShaderNodeBsdfGlossy')
    gl.inputs['Roughness'].default_value = rough
    tr = nt.nodes.new('ShaderNodeBsdfTransparent')
    tr.inputs['Color'].default_value = tint
    mix = nt.nodes.new('ShaderNodeMixShader')
    ramp = nt.nodes.new('ShaderNodeMath')
    ramp.operation = 'MULTIPLY_ADD'
    ramp.inputs[1].default_value = 0.85
    ramp.inputs[2].default_value = 0.08
    ramp.use_clamp = True
    nt.links.new(lw.outputs['Fresnel'], ramp.inputs[0])
    nt.links.new(ramp.outputs[0], mix.inputs[0])
    nt.links.new(tr.outputs[0], mix.inputs[1])
    nt.links.new(gl.outputs[0], mix.inputs[2])
    return mix


def wipe(m, nt, out, shader_out, jar_root, seam=srgb('#ffd27a')):
    """Show the surface only where LO < z(jar space) < HI, with a glowing seam at HI.
    Value nodes 'HI' and 'LO' are keyed from the bake."""
    tc = nt.nodes.new('ShaderNodeTexCoord')
    tc.object = jar_root
    sep = nt.nodes.new('ShaderNodeSeparateXYZ')
    nt.links.new(tc.outputs['Object'], sep.inputs[0])
    hi = nt.nodes.new('ShaderNodeValue'); hi.name = 'HI'; hi.outputs[0].default_value = 99
    lo = nt.nodes.new('ShaderNodeValue'); lo.name = 'LO'; lo.outputs[0].default_value = -99
    below = nt.nodes.new('ShaderNodeMath'); below.operation = 'LESS_THAN'
    nt.links.new(sep.outputs['Z'], below.inputs[0]); nt.links.new(hi.outputs[0], below.inputs[1])
    above = nt.nodes.new('ShaderNodeMath'); above.operation = 'GREATER_THAN'
    nt.links.new(sep.outputs['Z'], above.inputs[0]); nt.links.new(lo.outputs[0], above.inputs[1])
    vis = nt.nodes.new('ShaderNodeMath'); vis.operation = 'MULTIPLY'
    nt.links.new(below.outputs[0], vis.inputs[0]); nt.links.new(above.outputs[0], vis.inputs[1])
    tr = nt.nodes.new('ShaderNodeBsdfTransparent')
    mix = nt.nodes.new('ShaderNodeMixShader')
    nt.links.new(vis.outputs[0], mix.inputs[0])
    nt.links.new(tr.outputs[0], mix.inputs[1])
    nt.links.new(shader_out, mix.inputs[2])
    # seam: a thin band just under HI
    d = nt.nodes.new('ShaderNodeMath'); d.operation = 'SUBTRACT'
    nt.links.new(hi.outputs[0], d.inputs[0]); nt.links.new(sep.outputs['Z'], d.inputs[1])
    band = nt.nodes.new('ShaderNodeMapRange')
    band.inputs['From Min'].default_value = 0.0
    band.inputs['From Max'].default_value = 0.06
    band.inputs['To Min'].default_value = 1.0
    band.inputs['To Max'].default_value = 0.0
    nt.links.new(d.outputs[0], band.inputs['Value'])
    sv = nt.nodes.new('ShaderNodeMath'); sv.operation = 'MULTIPLY'
    nt.links.new(band.outputs[0], sv.inputs[0]); nt.links.new(vis.outputs[0], sv.inputs[1])
    em = nt.nodes.new('ShaderNodeEmission')
    em.inputs['Color'].default_value = seam
    st = nt.nodes.new('ShaderNodeMath'); st.operation = 'MULTIPLY'; st.inputs[1].default_value = 6.0
    nt.links.new(sv.outputs[0], st.inputs[0])
    nt.links.new(st.outputs[0], em.inputs['Strength'])
    add = nt.nodes.new('ShaderNodeAddShader')
    nt.links.new(mix.outputs[0], add.inputs[0])
    nt.links.new(em.outputs[0], add.inputs[1])
    nt.links.new(add.outputs[0], out.inputs['Surface'])
    m.use_transparent_shadow = True
    return m


def wipe_mat(name, jar_root, build_shader):
    m, nt, out = node_mat(name)
    s = build_shader(nt)
    return wipe(m, nt, out, s, jar_root)


# ------------------------------------------------------------------ hero

class Almond:
    """Analytic almond body (local to the body pivot, bottom at z=0, front = +Y)."""
    ax, ay, hz = 0.78, 0.64, 1.15

    @classmethod
    def rho(cls, s):
        top = max(0.0, s)
        return max(0.0, 1 - s * s) ** (0.5 + 0.28 * top) * (1 - 0.1 * top) * (1 + 0.05 * (1 - s * s))

    @classmethod
    def point(cls, phi, s):
        r = cls.rho(s)
        return Vector((cls.ax * r * math.cos(phi), cls.ay * r * math.sin(phi), cls.hz * (1 + s)))

    @classmethod
    def normal(cls, phi, s):
        e = 1e-3
        dp = cls.point(phi + e, s) - cls.point(phi - e, s)
        ds = cls.point(phi, s + e) - cls.point(phi, s - e)
        return dp.cross(ds).normalized()


FACE = {'span_phi': 0.74, 'span_s': 0.36, 's0': 0.02, 'height': 0.055, 'lip': 0.006}


def face_height(q):
    t = min(1.0, max(0.0, (q - 0.68) / 0.32))
    return FACE['lip'] + FACE['height'] * (1 - t * t * (3 - 2 * t))


def face_point(fx, fy, extra=0.0):
    """Point on the raised face plate; fx = to its right, fy = up, both in the unit disk."""
    phi = math.pi / 2 - FACE['span_phi'] * fx
    s = FACE['s0'] + FACE['span_s'] * fy
    n = Almond.normal(phi, s)
    q = math.hypot(fx, fy)
    return Almond.point(phi, s) + n * (face_height(q) + extra), n


def surface_frame(pos, n):
    """Object matrix with local Y = outward normal, X = hero right, Z = up-ish."""
    y = n.normalized()
    x = y.cross(Vector((0, 0, 1))).normalized()
    z = x.cross(y)
    m = Matrix((x, y, z)).transposed().to_4x4()
    m.translation = pos
    return m


def build_hero(ch, mats):
    root = empty('HeroRoot')
    pivot = empty('HeroBodyPivot', parent=root, loc=(0, 0, 0.3))

    # body: analytic almond
    bm = bmesh.new()
    segs, rings = 64, 40
    ss = [-math.cos(math.pi * i / rings) for i in range(rings + 1)]
    bottom = bm.verts.new(Almond.point(0, -1))
    grid = [[bm.verts.new(Almond.point(TAU * j / segs, s)) for j in range(segs)] for s in ss[1:-1]]
    top = bm.verts.new(Almond.point(0, 1))
    for j in range(segs):
        k = (j + 1) % segs
        bm.faces.new((bottom, grid[0][k], grid[0][j]))
        bm.faces.new((grid[-1][j], grid[-1][k], top))
    for A, B in zip(grid, grid[1:]):
        for j in range(segs):
            k = (j + 1) % segs
            bm.faces.new((A[j], A[k], B[k], B[j]))
    body = bm_to_obj('HeroBody', bm, mats['vinyl'], 'Hero', pivot)
    subsurf(body, 1)

    # raised, glossy moulded face plate
    bm = bmesh.new()
    nq, nb = 14, 72
    centre = bm.verts.new(face_point(0, 0)[0])
    rows = [[bm.verts.new(face_point(q / nq * math.cos(TAU * b / nb), q / nq * math.sin(TAU * b / nb))[0])
             for b in range(nb)] for q in range(1, nq + 1)]
    for b in range(nb):
        bm.faces.new((centre, rows[0][b], rows[0][(b + 1) % nb]))
    for A, B in zip(rows, rows[1:]):
        for b in range(nb):
            c = (b + 1) % nb
            bm.faces.new((A[b], B[b], B[c], A[c]))
    bm.normal_update()
    face = bm_to_obj('HeroFace', bm, mats['face'], 'Hero', pivot)
    # make sure the plate points outward
    if face.data.polygons[0].normal.y < 0:
        face.data.flip_normals()

    # eyes, glints, cheeks
    eyes = []
    for side, fx in (('R', 0.4), ('L', -0.4)):
        pos, n = face_point(fx, 0.32, 0.0)
        eye = bm_to_obj(f'HeroEye{side}', bm_sphere(1.0, 24, 12, (0.088, 0.045, 0.112)), mats['eye'], 'Hero', pivot)
        eye.matrix_basis = surface_frame(pos, n)
        glint = bm_to_obj(f'HeroGlint{side}', bm_sphere(1.0, 12, 6, (0.026, 0.012, 0.03)), mats['glint'], 'Hero', eye,
                          loc=(0.03, 0.04, 0.045))
        glint2 = bm_to_obj(f'HeroGlintB{side}', bm_sphere(1.0, 8, 5, (0.012, 0.008, 0.014)), mats['glint'], 'Hero',
                           eye, loc=(-0.035, 0.038, -0.04))
        eyes.append((eye, Vector(eye.location), eye.matrix_basis.to_3x3()))
    for side, fx in (('R', 0.68), ('L', -0.68)):
        pos, n = face_point(fx, -0.02, -0.004)
        cheek = bm_to_obj(f'HeroCheek{side}', bm_sphere(1.0, 16, 8, (0.1, 0.012, 0.065)), mats['cheek'], 'Hero', pivot)
        cheek.matrix_basis = surface_frame(pos, n)

    # mouth: a smile line with a 'Smile' shape key, plus an opening that scales
    pos, n = face_point(0, -0.3, 0.008)
    frame = surface_frame(pos, n)

    def smile_pts(w, depth):
        return [Vector((x, 0, depth * (x / w) ** 2 - depth * 0.35)) for x in [w * (i / 10 - 1) for i in range(21)]]
    bm = bm_tube(smile_pts(0.12, 0.022), 0.017, 10)
    line = bm_to_obj('HeroMouthLine', bm, mats['mouth_line'], 'Hero', pivot)
    line.matrix_basis = frame
    line.shape_key_add(name='Basis')
    sk = line.shape_key_add(name='Smile', from_mix=False)
    smile_bm = bm_tube(smile_pts(0.165, 0.085), 0.019, 10)
    smile_bm.verts.ensure_lookup_table()
    for v, sv in zip(sk.data, smile_bm.verts):
        v.co = sv.co
    smile_bm.free()
    mouth = bm_to_obj('HeroMouthOpen', bm_sphere(1.0, 24, 12, (0.1, 0.03, 0.075)), mats['mouth'], 'Hero', pivot)
    mpos, mn = face_point(0, -0.42, -0.006)
    mouth.matrix_basis = surface_frame(mpos, mn)
    mouth_base = Vector(mouth.location)
    tongue = bm_to_obj('HeroTongue', bm_sphere(1.0, 16, 8, (0.055, 0.015, 0.03), (0, 0.012, -0.035)), mats['tongue'],
                       'Hero', mouth)

    # nub arms (pivot at the shoulder, mesh hangs down -Z)
    arms = {}
    for side, phi in (('R', 0.0), ('L', math.pi)):
        p = Almond.point(phi, -0.12)
        p = Vector((p.x * 0.93, p.y, p.z))
        ap = empty(f'HeroArmPivot{side}', parent=pivot, loc=p)
        bm = bm_sphere(1.0, 24, 12, (0.15, 0.15, 0.27), (0, 0, -0.2))
        arm = bm_to_obj(f'HeroArm{side}', bm, mats['vinyl'], 'Hero', ap)
        arms[side] = ap

    # four short legs (parented to root so the body can bob above them)
    legs = []
    for i, (x, y) in enumerate(((-0.34, 0.26), (0.34, 0.26), (-0.34, -0.26), (0.34, -0.26))):
        bm = bm_cyl(0.13, 0.12, 0.42, 20, 0.06)
        bm_sphere(1.0, 20, 10, (0.17, 0.21, 0.085), (0, 0.03, 0.085), bm)
        leg = bm_to_obj(f'HeroLeg{i}', bm, mats['vinyl_dark'], 'Hero', root, loc=(x, y, 0))
        legs.append((leg, x, y))

    # ---- keys
    key(root, 'location', ch['hero.x'], 0)
    key(root, 'location', ch['hero.y'], 1)
    key(root, 'location', ch['hero.z'], 2)
    key(root, 'rotation_euler', ch['hero.yaw'], 2)
    key(pivot, 'location', [0.3 + v for v in ch['body.bob']], 2)
    key(pivot, 'rotation_euler', [-v for v in ch['body.lean']], 0)
    key(pivot, 'rotation_euler', ch['body.tilt'], 1)
    sq = ch['body.squash']
    key(pivot, 'scale', [v ** -0.5 for v in sq], 0)
    key(pivot, 'scale', [v ** -0.5 for v in sq], 1)
    key(pivot, 'scale', sq, 2)
    for side, sign in (('R', -1), ('L', 1)):
        ap = arms[side]
        key(ap, 'rotation_euler', ch[f'arm{side}.fwd'], 0)
        key(ap, 'rotation_euler', [sign * (0.32 + v) for v in ch[f'arm{side}.raise']], 1)
    for i, (leg, x, y) in enumerate(legs):
        key(leg, 'location', [y + v for v in ch[f'leg{i}.swing']], 1)
        key(leg, 'location', ch[f'leg{i}.lift'], 2)
    blink, happy = ch['eyes.blink'], ch['eyes.happy']
    lx, lz = ch['eyes.lookX'], ch['eyes.lookZ']
    for eye, base, rot in eyes:
        sz = [max(0.08, (1 - b) * (1 - 0.5 * h)) for b, h in zip(blink, happy)]
        key(eye, 'scale', sz, 2)
        right, up = rot.col[0], rot.col[2]
        locs = [base + right * (0.03 * a) + up * (0.025 * b + 0.02 * h) for a, b, h in zip(lx, lz, happy)]
        for j in range(3):
            key(eye, 'location', [p[j] for p in locs], j)
    key(line.data.shape_keys, 'key_blocks["Smile"].value', ch['mouth.smile'])
    op = ch['mouth.open']
    key(mouth, 'scale', [0.7 + 0.35 * v for v in op], 0)
    key(mouth, 'scale', [v if v > 0.02 else 0.0 for v in op], 2)
    key(line, 'scale', [1 - 0.25 * v for v in op], 0)
    return root


# ------------------------------------------------------------------ jar

def jar_profiles():
    body = [(0, 0), (0.78, 0), (0.88, 0.015), (0.93, 0.05), (0.95, 0.11), (0.95, 1.7), (0.935, 1.79),
            (0.9, 1.87), (0.865, 1.92), (0.86, 1.95)]
    band = [(0.95, 0.53), (0.966, 0.55), (0.966, 1.45), (0.95, 1.47)]
    lid = [(0.86, 1.95), (0.985, 1.955), (1.0, 1.985), (1.0, 2.37), (0.985, 2.425), (0.93, 2.45), (0, 2.45)]
    return body, band, lid


def merge_band(body, band):
    out = []
    for r, z in body:
        if z > band[0][1] and out and out[-1][1] <= band[0][1]:
            out += band
        if band[0][1] - 0.001 <= z <= band[-1][1] + 0.001:
            continue
        out.append((r, z))
    return out


def scaled(profile, k):
    return [(r * k, z) for r, z in profile]


def nut_mesh(rnd, jar):
    """Mixed nuts packed inside the jar, one mesh with a colour attribute."""
    bm = bmesh.new()
    col = bm.loops.layers.float_color.new('NutColor')
    kinds = [  # name, radii, colour, weight
        ('almond', (0.085, 0.05, 0.13), '#9a5a32', 3),
        ('cashew', (0.065, 0.055, 0.13), '#e8cf9c', 3),
        ('hazelnut', (0.095, 0.095, 0.1), '#7a4626', 2),
        ('macadamia', (0.1, 0.1, 0.1), '#efe0b8', 2),
        ('pistachio', (0.07, 0.06, 0.1), '#b9a77a', 1),
    ]
    weights = [k[3] for k in kinds]
    placed = []
    tries = 0
    inner = jar['r'] - 0.04
    while len(placed) < 210 and tries < 30000:
        tries += 1
        k = rnd.choices(kinds, weights)[0]
        rad = max(k[1])
        r = inner - rad * 0.9
        a = rnd.random() * TAU
        rr = r * math.sqrt(rnd.random())
        z = 0.03 + rad * 0.8 + rnd.random() * (1.86 - rad * 1.6)
        rlim = inner - rad * 0.9 if z < 1.7 else (0.83 - rad)
        if rr > rlim:
            continue
        p = Vector((rr * math.cos(a), rr * math.sin(a), z))
        if any((p - q).length < (rad + qr) * 0.78 for q, qr in placed):
            continue
        placed.append((p, rad))
        res = bmesh.ops.create_uvsphere(bm, u_segments=12, v_segments=8, radius=1.0)
        verts = res['verts']
        for v in verts:
            x, y, zz = v.co
            if k[0] == 'almond':
                taper = 1 - 0.45 * max(0, zz) ** 1.4
                x, y = x * taper, y * taper
            elif k[0] == 'cashew':
                x += 0.55 * (1 - zz * zz)
            elif k[0] == 'hazelnut' and zz > 0:
                x, y = x * (1 - 0.25 * zz * zz), y * (1 - 0.25 * zz * zz)
            v.co = Vector((x * k[1][0], y * k[1][1], zz * k[1][2]))
        rot = (Matrix.Rotation(rnd.random() * TAU, 4, 'Z') @ Matrix.Rotation(rnd.random() * math.pi, 4, 'X') @
               Matrix.Rotation(rnd.random() * TAU, 4, 'Z'))
        bmesh.ops.transform(bm, matrix=Matrix.Translation(p) @ rot, verts=verts)
        c = srgb(k[2])
        jitter = 0.85 + 0.3 * rnd.random()
        c = (c[0] * jitter, c[1] * jitter, c[2] * jitter, 1)
        for f in {f for v in verts for f in v.link_faces}:
            for l in f.loops:
                l[col] = c
    return bm, len(placed)


def bent_text(name, text, fnt, size, radius, zc, extrude=0.02, bevel_depth=0.004, spacing=1.0):
    """Text as a mesh wrapped around a cylinder of `radius`, centred on the -Y side."""
    cu = bpy.data.curves.new(name, 'FONT')
    cu.body = text
    if fnt:
        cu.font = fnt
    cu.size = size
    cu.align_x = 'CENTER'
    cu.align_y = 'CENTER'
    cu.extrude = extrude
    cu.bevel_depth = bevel_depth
    cu.space_character = spacing
    cu.resolution_u = 4
    tmp = bpy.data.objects.new(name + 'Tmp', cu)
    bpy.context.scene.collection.objects.link(tmp)
    dg = bpy.context.evaluated_depsgraph_get()
    me = bpy.data.meshes.new_from_object(tmp.evaluated_get(dg))
    bpy.data.objects.remove(tmp)
    bpy.data.curves.remove(cu)
    for v in me.vertices:
        x, y, z = v.co
        rr = radius + extrude + z
        a = x / radius
        v.co = (rr * math.sin(a), -rr * math.cos(a), zc + y)
    return me


def build_jar(L, ch, mats, fonts, jar_root):
    jar = L['jar']
    body, band, lid = jar_profiles()
    banded = merge_band(body, band)
    full = banded + lid[1:]
    pieces = {}

    def wiped(name, shader_fn):
        return wipe_mat(name, jar_root, shader_fn)

    def clay_shader(nt):
        p = principled(nt, srgb('#8e8e8c'), 0.85)
        bump(nt, p, scale=18, strength=0.12)
        return p.outputs[0]

    def kraft_shader(nt):
        p = principled(nt, srgb('#a87a4c'), 0.92, spec=0.3)
        bump(nt, p, scale=140, strength=0.18, stretch=(1, 1, 3))
        return p.outputs[0]

    def glass_fn(nt):
        return glass_shader(nt).outputs[0]

    def wood_shader(nt):
        p = principled(nt, srgb('#7b4a28'), 0.42, coat=0.4)
        tx = bump(nt, p, scale=7, strength=0.25, stretch=(1, 1, 0.15), kind='wave')
        ramp = nt.nodes.new('ShaderNodeValToRGB')
        ramp.color_ramp.elements[0].color = srgb('#5c341b')
        ramp.color_ramp.elements[1].color = srgb('#9c6a3e')
        nt.links.new(tx.outputs['Fac'], ramp.inputs[0])
        nt.links.new(ramp.outputs[0], p.inputs['Base Color'])
        return p.outputs[0]

    def label_shader(nt):
        p = principled(nt, srgb('#f1e7d2'), 0.55)
        bump(nt, p, scale=220, strength=0.05)
        return p.outputs[0]

    def gold_shader(nt):
        p = principled(nt, srgb('#e6b54a'), 0.18, metal=1.0, emit=srgb('#ffc861'), emit_strength=0.0)
        p.name = 'GOLD'
        return p.outputs[0]

    def nut_shader(nt):
        p = principled(nt, (1, 1, 1, 1), 0.55, coat=0.15)
        at = nt.nodes.new('ShaderNodeAttribute')
        at.attribute_name = 'NutColor'
        nt.links.new(at.outputs['Color'], p.inputs['Base Color'])
        bump(nt, p, scale=90, strength=0.2)
        return p.outputs[0]

    mats_jar = {
        'clay': wiped('JarClay', clay_shader),
        'kraft': wiped('JarKraft', kraft_shader),
        'glass': wiped('JarGlass', glass_fn),
        'glasslid': wiped('JarGlassLid', glass_fn),
        'nuts': wiped('JarNuts', nut_shader),
        'wood': wiped('JarWoodLid', wood_shader),
        'label': wiped('JarLabel', label_shader),
        'gold': wiped('JarGoldFoil', gold_shader),
    }
    pieces['clay'] = bm_to_obj('JarSkin1_Clay', lathe(full, 72), mats_jar['clay'], 'Jar', jar_root)
    pieces['kraft'] = bm_to_obj('JarSkin2_Kraft', lathe(scaled(full, 1.006), 72), mats_jar['kraft'], 'Jar', jar_root)
    pieces['glass'] = bm_to_obj('JarSkin3_Glass', lathe(scaled(body, 1.012), 72), mats_jar['glass'], 'Jar', jar_root)
    pieces['glasslid'] = bm_to_obj('JarSkin3_GlassLid', lathe(scaled(lid, 1.012), 72), mats_jar['glasslid'], 'Jar',
                                   jar_root)
    nbm, count = nut_mesh(random.Random(7), jar)
    pieces['nuts'] = bm_to_obj('JarNuts', nbm, mats_jar['nuts'], 'Jar', jar_root)
    # final skin: wooden lid with grip ridges, cream label, gold foil
    ridged = [(0.86, 1.95), (0.995, 1.955), (1.018, 1.99)]
    z = 1.99
    while z < 2.36:
        ridged += [(1.018, z + 0.012), (1.008, z + 0.024), (1.018, z + 0.036)]
        z += 0.036
    ridged += [(1.018, 2.38), (1.0, 2.44), (0.94, 2.465), (0, 2.465)]
    pieces['wood'] = bm_to_obj('JarSkin4_WoodLid', lathe(ridged, 96), mats_jar['wood'], 'Jar', jar_root)
    lab = [(0.962, 0.55), (0.974, 0.55), (0.974, 1.45), (0.962, 1.45)]
    pieces['label'] = bm_to_obj('JarSkin4_Label', lathe(lab, 96, closed=True), mats_jar['label'], 'Jar', jar_root)
    gbm = bmesh.new()
    for zc in (0.575, 1.425):
        lathe([(0.976, zc - 0.013), (0.979, zc - 0.01), (0.979, zc + 0.01), (0.976, zc + 0.013)], 96, True, gbm)
    pieces['trim'] = bm_to_obj('JarSkin4_GoldTrim', gbm, mats_jar['gold'], 'Jar', jar_root)
    me = bent_text('JarLogo', 'JoyNuts', fonts['label'], 0.4, 0.976, 1.07, extrude=0.012, bevel_depth=0.003)
    me.materials.append(mats_jar['gold'])
    me.shade_smooth()
    pieces['logo'] = add_obj('JarSkin4_Logo', me, 'Jar', jar_root)
    me = bent_text('JarTagline', 'PREMIUM NUT GIFT', fonts['words'], 0.085, 0.976, 0.78, extrude=0.006,
                   bevel_depth=0.0, spacing=1.25)
    me.materials.append(mats_jar['gold'])
    pieces['tag'] = add_obj('JarSkin4_Tagline', me, 'Jar', jar_root)
    me = bent_text('JarSince', '~ hand roasted ~', fonts['words'], 0.07, 0.976, 1.31, extrude=0.006,
                   bevel_depth=0.0, spacing=1.1)
    me.materials.append(mats_jar['gold'])
    pieces['since'] = add_obj('JarSkin4_Script', me, 'Jar', jar_root)

    # wire rings
    wire_mat = emission_mat('JarWire', srgb('#ffe2a6'), 2.0)
    for k, ring in enumerate(L['wireRings']):
        w = bm_to_obj(f'JarWireRing{k}', bm_torus(ring['r'], 0.013, 96, 8), wire_mat, 'Jar', jar_root,
                      loc=(0, 0, ring['z']))
        sc = ch[f'wire{k}.scale']
        for j in range(3):
            key(w, 'scale', sc, j)
    key(wire_mat.node_tree, 'nodes["EMIT"].inputs[1].default_value', ch['wire.glow'])

    # invisible collider standing in for every skin
    coll_bm = bm_cyl(1.02, 1.02, jar['H'] + 0.02, 32, 0.0)
    jc = bm_to_obj('JarCollider', coll_bm, None, 'Colliders', jar_root, smooth=False)
    jc.hide_render = True
    jc.display_type = 'WIRE'

    # ---- wipe keys: each skin appears bottom-up; the one beneath is removed under the seam
    H = jar['H']
    def keyw(mat, node, vals):
        key(mat.node_tree, f'nodes["{node}"].outputs[0].default_value', vals)
    clay, kraft, glass, final = ch['skin.clay'], ch['skin.kraft'], ch['skin.glass'], ch['skin.final']
    keyw(mats_jar['clay'], 'HI', clay); keyw(mats_jar['clay'], 'LO', [v - 0.002 for v in kraft])
    keyw(mats_jar['kraft'], 'HI', kraft); keyw(mats_jar['kraft'], 'LO', [v - 0.002 for v in glass])
    keyw(mats_jar['glass'], 'HI', glass)
    keyw(mats_jar['nuts'], 'HI', glass)
    keyw(mats_jar['glasslid'], 'HI', glass); keyw(mats_jar['glasslid'], 'LO', [v - 0.002 for v in final])
    for m in ('wood', 'label', 'gold'):
        keyw(mats_jar[m], 'HI', final)
    key(mats_jar['gold'].node_tree, 'nodes["GOLD"].inputs["Emission Strength"].default_value',
        [2.2 * v for v in ch['label.glow']])
    return pieces, count


# ------------------------------------------------------------------ studio

def softbox(name, w, h, depth, mats, collection='Stands'):
    """Softbox local frame: front diffuser faces -Z at z=0, body extends to +Z."""
    bm = bmesh.new()
    f = [bm.verts.new((sx * w / 2, sy * h / 2, 0)) for sx, sy in ((-1, -1), (1, -1), (1, 1), (-1, 1))]
    b = [bm.verts.new((sx * w * 0.18, sy * h * 0.18, depth)) for sx, sy in ((-1, -1), (1, -1), (1, 1), (-1, 1))]
    for i in range(4):
        j = (i + 1) % 4
        bm.faces.new((f[i], f[j], b[j], b[i]))
    bm.faces.new(list(reversed(b)))
    bm_cyl(w * 0.2, w * 0.2, 0.18, 24, depth, bm)  # speed ring
    shell = bm_to_obj(name + 'Shell', bm, mats['fabric'], collection, smooth=False)
    bm = bm_box((w * 0.98, h * 0.98, 0.02), (0, 0, 0.012))
    front = bm_to_obj(name + 'Diffuser', bm, None, collection, shell, smooth=False)
    m = emission_mat(name + 'DiffuserMat', srgb('#fff6ea'), 0.0)
    # a hint of fabric so it is not black when off
    nt = m.node_tree
    p = principled(nt, srgb('#6f6c68'), 0.9)
    add = nt.nodes.new('ShaderNodeAddShader')
    out = nt.nodes['Material Output']
    nt.links.new(p.outputs[0], add.inputs[0])
    nt.links.new(nt.nodes['EMIT'].outputs[0], add.inputs[1])
    nt.links.new(add.outputs[0], out.inputs['Surface'])
    front.data.materials.append(m)
    return shell, m


def light_stand(name, x, y, top, mats, collection='Stands'):
    """C-stand-ish: three splayed legs, a pole and a sandbag on one leg."""
    bm = bmesh.new()
    hub = Vector((0, 0, 0.6))
    for i in range(3):
        a = TAU * i / 3 + 0.4
        foot = Vector((1.05 * math.cos(a), 1.05 * math.sin(a), 0.04))
        bm_tube([hub, foot], 0.04, 8, bm)
    bm_cyl(0.06, 0.06, top, 16, 0.0, bm)
    bm_cyl(0.1, 0.1, 0.25, 16, 0.5, bm)
    stand = bm_to_obj(name, bm, mats['metal_black'], collection, loc=(x, y, 0), smooth=False)
    a = 0.4
    sandbag(name + 'Sandbag', (x + 0.62 * math.cos(a), y + 0.62 * math.sin(a)), a, mats)
    return stand


def sandbag(name, xy, ang, mats, z=0.0):
    bm = bmesh.new()
    for s in (-1, 1):
        bm_sphere(1.0, 16, 8, (0.32, 0.2, 0.13), (0, s * 0.19, 0.13), bm)
    bm_box((0.3, 0.12, 0.06), (0, 0, 0.27), bm)
    ob = bm_to_obj(name, bm, mats['sandbag'], 'Props', loc=(xy[0], xy[1], z), rot=(0, 0, ang))
    return ob


def build_studio(L, ch, mats, cam_targets):
    rm = L['room']
    # dark room: floor, walls, ceiling
    x0, x1 = rm['x']
    y0, y1 = rm['y']
    bm = bm_box((x1 - x0, y1 - y0, rm['z']), ((x0 + x1) / 2, (y0 + y1) / 2, rm['z'] / 2))
    bmesh.ops.reverse_faces(bm, faces=bm.faces)
    room = bm_to_obj('Room', bm, mats['room'], 'Studio', smooth=False)
    floor = bm_to_obj('Floor', bm_box((x1 - x0, y1 - y0, 0.02), ((x0 + x1) / 2, (y0 + y1) / 2, -0.01)),
                      mats['floor'], 'Studio', smooth=False)

    # white sweep: flat floor, curve, wall
    sw = L['sweep']
    prof = [(sw['front'], 0.0)]
    for i in range(1, 17):
        a = (math.pi / 2) * i / 16
        prof.append((sw['curveY'] + sw['radius'] * math.sin(a), sw['radius'] * (1 - math.cos(a))))
    prof.append((sw['curveY'] + sw['radius'], sw['top']))
    bm = bmesh.new()
    hw = sw['width'] / 2
    cols = 24
    grid = [[bm.verts.new((-hw + sw['width'] * c / cols, y, z + 0.003)) for c in range(cols + 1)] for y, z in prof]
    # subdivide the long front strip for nicer shading
    for A, B in zip(grid, grid[1:]):
        for c in range(cols):
            bm.faces.new((A[c], A[c + 1], B[c + 1], B[c]))
    sweep = bm_to_obj('Sweep', bm, mats['sweep'], 'Studio')
    if sweep.data.polygons[0].normal.z < 0:
        sweep.data.flip_normals()

    # plinth + turntable
    P, TT = L['plinth'], L['turntable']
    bm = lathe([(0, 0), (P['r'], 0), (P['r'], P['h'] - 0.05), (P['r'] - 0.04, P['h'] - 0.005), (P['r'] - 0.08, P['h']),
                (0, P['h'])], 96)
    plinth = bm_to_obj('Plinth', bm, mats['plinth'], 'Studio')
    bm = lathe([(0, 0), (TT['r'] - 0.05, 0), (TT['r'], 0.03), (TT['r'], TT['h'] - 0.03), (TT['r'] - 0.03, TT['h']),
                (0, TT['h'])], 96)
    turntable = bm_to_obj('Turntable', bm, mats['turntable'], 'Studio', loc=(0, 0, P['h']))
    bm = bmesh.new()
    for i in range(24):  # tick marks so the spin reads
        a = TAU * i / 24
        m = Matrix.Rotation(a, 4, 'Z')
        bm_box((0.22 if i % 6 else 0.36, 0.035, 0.012), ((TT['r'] - 0.2 - (0.07 if i % 6 == 0 else 0)), 0,
                                                       TT['h'] + 0.004), bm, m)
    lathe([(TT['r'] - 0.015, TT['h'] - 0.035), (TT['r'] + 0.012, TT['h'] - 0.03), (TT['r'] + 0.012, 0.03),
           (TT['r'] - 0.015, 0.035)], 96, True, bm)
    ticks = bm_to_obj('TurntableTicks', bm, mats['metal_bright'], 'Studio', turntable, smooth=False)
    key(turntable, 'rotation_euler', ch['turntable.rot'], 2)
    jar_root = empty('JarRoot', 'Jar', turntable, (0, 0, TT['h']))

    # lights on stands
    S = L['stands']
    aim = Vector((0, 0, 2.3))
    soft = {}
    for nm in ('key', 'rim'):
        s = S[nm]
        stand = light_stand(f'Stand_{nm.title()}', s['x'], s['y'], s['top'], mats)
        w, h, d = s['box']
        base = Vector((s['x'], s['y'], s['top']))
        fwd = (aim - base); fwd.z = 0; fwd.normalize()
        centre = base + fwd * (d + 0.15)
        shell, dm = softbox(f'Softbox_{nm.title()}', w, h, d, mats)
        mtx = look_matrix(centre, aim)
        shell.matrix_world = mtx
        yoke = bm_tube([base, base + fwd * 0.2], 0.05, 8)
        bm_to_obj(f'Yoke_{nm.title()}', yoke, mats['metal_black'], 'Stands', smooth=False)
        soft[nm] = (shell, dm, mtx, w, h)
    s = S['boom']
    light_stand('Stand_Boom', s['x'], s['y'], s['top'], mats)
    hang = Vector(s['hang'])
    base = Vector((s['x'], s['y'], s['top']))
    arm_dir = Vector((hang.x - base.x, hang.y - base.y, 0)).normalized()
    arm_end = Vector((hang.x, hang.y, s['top'])) + arm_dir * 0.3
    tail = base - arm_dir * 2.2
    bm = bm_tube([tail, arm_end], 0.05, 10)
    bm_tube([Vector((hang.x, hang.y, s['top'])), Vector((hang.x, hang.y, hang.z + s['box'][2] + 0.1))], 0.035, 8, bm)
    bm_cyl(0.13, 0.13, 0.25, 16, s['top'] - 0.12, bm, Matrix.Translation((base.x, base.y, 0)))
    boom = bm_to_obj('Stand_BoomArm', bm, mats['metal_black'], 'Stands', smooth=False)
    cw = bm_to_obj('Boom_Counterweight', bm_cyl(0.28, 0.28, 0.5, 20, -0.25), mats['sandbag'], 'Stands',
                   loc=tail + Vector((0, 0, -0.05)))
    w, h, d = s['box']
    shell, dm = softbox('Softbox_Top', w, h, d, mats)
    mtx = Matrix.Translation(hang)  # front faces -Z (down)
    shell.matrix_world = mtx
    soft['top'] = (shell, dm, mtx, w, h)

    # prop camera on a tripod with a red tally
    tp = L['tripod']
    tpos = Vector((tp['x'], tp['y'], 0))
    bm = bmesh.new()
    head = Vector((0, 0, tp['h']))
    for i in range(3):
        a = TAU * i / 3 + 1.1
        bm_tube([head - Vector((0, 0, 0.1)), Vector((1.0 * math.cos(a), 1.0 * math.sin(a), 0.03))], 0.045, 8, bm)
        bm_tube([Vector((0, 0, 0.9)), Vector((0.42 * math.cos(a), 0.42 * math.sin(a), 0.9 - 0.28))], 0.02, 6, bm)
    bm_cyl(0.06, 0.06, tp['h'] - 0.9, 12, 0.9, bm)
    bm_cyl(0.16, 0.13, 0.22, 16, tp['h'] - 0.1, bm)
    tripod = bm_to_obj('Tripod', bm, mats['metal_black'], 'Props', loc=tpos, smooth=False)
    to_plinth = math.atan2(-tpos.y, -tpos.x)
    cam_body = bmesh.new()
    bm_box((0.95, 0.55, 0.62), (0, 0, 0.31), cam_body)
    bm_cyl(0.2, 0.2, 0.5, 24, 0, cam_body, Matrix.Translation((0.47, 0, 0.31)) @ Matrix.Rotation(math.pi / 2, 4, 'Y'))
    bm_box((0.3, 0.22, 0.16), (-0.25, 0, 0.7), cam_body)
    cb = bm_to_obj('PropCamera', cam_body, mats['camera_body'], 'Props', loc=tpos + Vector((0, 0, tp['h'] + 0.12)),
                   rot=(0, 0, to_plinth), smooth=False)
    bevel(cb, 0.03, 2)
    lens = bm_to_obj('PropCameraLens', bm_cyl(0.17, 0.17, 0.02, 24, 0, None,
                                              Matrix.Translation((0.97, 0, 0.31)) @ Matrix.Rotation(math.pi / 2, 4, 'Y')),
                     mats['lens'], 'Props', cb)
    tally = bm_to_obj('PropCameraTally', bm_sphere(0.06, 12, 8), mats['tally'], 'Props', cb, loc=(0.32, 0, 0.66))
    tl = bpy.data.lights.new('TallyLight', 'POINT')
    tl.color = (1, 0.05, 0.03)
    tl.energy = 0.8
    tl.shadow_soft_size = 0.05
    add_obj('TallyLight', tl, 'Lights', cb, loc=(0.32, 0, 0.75))
    sandbag('Tripod_Sandbag', (tpos.x + 0.55 * math.cos(1.1), tpos.y + 0.55 * math.sin(1.1)), 1.1, mats)

    # monitor cart
    c = L['cart']
    bm = bmesh.new()
    for sx in (-1, 1):
        for sy in (-1, 1):
            bm_cyl(0.05, 0.05, c['h'] - 0.2, 8, 0.2, bm, Matrix.Translation((sx * (c['w'] / 2 - 0.08),
                                                                              sy * (c['d'] / 2 - 0.08), 0)))
            bm_sphere(0.11, 12, 6, (1, 1, 1), (sx * (c['w'] / 2 - 0.08), sy * (c['d'] / 2 - 0.08), 0.11), bm)
    for z in (0.45, 1.35, c['h'] - 0.04):
        bm_box((c['w'], c['d'], 0.06), (0, 0, z), bm)
    cart = bm_to_obj('MonitorCart', bm, mats['metal_grey'], 'Props', loc=(c['x'], c['y'], 0), smooth=False)
    to_center = math.atan2(-c['y'] - 4, -c['x'])
    bm = bmesh.new()
    bm_box((1.7, 0.1, 1.05), (0, 0, 0.75), bm)
    bm_box((0.4, 0.3, 0.05), (0, 0, 0.03), bm)
    bm_box((0.08, 0.08, 0.3), (0, 0, 0.2), bm)
    mon = bm_to_obj('Monitor', bm, mats['camera_body'], 'Props', loc=(c['x'], c['y'], c['h']),
                    rot=(0, 0, to_center + math.pi / 2), smooth=False)
    scr = bm_to_obj('MonitorScreen', bm_box((1.58, 0.01, 0.93), (0, -0.056, 0.75)), mats['screen'], 'Props', mon,
                    smooth=False)
    bm_to_obj('CartHardDrive', bm_box((0.6, 0.45, 0.25), (0.3, 0, 0.6)), mats['camera_body'], 'Props', cart,
              smooth=False)

    # apple boxes (full, half, quarter)
    a = L['appleBoxes']
    z = 0
    for i, (hgt, rot) in enumerate(((0.6, 0.1), (0.3, -0.15), (0.15, 0.3))):
        ob = bm_to_obj(f'AppleBox{i}', bm_box((1.8, 1.2, hgt), (0, 0, hgt / 2)), mats['plywood'], 'Props',
                       loc=(a['x'], a['y'], z), rot=(0, 0, rot), smooth=False)
        bevel(ob, 0.02, 2)
        z += hgt
    bm_to_obj('AppleBoxSide', bm_box((0.9, 0.6, 0.3), (0, 0, 0.15)), mats['plywood'], 'Props',
              loc=(a['x'] + 1.6, a['y'] + 0.4, 0), rot=(0, 0, 0.6), smooth=False)

    # cable coil, plus a cable running to the tripod
    cl = L['coil']
    bm = bmesh.new()
    for i in range(6):
        bm_torus(0.62 + 0.03 * math.sin(i * 1.7), 0.035, 48, 6, bm,
                 Matrix.Translation((cl['x'] + 0.03 * math.cos(i), cl['y'] + 0.03 * math.sin(i), 0.035 + i * 0.05)) @
                 Matrix.Rotation(0.04 * math.sin(i * 2.3), 4, 'X'))
    run = [Vector((cl['x'] + 0.6, cl['y'] + 0.1, 0.035))]
    for i in range(1, 9):
        u = i / 8
        run.append(Vector((lerp(cl['x'] + 0.6, tp['x'] + 0.1, u) + 0.4 * math.sin(u * 5),
                           lerp(cl['y'] + 0.1, tp['y'] - 0.2, u), 0.035)))
    bm_tube(run, 0.03, 6, bm)
    bm_to_obj('CableCoil', bm, mats['rubber'], 'Props')

    # potted plant
    pl = L['plant']
    bm = lathe([(0, 0), (0.55, 0), (0.75, 1.1), (0.82, 1.15), (0.82, 1.25), (0.7, 1.25), (0.68, 1.15), (0, 1.15)], 32)
    pot = bm_to_obj('PlantPot', bm, mats['terracotta'], 'Props', loc=(pl['x'], pl['y'], 0))
    rnd = random.Random(3)
    bm = bmesh.new()
    for i in range(16):
        a = TAU * i / 16 + rnd.random() * 0.3
        tilt = 0.35 + rnd.random() * 0.6
        ln = 1.2 + rnd.random() * 1.2
        m = (Matrix.Translation((0, 0, 1.15)) @ Matrix.Rotation(a, 4, 'Z') @ Matrix.Rotation(tilt, 4, 'Y'))
        leaf = bmesh.new()
        bmesh.ops.create_uvsphere(leaf, u_segments=10, v_segments=8, radius=1.0)
        for v in leaf.verts:
            v.co = Vector((v.co.x * 0.18, v.co.y * 0.03, (v.co.z + 1) * ln / 2))
            v.co.x += 0.15 * (v.co.z / ln) ** 2 * ln
        bmesh.ops.transform(leaf, matrix=m, verts=leaf.verts)
        tmp = bpy.data.meshes.new('tmp')
        leaf.to_mesh(tmp)
        leaf.free()
        bm.from_mesh(tmp)
        bpy.data.meshes.remove(tmp)
    bm_to_obj('PlantLeaves', bm, mats['leaf'], 'Props', loc=(pl['x'], pl['y'], 0))

    # bare work bulb on a cord
    b = L['bulb']
    bpos = Vector((b['x'], b['y'], b['z']))
    bm = lathe([(0, -0.3), (0.12, -0.28), (0.22, -0.18), (0.25, -0.05), (0.2, 0.08), (0.11, 0.18), (0.1, 0.25),
                (0, 0.25)], 32)
    bulb = bm_to_obj('WorkBulb', bm, mats['bulb'], 'Props', loc=bpos)
    bm = bm_cyl(0.12, 0.12, 0.3, 16, 0.25)
    bm_cyl(0.025, 0.025, L['room']['z'] - b['z'], 6, 0.5, bm)
    bm_to_obj('WorkBulbSocket', bm, mats['rubber'], 'Props', bulb, smooth=False)
    bl = bpy.data.lights.new('WorkBulbLight', 'POINT')
    bl.color = (1.0, 0.72, 0.42)
    bl.energy = 3200
    bl.shadow_soft_size = 0.2
    add_obj('WorkBulbLight', bl, 'Lights', loc=bpos)

    return {'soft': soft, 'jar_root': jar_root, 'turntable': turntable}


def lerp(a, b, u):
    return a + (b - a) * u


# ------------------------------------------------------------------ lights / words / sparks / cameras

def build_softbox_lights(L, ch, soft):
    power = {'key': 950, 'rim': 600, 'top': 380}
    for nm, (shell, dm, mtx, w, h) in soft.items():
        ld = bpy.data.lights.new(f'Area_{nm.title()}', 'AREA')
        ld.shape = 'RECTANGLE'
        ld.size, ld.size_y = w * 0.95, h * 0.95
        ld.color = (1.0, 0.96, 0.9)
        ld.energy = 0
        lo = add_obj(f'Area_{nm.title()}', ld, 'Lights')
        lo.matrix_world = mtx @ Matrix.Translation((0, 0, -0.05))
        vals = ch[f'light.{nm}']
        key(ld, 'energy', [power[nm] * v for v in vals])
        key(dm.node_tree, 'nodes["EMIT"].inputs[1].default_value', [3.2 * v for v in vals])
    # the conjuring glow over the turntable (shot 2) and the label's own glow
    gd = bpy.data.lights.new('JarGlow', 'POINT')
    gd.color = (1.0, 0.8, 0.5)
    gd.shadow_soft_size = 0.8
    add_obj('JarGlow', gd, 'Lights', loc=(0.0, 1.2, 5.2))
    key(gd, 'energy', [420 * v for v in ch['light.glow']])
    ld = bpy.data.lights.new('LabelGlow', 'AREA')
    ld.shape = 'DISK'
    ld.size = 1.2
    ld.color = (1.0, 0.78, 0.4)
    # sits just off the label and shines outward (-Y), onto the turntable and the hero
    add_obj('LabelGlow', ld, 'Lights', bpy.data.objects['JarRoot'], loc=(0, -1.06, 1.0), rot=(-math.pi / 2, 0, 0))
    key(ld, 'energy', [30 * v for v in ch['label.glow']])


def build_words(L, ch, mats, fonts):
    out = []
    for k, w in enumerate(L['words']):
        cu = bpy.data.curves.new(f'Word{k}', 'FONT')
        cu.body = w['text']
        if fonts['words']:
            cu.font = fonts['words']
        cu.size = w['size']
        cu.align_x = 'CENTER'
        cu.align_y = 'CENTER'
        cu.extrude = 0.05
        cu.bevel_depth = 0.012
        cu.resolution_u = 6
        ob = add_obj(f'Word{k}_{w["text"].strip(".")}', cu, 'FX', rot=(math.pi / 2, 0, L['wordYaw']))
        cu.materials.append(mats['words'])
        key(ob, 'location', ch[f'word{k}.x'], 0)
        key(ob, 'location', ch[f'word{k}.y'], 1)
        key(ob, 'location', ch[f'word{k}.z'], 2)
        for j in range(3):
            key(ob, 'scale', ch[f'word{k}.scale'], j)
        out.append(ob)
    wl = bpy.data.lights.new('WordsGlow', 'POINT')
    wl.color = (1.0, 0.78, 0.45)
    wl.shadow_soft_size = 1.0
    lo = add_obj('WordsGlow', wl, 'Lights', loc=(0, -0.6, 3.6))
    key(wl, 'energy', [60 * v for v in ch['words.light']])
    return out


def build_sound_rings(L, ch):
    for k, r in enumerate(L['soundRings']):
        m = alpha_emission_mat(f'SoundRing{k}Mat', srgb('#fff1d6'), 4.0)
        bm = bm_torus(0.32, 0.022, 64, 8)
        d = Vector(r['dir'])
        rot = d.to_track_quat('Z', 'Y').to_euler()
        ob = bm_to_obj(f'SoundRing{k}', bm, m, 'FX', rot=rot)
        for j, ax in enumerate('xyz'):
            key(ob, 'location', ch[f'sring{k}.{ax}'], j)
            key(ob, 'scale', ch[f'sring{k}.scale'], j)
        key(m.node_tree, 'nodes["ALPHA"].outputs[0].default_value', ch[f'sring{k}.alpha'])


def build_sparks(L, ch):
    m = emission_mat('SparkMat', srgb('#ffd889'), 14.0)
    src = bmesh.new()
    bmesh.ops.create_icosphere(src, subdivisions=2, radius=1.0)
    me = bpy.data.meshes.new('Spark')
    src.to_mesh(me)
    src.free()
    me.materials.append(m)
    me.shade_smooth()
    for k in range(L['nSparks']):
        ob = add_obj(f'Spark{k:02d}', me, 'FX')
        for j, ax in enumerate('xyz'):
            key(ob, 'location', ch[f'spark{k}.{ax}'], j)
            key(ob, 'scale', ch[f'spark{k}.scale'], j)


def build_cameras(L, ch):
    scene = bpy.context.scene
    cams = []
    for k, rig in enumerate(L['cameras']):
        cd = bpy.data.cameras.new(rig['name'])
        cd.lens = rig['lens']
        cd.sensor_width = 36
        cd.clip_start = 0.05
        cd.clip_end = 300
        cam = add_obj(rig['name'], cd, 'Cameras')
        tgt = empty(rig['name'] + '_Target', 'Cameras')
        for j, ax in enumerate('xyz'):
            key(cam, 'location', ch[f'cam{k}.{ax}'], j)
            key(tgt, 'location', ch[f'cam{k}.t{ax}'], j)
        con = cam.constraints.new('TRACK_TO')
        con.target = tgt
        con.track_axis = 'TRACK_NEGATIVE_Z'
        con.up_axis = 'UP_Y'
        mk = scene.timeline_markers.new(rig['name'], frame=int(round(rig['start'] * L['fps'])) + FRAME0)
        mk.camera = cam
        cams.append(cam)
    scene.camera = cams[0]
    return cams


# ------------------------------------------------------------------ main build

def make_materials():
    M = {}
    M['vinyl'] = simple_mat('HeroVinyl', srgb('#c98c52'), 0.36, coat=0.35, sss=0.06,
                            bump_args={'scale': 26, 'strength': 0.06, 'stretch': (1, 1, 0.25)})
    M['vinyl_dark'] = simple_mat('HeroVinylDark', srgb('#a86d3c'), 0.4, coat=0.3)
    M['face'] = simple_mat('HeroFaceGloss', srgb('#efc28e'), 0.12, coat=1.0, sss=0.04)
    M['eye'] = simple_mat('HeroEyeGloss', srgb('#1a0f0a'), 0.06, coat=1.0)
    M['glint'] = emission_mat('HeroGlint', (1, 1, 1, 1), 3.0)
    M['cheek'] = simple_mat('HeroCheek', srgb('#ec8f7c'), 0.5, coat=0.4)
    M['mouth_line'] = simple_mat('HeroMouthLine', srgb('#4a2414'), 0.3, coat=0.6)
    M['mouth'] = simple_mat('HeroMouth', srgb('#3a120c'), 0.4)
    M['tongue'] = simple_mat('HeroTongue', srgb('#d9616a'), 0.4)
    M['room'] = simple_mat('RoomDark', srgb('#121214'), 0.9)
    M['floor'] = simple_mat('FloorConcrete', srgb('#1d1d1f'), 0.75, bump_args={'scale': 3, 'strength': 0.1})
    M['sweep'] = simple_mat('SweepWhite', srgb('#e9e7e3'), 0.88)
    M['plinth'] = simple_mat('PlinthSatin', srgb('#f2f0ec'), 0.4)
    M['turntable'] = simple_mat('TurntableBlack', srgb('#1b1b1d'), 0.35, coat=0.3)
    M['metal_bright'] = simple_mat('BrushedSteel', srgb('#c9c9c9'), 0.25, metal=1.0)
    M['metal_black'] = simple_mat('StandBlack', srgb('#141414'), 0.45, metal=0.6)
    M['metal_grey'] = simple_mat('CartGrey', srgb('#5b5e63'), 0.4, metal=0.8)
    M['fabric'] = simple_mat('SoftboxFabric', srgb('#121212'), 0.95)
    M['sandbag'] = simple_mat('SandbagCanvas', srgb('#b4471e'), 0.9, bump_args={'scale': 90, 'strength': 0.2})
    M['camera_body'] = simple_mat('CameraBody', srgb('#232325'), 0.5)
    M['lens'] = simple_mat('LensGlass', srgb('#0c1622'), 0.04, coat=1.0)
    M['tally'] = emission_mat('TallyRed', srgb('#ff1a0e'), 12.0)
    M['screen'] = emission_mat('MonitorScreen', srgb('#3a5f8a'), 0.6)
    M['plywood'] = simple_mat('Plywood', srgb('#b58a5a'), 0.7,
                              bump_args={'scale': 6, 'strength': 0.15, 'stretch': (1, 0.1, 1), 'kind': 'wave'})
    M['rubber'] = simple_mat('CableRubber', srgb('#0d0d0d'), 0.5)
    M['terracotta'] = simple_mat('Terracotta', srgb('#a4553a'), 0.8)
    M['leaf'] = simple_mat('Leaf', srgb('#2f6b2a'), 0.45, sss=0.1)
    M['bulb'] = emission_mat('BulbGlow', srgb('#ffcf8a'), 18.0)
    M['words'] = simple_mat('WordGold', srgb('#ffcf6a'), 0.2, metal=0.4, emit=srgb('#ffb84a'), emit_strength=5.0)
    return M


def setup_render(L, ch, opts):
    sc = bpy.context.scene
    sc.unit_settings.system = 'METRIC'
    sc.unit_settings.scale_length = 0.01
    sc.unit_settings.length_unit = 'CENTIMETERS'
    sc.render.engine = 'BLENDER_EEVEE'
    sc.render.resolution_x, sc.render.resolution_y = 1920, 1080
    sc.render.resolution_percentage = opts['res']
    sc.render.fps = L['fps']
    sc.frame_start, sc.frame_end = 1, L['frames']
    sc.eevee.taa_render_samples = opts['samples']
    sc.eevee.use_shadows = True
    sc.eevee.use_raytracing = True
    sc.eevee.ray_tracing_options.resolution_scale = '2'
    sc.eevee.use_fast_gi = True
    sc.view_settings.view_transform = 'AgX'
    try:
        sc.view_settings.look = 'AgX - Medium High Contrast'
    except TypeError:
        pass
    key(sc, 'view_settings.exposure', ch['exposure'])
    w = bpy.data.worlds.new('DarkRoom')
    w.color = (0.002, 0.002, 0.0025)
    nt = w.node_tree
    bg = nt.nodes.get('Background')
    if bg:
        bg.inputs['Color'].default_value = (0.012, 0.012, 0.014, 1)
        bg.inputs['Strength'].default_value = 1.0
    sc.world = w
    sc.render.image_settings.file_format = 'PNG'
    sc.render.film_transparent = False


def build(opts):
    with open(opts['baked']) as f:
        data = json.load(f)
    L, ch = data['layout'], data['channels']
    for ob in list(bpy.data.objects):
        bpy.data.objects.remove(ob)
    mats = make_materials()
    fonts = {
        'words': font('/usr/share/fonts/opentype/inter/Inter-ExtraBold.otf',
                      '/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf'),
        'label': font('/usr/share/fonts/truetype/dejavu/DejaVuSerif-Bold.ttf',
                      '/usr/share/fonts/opentype/inter/Inter-Bold.otf'),
    }
    setup_render(L, ch, opts)
    studio = build_studio(L, ch, mats, None)
    build_softbox_lights(L, ch, studio['soft'])
    pieces, nuts = build_jar(L, ch, mats, fonts, studio['jar_root'])
    build_hero(ch, mats)
    build_words(L, ch, mats, fonts)
    build_sound_rings(L, ch)
    build_sparks(L, ch)
    build_cameras(L, ch)
    # glowing practicals and FX never block their own light
    for ob in bpy.data.objects:
        if ob.name.startswith(('WorkBulb', 'PropCameraTally', 'Spark', 'Word', 'SoundRing', 'JarWireRing')):
            ob.visible_shadow = False
    print(f'[build] scene ready: {len(bpy.data.objects)} objects, {nuts} nuts in the jar')
    return L, ch


def render(opts, L):
    sc = bpy.context.scene
    out = os.path.abspath(opts['render'])
    os.makedirs(out, exist_ok=True)
    for f in parse_frames(opts['frames'], L['frames']):
        sc.frame_set(f)
        sc.render.filepath = os.path.join(out, f'frame_{f:04d}.png')
        bpy.ops.render.render(write_still=True)
        print(f'[render] frame {f} -> {sc.render.filepath}', flush=True)


def main():
    opts = parse_args()
    L, ch = build(opts)
    if opts['save']:
        os.makedirs(os.path.dirname(os.path.abspath(opts['save'])), exist_ok=True)
        bpy.ops.wm.save_as_mainfile(filepath=os.path.abspath(opts['save']))
    ok = True
    if opts['check']:
        sys.path.insert(0, HERE)
        import checks
        ok = checks.run(L, report=os.path.join(HERE, 'build', 'checks_report.json'))
    if opts['render']:
        render(opts, L)
    if not ok:
        sys.exit(1)


if __name__ == '__main__':
    main()
