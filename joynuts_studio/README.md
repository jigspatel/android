# JoyNuts — "make it crunch."

A 17 s, 1920×1080, 60 fps product film. Everything is built in Blender from Python and rendered with Eevee:

- a 2.6 cm vinyl-toy almond mascot
- a hero-scale product photo studio
- a gift jar that builds itself

Units: 1 Blender unit = 1 cm (the scene unit scale is 0.01).

| file | role |
|---|---|
| `tracks.js` | Keyed tracks in JS: eased keys per channel, plus procedural layers for the walk cycle, sound rings and sparks. Bakes every channel per frame into `build/baked.json`. It also holds the studio layout, so geometry and motion share one source. |
| `build.py` | Builds the studio, hero, jar, lights and four cameras, then keys every frame from the bake. Cameras switch on timeline markers (hard cuts). |
| `checks.py` | Runs on every frame. Fails if the hero's body or nub arms touch or enter the plinth, turntable, jar, any stand/softbox/boom, the tripod or the cart. It also fails if a live camera comes within 0.5 cm of the hero or a prop, or if the words face away from the camera or read mirrored. |
| `render.sh` | bake → build + check → render PNGs → encode MP4 |

```bash
node tracks.js                                  # -> build/baked.json
blender -b -P build.py -- --check               # build, save build/joynuts.blend, check all 1020 frames
./render.sh                                     # full render (BLENDER=/path/to/blender if not on PATH)
./render.sh --res 25 --samples 8                # quick preview
```

`build.py` also runs under the `bpy` wheel (`pip install bpy`, Python 3.13). Use `python build.py --check`.

## Story / shots

| time | camera | action |
|---|---|---|
| 0–4 s | Behind the turntable, facing the hero | The hero toddles up, coughs twice into its nub and says *make · it · crunch.* A sound ring rolls out for each word. The words land over the turntable, turned to read correctly to this camera, then burst into 90 sparks. |
| 4–10 s | Orbiting on the far side (+Y half only, so it never crosses the hero) | The sparks spiral down and land on nine wire rings, bottom up. The jar goes solid in grey clay, then the turntable spins two turns while the jar changes skin three times: kraft → clear glass full of mixed nuts → final jar. Each skin is a bottom-up wipe with a gold seam. The final jar has a wooden lid, a cream label and gold foil that glows. The hero watches, leans in and hops. |
| 10–14 s | Front | In the dark, only the label glows. Key, rim and top softboxes switch on 0.6 s apart. The hero nods, steps aside, turns to camera and does a ta-da. |
| 14–17 s | Pull back and up over the studio | It glances back at the jar, turns to us, smiles and waves. Fade to black from 16.1 s. |

## Lighting

The room starts dark: there's a bare work bulb, a whisper of ambient, the red tally and the monitor glow. A warm glow sits over the turntable while the jar builds.

Each softbox is a modest step up: key 950 W, rim 600 W, top 380 W, in a 1 cm-unit scene. Exposure stays fixed under AgX until the fade, so nothing clips.

## Check results (current bake)

All 1020 frames pass. Minimum distances:

- Hero to plinth: 1.03 cm
- Hero to turntable: 1.31 cm
- Hero to the key stand: 1.20 cm
- Jar, rim, boom, tripod and cart: the hero never comes within 1.5 cm
- Closest camera: the shot 2 orbit, 1.64 cm from the rim softbox

The checker was tested by pushing the hero into the plinth for 10 frames. It flagged the plinth, turntable and jar on exactly those frames.

The full report is written to `build/checks_report.json`.
