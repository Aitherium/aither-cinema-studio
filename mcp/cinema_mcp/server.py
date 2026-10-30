"""cinema-mcp — the Aither Cinema Studio MCP server.

Ten tools, one per studio control: list the camera vocabulary, plan a shot
without spending a render, render it, re-shoot it from a new angle, grade it,
list and read shots, cut a sequence, cut a music video on a beat sheet, and
check that the whole chain is ready.

Transport is chosen on the command line: ``stdio`` (what Claude Code, Codex,
Gemini CLI and awdk spawn) or ``streamable-http`` (what a hosted or remote MCP
client connects to).  The backend URL comes from ``CINEMA_URL`` and defaults to
``http://127.0.0.1:8765``.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from typing import Any, Dict, List, Optional, Union

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

from . import tools
from .client import CinemaClient, CinemaError

READ_ONLY = ToolAnnotations(readOnlyHint=True, destructiveHint=False, openWorldHint=False)
CREATES = ToolAnnotations(
    readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=False
)

INSTRUCTIONS = """\
Aither Cinema Studio: direct shots on an open-weights film set (ComfyUI + Wan 2.2).

Use it like a camera department, not like an image generator:
1. backend_status first when anything might be wrong (ComfyUI down, jobs stuck).
2. list_camera_moves for the exact rig ids; never invent a move/lens/framing id.
3. plan_shot to read the compiled prompt — it costs nothing. Ask for a decision if
   the subject is empty or the brief is ambiguous.
4. render_shot costs GPU minutes. One shot at a time: poll get_shot until status is
   "done" or "error"; do not re-submit a running shot. Use quality="draft" to look
   at the frame, quality="final" only on a shot the human approved.
5. reshoot_shot is the note-driven fix: same scene, new camera rig (angle, move,
   lens), optionally from a specific second of the source.
6. grade_shot applies a film look to the rendered shot; it is not a re-render.
7. cut_sequence / cut_music_video assemble finished shots in screen order.

