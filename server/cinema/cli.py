"""Command line: ``python -m cinema.cli render|grade|probe|export-workflows``.

The commands ride the same code as the HTTP API (``submit_shot`` + the job
store run inline), so a bug reproduced here reproduces there.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import camera, grade, graphs
from .app import (
    DEFAULT_PROBE_FILE,
    REPO_ROOT,
    CinemaState,
    RequestError,
    ShotRequest,
    build_state,
    submit_shot,
)
from .comfy import ComfyClient
from .media import now_iso


def _print(data: Any) -> None:
    print(json.dumps(data, indent=2, default=str))


def _add_rig_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--move", default="static", help="camera move (see `camera` vocab)")
    p.add_argument("--lens", default="50mm")
    p.add_argument("--speed", default="slow", choices=camera.speed_ids())
    p.add_argument("--framing", default="medium")


def _register_media(state: CinemaState, path: Optional[str]) -> Optional[str]:
    if not path:
        return None
    p = Path(path).expanduser()
    if not p.is_file():
        raise SystemExit("no such file: %s" % p)
    return state.media.add_file(p, origin="cli")["media_id"]


def cmd_render(args: argparse.Namespace) -> int:
    prompt = args.prompt or ""
    if args.prompt_file:
        prompt = Path(args.prompt_file).read_text(encoding="utf-8").strip()
    state = build_state(Path(args.data_dir) if args.data_dir else None, inline=True)
    try:
        start_id = _register_media(state, args.start)
        end_id = _register_media(state, args.end)
        source_id = _register_media(state, args.source)
        body = ShotRequest(
            mode=args.mode,
            prompt=prompt,
            negative=args.negative,
            camera={"move": args.move, "lens": args.lens, "speed": args.speed, "framing": args.framing},
            start_media_id=start_id,
            end_media_id=end_id,
            source_media_id=source_id,
            seed=args.seed,
            quality=args.quality,
            at_s=args.at_s,
            engine=args.engine,
            width=args.width,
            height=args.height,
            frames=args.frames,
            steps=args.steps,
        )
        res = submit_shot(state, body)
    except (RequestError, camera.CameraError, graphs.GraphError) as exc:
        print("error: %s" % exc, file=sys.stderr)
        return 1

    shot = state.shots.get(res["shot_id"]) or {}
    if shot.get("status") != "done":
        _print({"shot": shot, "job": state.jobs.get(res["job_id"])})
        print("error: %s" % (shot.get("error") or "render failed"), file=sys.stderr)
        return 1
    media = state.media.path(shot.get("media_id") or "")
    if media is None:
        print("error: media vanished", file=sys.stderr)
        return 1
    out = Path(args.out).expanduser() if args.out else (REPO_ROOT / "out" / (res["shot_id"] + Path(media).suffix))
    out.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(media, out)
    _print(
        {
            "shot_id": res["shot_id"],
            "job_id": res["job_id"],
            "out": str(out),
            "media_id": shot.get("media_id"),
            "compiled_prompt": shot.get("compiled_prompt"),
            "seed": shot.get("seed"),
        }
    )
    return 0


def cmd_grade(args: argparse.Namespace) -> int:
    src, dst = Path(args.input).expanduser(), Path(args.output).expanduser()
    try:
        grade.grade(src, dst, look=args.look, strength=args.strength)
    except (grade.GradeError, OSError) as exc:
        print("error: %s" % exc, file=sys.stderr)
        return 1
    except Exception as exc:  # noqa: BLE001 - ffmpeg errors carry the message
        print("error: %s" % exc, file=sys.stderr)
        return 1
    _print({"out": str(dst), "look": args.look, "strength": args.strength})
    return 0


def cmd_probe(args: argparse.Namespace) -> int:
    client = ComfyClient()
    try:
        info = client.object_info()
    except Exception as exc:  # noqa: BLE001
        print("could not reach ComfyUI at %s: %s" % (client.base_url, exc), file=sys.stderr)
        return 2
    doc = {
        "source": "live",
        "base": client.base_url,
        "generated": now_iso(),
        "object_info": info,
    }
    out = Path(args.write).expanduser() if args.write else DEFAULT_PROBE_FILE
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(doc, indent=1, sort_keys=True), encoding="utf-8")
    _print(
        {
            "nodes": len(info),
            "video_output": graphs.choose_video_output(info),
            "latent": graphs.choose_latent_node(info),
            "wrote": str(out),
            "wan": {
                "WanImageToVideo": "WanImageToVideo" in info,
                "WanFirstLastFrameToVideo": "WanFirstLastFrameToVideo" in info,
                "SaveVideo": "SaveVideo" in info,
                "CreateVideo": "CreateVideo" in info,
                "VHS_VideoCombine": "VHS_VideoCombine" in info,
                "SaveAnimatedWEBP": "SaveAnimatedWEBP" in info,
            },
        }
    )
    return 0


EXAMPLE_PROMPT = "a lone astronaut walks across a rain-soaked neon street"


def cmd_export(args: argparse.Namespace) -> int:
    outdir = Path(args.outdir).expanduser() if args.outdir else REPO_ROOT / "workflows"
    outdir.mkdir(parents=True, exist_ok=True)
    probe: Dict[str, Any] = {}
    if DEFAULT_PROBE_FILE.is_file():
        try:
            probe = json.loads(DEFAULT_PROBE_FILE.read_text(encoding="utf-8"))
        except ValueError:
            probe = {}
    rig = {"move": "dolly_in", "lens": "35mm", "speed": "slow", "framing": "medium"}
    prompt = camera.compile(rig, EXAMPLE_PROMPT)
    built = {
        "keyframe": graphs.build_shot("keyframe", prompt, quality="final", seed=0, probe=probe),
        "t2v": graphs.build_shot("t2v", prompt, quality="draft", seed=0, probe=probe),
        "i2v": graphs.build_shot("i2v", prompt, quality="draft", seed=0, image_name="example_start.png", probe=probe),
        "flf2v": graphs.build_shot(
            "flf2v", prompt, quality="draft", seed=0,
            image_name="example_start.png", end_image_name="example_end.png", probe=probe,
        ),
        "wan_camera": graphs.build_shot(
            "i2v", prompt, quality="draft", seed=0, engine="wan_camera", move="dolly_in",
            image_name="example_start.png", probe=probe,
        ),
        "ltx_t2v": graphs.build_shot("t2v", prompt, quality="draft", seed=0, engine="ltx", probe=probe),
    }
    written: List[str] = []
    for name, graph in built.items():
        problems = graphs.validate_graph(graph)
        if problems:
            print("error: %s graph is invalid: %s" % (name, problems), file=sys.stderr)
            return 1
        target = outdir / ("%s.json" % name)
        target.write_text(json.dumps(graphs.api_graph(graph), indent=2, sort_keys=True), encoding="utf-8")
        written.append(str(target))
    _print({"written": written, "video_output": graphs.choose_video_output(probe)})
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="cinema", description="Aither Cinema Studio backend CLI")
    sub = parser.add_subparsers(dest="command", required=True)

    r = sub.add_parser("render", help="render one shot through ComfyUI")
    r.add_argument("--mode", default="t2v", choices=list(graphs.MODES))
    r.add_argument("--prompt", default="")
    r.add_argument("--prompt-file")
    r.add_argument("--negative", default="")
    _add_rig_args(r)
    r.add_argument("--quality", default="draft", choices=list(graphs.PRESETS))
    r.add_argument(
        "--engine",
        default=None,
        choices=list(graphs.ENGINES),
        help="default: sdxl for keyframe, wan_camera for i2v, wan_t2v for t2v",
    )
    r.add_argument("--width", type=int, default=None, help="override the preset width")
    r.add_argument("--height", type=int, default=None, help="override the preset height")
    r.add_argument("--frames", type=int, default=None, help="override the preset frame count")
    r.add_argument("--steps", type=int, default=None, help="override the preset step count")
    r.add_argument("--seed", type=int, default=0)
    r.add_argument("--start", help="start image (i2v/flf2v)")
    r.add_argument("--end", help="end image (flf2v)")
    r.add_argument("--source", help="source video/image (reshoot)")
    r.add_argument("--at-s", type=float, default=None, help="reshoot frame time (default: last frame)")
    r.add_argument("--out", help="where to copy the result")
    r.add_argument("--data-dir", help="CINEMA_DATA_DIR override")
    r.set_defaults(func=cmd_render)

    g = sub.add_parser("grade", help="film grade a still or a video")
    g.add_argument("input")
    g.add_argument("output")
    g.add_argument("--look", default="none", choices=list(grade.LOOKS))
    g.add_argument("--strength", type=float, default=1.0)
    g.set_defaults(func=cmd_grade)

    p = sub.add_parser("probe", help="read /object_info and write docs/comfy-probe.json")
    p.add_argument("--write", help="destination (default docs/comfy-probe.json)")
    p.set_defaults(func=cmd_probe)

    e = sub.add_parser("export-workflows", help="write workflows/*.json API-format examples")
    e.add_argument("--outdir")
    e.set_defaults(func=cmd_export)
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
