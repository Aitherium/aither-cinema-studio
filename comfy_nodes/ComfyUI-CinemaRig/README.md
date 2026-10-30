# ComfyUI-CinemaRig

Two nodes that let a ComfyUI graph be directed like a film set.

| Node | Display name | What it does |
|---|---|---|
| `CinemaCameraRig` | **Cinema Camera Rig** | camera move / lens / speed / framing -> one prompt clause string |
| `CinemaFilmGrade` | **Cinema Film Grade** | IMAGE batch -> graded IMAGE batch (colour lookup, blended by strength) |

Both are MIT. Nothing here is copied from another project.

## Install

Copy (or symlink) this folder into your ComfyUI `custom_nodes/` directory and
restart ComfyUI:

```bash
ln -s "$PWD/comfy_nodes/ComfyUI-CinemaRig" "$COMFYUI_DIR/custom_nodes/ComfyUI-CinemaRig"
```

`CinemaCameraRig` has no dependencies beyond ComfyUI itself.

## Cinema Camera Rig

Inputs: `prompt` (the subject), `move`, `lens`, `speed`, `framing`.
Output: one `STRING` - the subject first, then explicit camera clauses, the
lens look and the framing:

```
a lone astronaut walks across a rain-soaked neon street. the camera slowly
dollies in toward the subject. shot on a 35mm lens with a natural documentary
field of view. medium shot, framed from the waist up.
```

The vocabulary comes from `camera_vocab.json`, which is **byte-identical** to
`server/cinema/camera_vocab.json` in the studio backend (a test asserts it), so
a shot planned in the studio compiles to the same text here.

Moves: static, dolly_in, dolly_out, truck_left, truck_right, pan_left,
pan_right, tilt_up, tilt_down, crane_up, crane_down, orbit_left, orbit_right,
handheld, push_in_fast, whip_pan, arc, rack_focus, zoom_in, zoom_out.

Lenses: 14mm, 24mm, 35mm, 50mm, 85mm, 135mm, anamorphic.
Speeds: slow, medium, fast.
Framings: extreme_wide, wide, medium, close_up, extreme_close_up,
over_the_shoulder, low_angle, high_angle, birds_eye, dutch.

## Cinema Film Grade

Inputs: `images` (IMAGE batch, BHWC float 0-1), `look`, `strength` (0-1).

Each frame is written to a PNG, graded with the grading engine's
`image.adjustments.colorLookup` command, and blended back over the original by
`strength` (`0` leaves the frame alone, `1` is the full look). The batch comes
back on the same device.

Looks: none, warm, cool, tealOrange, bleachBypass, fadedFilm, dayForNight,
monoContrast, crossProcess.

The binary is found in this order:

1. `PHOTOCRAFT_BIN` (absolute path)
2. `photocraft-cli` on `PATH`
3. `~/.aither/photocraft/photocraft-cli` (or `.exe`) - the reference host keeps
   it here and does **not** put it on `PATH`

The look is validated before the call: the CLI silently coerces an unknown
look to `none`, which would otherwise ship an ungraded shot looking successful.

## Licence

MIT. Colour-lookup engine attribution lives in the repository `NOTICE`; the
node display names deliberately carry no third-party brand.