Rules that keep the shoot honest: never claim a shot is rendered before get_shot
says "done"; report media_url (an absolute link) so the human can watch it; a shot
with status "error" carries the reason — read it before retrying with the same
parameters.
"""


def build_server(client: Optional[CinemaClient] = None, host: str = "127.0.0.1",
                 port: int = 8788) -> FastMCP:
    """Wire the tools onto a FastMCP app. ``client`` is injectable for tests."""
    cinema = client or CinemaClient()
    mcp = FastMCP("cinema", instructions=INSTRUCTIONS, host=host, port=port)

    @mcp.tool(name="list_camera_moves", annotations=READ_ONLY)
    async def list_camera_moves() -> Dict[str, Any]:
        """List the camera vocabulary: moves, lenses, speeds and framings.

        Call this before composing a camera_rig. Ids are the only accepted
        values; labels are for the human. Also returns the default rig and the
        id-vs-label rule. The list comes from the same table the backend uses to
        compile the prompt, so it cannot drift.
        """
        return await tools.list_camera_moves(cinema)

    @mcp.tool(name="plan_shot", annotations=READ_ONLY)
    async def plan_shot(
        prompt: str,
        camera_rig: Optional[Dict[str, Any]] = None,
        engine: str = "t2v",
        quality: str = "draft",
    ) -> Dict[str, Any]:
        """Compile a shot plan WITHOUT rendering — the pre-visualisation step.

        Returns the filled camera rig and the exact prompt text the render will
        use (subject sentence, then move/lens/framing clauses). Costs no GPU time.

        Args:
            prompt: The subject and action in plain language, e.g. "a lone
                lighthouse keeper walks out into the storm".
            camera_rig: Object with any of move, lens, speed, framing (ids from
                list_camera_moves). Missing fields take the defaults
                static / 50mm / slow / medium.
            engine: How it would be shot: "keyframe" (a still), "t2v"
                (text-to-video), "i2v" (animate a still), "flf2v" (first and
                last frame bridge).
            quality: "draft" (fast look) or "final".
        """
        return await tools.plan_shot(cinema, prompt, camera_rig, engine, quality)

    @mcp.tool(name="render_shot", annotations=CREATES)
    async def render_shot(
        engine: str,
        camera_rig: Optional[Dict[str, Any]] = None,
        prompt: str = "",
        start_media_id: Optional[str] = None,
        end_media_id: Optional[str] = None,
        negative: Optional[str] = None,
        seed: Optional[int] = None,
        quality: str = "draft",
    ) -> Dict[str, Any]:
        """Shoot one shot: queue a render on the film set and return its ids.

        This spends GPU time. It returns {shot_id, job_id} immediately — the
        render runs in the background. Poll get_shot(shot_id) until status is
        "done" (then media_url plays it) or "error" (the shot record carries the
        reason). Never submit the same shot twice while it is queued or running.

        Args:
            engine: "keyframe" for a still frame, "t2v" for text-to-video,
                "i2v" to animate a still (needs start_media_id), "flf2v" to
                bridge two frames (needs both ids).
            camera_rig: move / lens / speed / framing ids from
                list_camera_moves. Defaults fill the gaps.
            prompt: Subject and action. Required except when animating a still
                where the frame already carries the subject.
            start_media_id: Uploaded still or existing media that seeds i2v.
            end_media_id: The closing frame for flf2v.
            negative: What must not appear in the shot.
            seed: Pin the seed to reproduce or vary a shot deliberately.
            quality: "draft" for a look pass, "final" for the take you keep.
        """
        return await tools.render_shot(
            cinema,
            engine,
            camera_rig,
            prompt,
            start_media_id,
            end_media_id,
            negative,
            seed,
            quality,
        )

    @mcp.tool(name="reshoot_shot", annotations=CREATES)
    async def reshoot_shot(
        source_media_id: str,
        camera_rig: Optional[Dict[str, Any]] = None,
        at_s: Optional[float] = None,
        prompt: str = "",
        seed: Optional[int] = None,
        quality: str = "draft",
    ) -> Dict[str, Any]:
        """Re-shoot an existing shot from a new angle — the "coverage" move.

        Takes a frame of the source (a video: the frame at at_s, default the last
        frame; an image: itself) and animates THAT frame again with a new camera
        rig. Use it to answer notes like "same scene, 85mm, close up, push in"
        without losing the scene. Spends GPU time and returns ids immediately.

        Args:
            source_media_id: media_id of the shot or upload to re-shoot.
            camera_rig: the NEW rig — this is the whole point of the tool.
            at_s: Which second of a source video to lift; omit for the last frame.
            prompt: Optional additional direction for the new take.
            seed: Optional seed for the new take.
            quality: "draft" or "final".
        """
        return await tools.reshoot_shot(
            cinema, source_media_id, camera_rig, at_s, prompt, seed, quality
        )

    @mcp.tool(name="grade_shot", annotations=CREATES)
    async def grade_shot(shot_id: str, look: str, strength: float = 1.0) -> Dict[str, Any]:
        """Apply a film grade to a rendered shot (the colour pass, not a re-render).

        Looks: none, warm, cool, tealOrange, bleachBypass, fadedFilm, dayForNight,
        monoContrast, crossProcess. strength 0..1 blends graded against original
        (0.8 reads as a natural film grade, 1.0 is the full look). The graded
        result lands on the shot as graded_media_id and is what a sequence uses.

        Args:
            shot_id: The shot to grade; it must have finished rendering.
            look: One of the look ids above.
            strength: Blend amount between 0 and 1.
        """
        return await tools.grade_shot(cinema, shot_id, look, strength)

    @mcp.tool(name="list_shots", annotations=READ_ONLY)
    async def list_shots(limit: int = 50) -> Dict[str, Any]:
        """List the shots on this film set, newest state first, with playable URLs.

        Each row carries shot_id, mode, status, progress, the prompt, the camera
        rig, and absolute media_url / graded_media_url when media exists. Use it
        to find what is already shot before asking for another take.

        Args:
            limit: Maximum rows to return (default 50).
        """
        return await tools.list_shots(cinema, limit)

    @mcp.tool(name="get_shot", annotations=READ_ONLY)
    async def get_shot(shot_id: str) -> Dict[str, Any]:
        """Read one shot: status, progress, error, compiled prompt, graph, media URL.

        Poll this after render_shot / reshoot_shot / grade_shot. status is
        "queued", "running", "done" or "error"; when "done" the media_url is an
        absolute link the human can open. On "error", report the message instead
        of re-submitting blindly.
        """
        return await tools.get_shot(cinema, shot_id)

    @mcp.tool(name="cut_sequence", annotations=CREATES)
    async def cut_sequence(shot_ids: List[str]) -> Dict[str, Any]:
        """Cut finished shots into one sequence, in the order given.

        Clips are normalised to one size and concatenated; a shot that has a
        grade contributes its graded clip. Returns a job_id — poll
        GET /api/jobs/{job_id} for the sequence media_id.

        Args:
            shot_ids: Shot ids in screen order. Every shot must have rendered.
        """
        return await tools.cut_sequence(cinema, shot_ids)

    @mcp.tool(name="cut_music_video", annotations=CREATES)
    async def cut_music_video(
        shot_ids: List[str],
        bpm: float = 120.0,
        beats_per_shot: Union[int, List[int]] = 4,
        track_duration_s: Optional[float] = None,
        audio_media_id: Optional[str] = None,
        fit: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Cut a beat-sheet music video: shots in song order, timing on the grid.

        The backend retimes each shot to beats_per_shot[i] * 60/bpm seconds
        (hard cuts, no frozen tail) and, when audio_media_id is given, muxes
        that track as AAC. Returns job_id plus the sheet the cut follows.

        Args:
            shot_ids: Shot ids in song order; the first is the opening frame.
            bpm: Tempo of the track (beats per minute).
            beats_per_shot: One beat count per shot, or a single int repeated
                for every shot (4 = one bar at 4/4).
            track_duration_s: Optional track length; when given, the result says
                whether the plan fits and by how much it overruns.
            audio_media_id: Media id from an audio upload; muxes the track into
                the cut. Without it the cut is silent.
            fit: "audio" stretches the last shot so the picture ends with the
                track rather than trimming the track to the picture.
        """
        return await tools.cut_music_video(
            cinema,
            shot_ids,
            bpm,
            beats_per_shot,
            track_duration_s,
            audio_media_id,
            fit,
        )

    @mcp.tool(name="backend_status", annotations=READ_ONLY)
    async def backend_status() -> Dict[str, Any]:
        """Check the whole chain: backend version, ComfyUI reachability, grade binary,
        shot and job counts, shots in flight.

        Call this first on a new session, before a batch of shots, or when a render
        errors. ready_to_render is false when ComfyUI is unreachable — shots would
        queue and fail, so tell the human to start ComfyUI before shooting.
        """
        return await tools.backend_status(cinema)

    return mcp


def _parse_args(argv: Optional[List[str]]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="cinema-mcp",
        description="MCP server for Aither Cinema Studio (tools over the backend HTTP API)",
    )
    parser.add_argument(
        "--transport",
        choices=("stdio", "streamable-http"),
        default=os.environ.get("CINEMA_MCP_TRANSPORT", "stdio"),
        help="stdio for a local client (default), streamable-http to serve over HTTP",
    )
    parser.add_argument(
        "--host",
        default=os.environ.get("CINEMA_MCP_HOST", "127.0.0.1"),
        help="bind host for streamable-http (default 127.0.0.1)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.environ.get("CINEMA_MCP_PORT") or 8788),
        help="bind port for streamable-http (default 8788)",
    )
    parser.add_argument(
        "--url",
        default=None,
        help="backend base URL; overrides CINEMA_URL (default http://127.0.0.1:8765)",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="print the backend status as JSON and exit (exit 1 when it is not reachable)",
    )
    return parser.parse_args(argv)


def main(argv: Optional[List[str]] = None) -> int:
    args = _parse_args(argv)
    if args.url:
        os.environ["CINEMA_URL"] = args.url
    client = CinemaClient(url=args.url)

    if args.check:
        try:
            status = asyncio.run(tools.backend_status(client))
        except CinemaError as exc:
            print(json.dumps({"ok": False, "backend": client.url, "error": str(exc)}, indent=2))
            return 1
        print(json.dumps(status, indent=2))
        return 0 if status.get("ready_to_render") else 2

    server = build_server(client, host=args.host, port=args.port)
    server.run(transport=args.transport)
    return 0


if __name__ == "__main__":  # pragma: no cover - console entry point
    sys.exit(main())
