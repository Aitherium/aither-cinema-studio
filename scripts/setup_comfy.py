#!/usr/bin/env python3
"""Make sure a ComfyUI is there for the studio -- the visitor's own, on their own machine.

Called by scripts/start.sh and scripts/start.ps1 before the backend starts. Progress goes to
stderr; the ONE line on stdout is the ComfyUI base URL the backend should use (empty when the
visitor chose Comfy Cloud).

Order of preference (first that applies wins):
  1. COMFY_API_KEY / COMFY_API_KEY_FILE set   -> Comfy Cloud, nothing to install.
  2. COMFY_BASE_URL set                        -> the visitor's own ComfyUI, used as-is.
  3. a ComfyUI already answers on the port     -> used as-is (http or https).
  4. otherwise install ComfyUI into $CINEMA_HOME/ComfyUI, add the CinemaRig nodes, fetch the
     models the shipped graphs load (sha256-verified, resumable), start it, wait for it.

Knobs (environment, or KEY=VALUE lines in $CINEMA_HOME/cinema.env -- the start scripts load it):
  CINEMA_COMFY       local (default) | cloud                   -- cloud skips the install
  CINEMA_MODELS     all (default) | video | keyframe | none  -- which weights to fetch
  CINEMA_TORCH       auto (default) | cuda | cpu              -- which PyTorch build
  CINEMA_COMFY_PORT  8188 (default)
  CINEMA_COMFY_ARGS  extra ComfyUI arguments, e.g. "--lowvram" or "--cpu"

Standard library only; Python 3.10+.
"""
from __future__ import annotations

import hashlib
import os
import re
import shlex
import shutil
import socket
import ssl
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SPEC = REPO / "comfy-build" / "comfy-build.yaml"
NODES = REPO / "comfy_nodes" / "ComfyUI-CinemaRig"
COMFY_REPO = "https://github.com/comfyanonymous/ComfyUI.git"
TORCH_CUDA_INDEX = "https://download.pytorch.org/whl/cu128"
TORCH_CPU_INDEX = "https://download.pytorch.org/whl/cpu"
WIN = os.name == "nt"

# which shipped graph needs which file (comfy-build/README.md, "loaded by")
VIDEO = {"diffusion_models", "text_encoders", "vae", "clip_vision"}
KEYFRAME = {"checkpoints"}


def say(msg: str) -> None:
    print(f"[comfy] {msg}", file=sys.stderr, flush=True)


def comfy_answers(port: int) -> str:
    """The base URL of a ComfyUI answering on 127.0.0.1:port, or ''."""
    for scheme in ("http", "https"):
        url = f"{scheme}://127.0.0.1:{port}"
        ctx = None
        if scheme == "https":
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE  # only asking "is it ComfyUI?", nothing is sent
        try:
            with urllib.request.urlopen(url + "/system_stats", timeout=3, context=ctx) as r:
                if r.status == 200 and b"comfyui" in r.read(4096).lower():
                    return url
        except (urllib.error.URLError, OSError, ValueError):
            continue
    return ""


def port_free(port: int) -> bool:
    with socket.socket() as s:
        return s.connect_ex(("127.0.0.1", port)) != 0


def run(cmd: list, **kw) -> None:
    say("$ " + " ".join(str(c) for c in cmd))
    subprocess.run([str(c) for c in cmd], check=True, **kw)


# ----------------------------------------------------------------------------- the spec ---

def read_spec() -> tuple[str, list[dict]]:
    """(baseComfyVersion, [{type, filename, url, sha256}]) from comfy-build.yaml.

    A line-oriented read of the one file this repo owns, so no YAML dependency."""
    text = SPEC.read_text(encoding="utf-8")
    version = re.search(r"^\s*baseComfyVersion:\s*(\S+)", text, re.M)
    models, cur = [], None
    in_models = False
    for line in text.splitlines():
        if re.match(r"^\s*models:\s*$", line):
            in_models = True
            continue
        if in_models and re.match(r"^\s{0,2}\S", line) and not line.lstrip().startswith("#"):
            break  # left the models block
        if not in_models or line.lstrip().startswith("#"):
            continue
        m = re.match(r"^\s*-\s*type:\s*(\S+)", line)
        if m:
            cur = {"type": m.group(1)}
            models.append(cur)
            continue
        m = re.match(r"^\s*(filename|sourceUri|sha256):\s*(\S+)", line)
        if m and cur is not None:
            cur[{"sourceUri": "url"}.get(m.group(1), m.group(1))] = m.group(2)
    return (version.group(1) if version else "master"), [x for x in models if "url" in x]


def wanted(models: list[dict], choice: str) -> list[dict]:
    kinds = {"all": VIDEO | KEYFRAME, "video": VIDEO, "keyframe": KEYFRAME, "none": set()}
    if choice not in kinds:
        say(f"CINEMA_MODELS={choice!r} is not one of all|video|keyframe|none; using all")
        choice = "all"
    return [m for m in models if m["type"] in kinds[choice]]


def remote_size(url: str) -> int:
    req = urllib.request.Request(url, method="HEAD")
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return int(r.headers.get("Content-Length") or 0)
    except (urllib.error.URLError, OSError, ValueError):
        return 0


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(8 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def fetch(model: dict, models_dir: Path) -> None:
    dest = models_dir / model["type"] / model["filename"]
    ok_mark = dest.with_name(dest.name + ".sha256-ok")
    if dest.exists() and ok_mark.exists() and ok_mark.read_text().strip() == model.get("sha256"):
        return
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_name(dest.name + ".part")
    if not dest.exists():
        have = part.stat().st_size if part.exists() else 0
        req = urllib.request.Request(model["url"])
        if have:
            req.add_header("Range", f"bytes={have}-")
        say(f"downloading {model['filename']}" + (f" (resuming at {have >> 20} MiB)" if have else ""))
        with urllib.request.urlopen(req, timeout=60) as r:
            if have and r.status != 206:
                have = 0  # server ignored the range: start over
            total = have + int(r.headers.get("Content-Length") or 0)
            with part.open("ab" if have else "wb") as f:
                done, last = have, time.monotonic()
                for chunk in iter(lambda: r.read(4 << 20), b""):
                    f.write(chunk)
                    done += len(chunk)
                    if time.monotonic() - last > 10:
                        pct = f"{100 * done // total}%" if total else f"{done >> 20} MiB"
                        say(f"  {model['filename']}: {pct}")
                        last = time.monotonic()
        part.replace(dest)
    want = model.get("sha256", "")
    if want:
        say(f"verifying {model['filename']}")
        got = sha256_of(dest)
        if got != want:
            dest.unlink()
            raise SystemExit(f"{model['filename']}: sha256 {got} != {want}; deleted, re-run to retry")
        ok_mark.write_text(want)


def fetch_models(models: list[dict], models_dir: Path) -> None:
    missing = [m for m in models if not (models_dir / m["type"] / m["filename"]).exists()]
    need = sum(remote_size(m["url"]) for m in missing)
    free = shutil.disk_usage(models_dir if models_dir.exists() else models_dir.parent).free
    if need and need + (5 << 30) > free:
        raise SystemExit(
            f"the models need {need / 2**30:.1f} GB and {free / 2**30:.1f} GB is free. Free some "
            "space, or set CINEMA_MODELS=keyframe (6.9 GB, stills only), or use Comfy Cloud "
            "(set COMFY_API_KEY) and run this again.")
    if need:
        say(f"fetching {len(missing)} model file(s), {need / 2**30:.1f} GB -- first run only")
    for m in models:
        fetch(m, models_dir)


# ------------------------------------------------------------------------- install/start ---

def has_nvidia() -> bool:
    exe = shutil.which("nvidia-smi")
    if not exe:
        return False
    try:
        kw = {"creationflags": 0x08000000} if WIN else {}  # CREATE_NO_WINDOW
        return subprocess.run([exe, "-L"], capture_output=True, timeout=15, **kw).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def venv_python(home: Path) -> Path:
    return home / ".venv" / ("Scripts/python.exe" if WIN else "bin/python")


def install(home: Path, version: str) -> Path:
    if not (home / "main.py").exists():
        say(f"installing ComfyUI {version} into {home}")
        run(["git", "clone", "-q", "--depth", "1", "--branch", version, COMFY_REPO, home])
    py = venv_python(home)
    marker = home / ".cinema-installed"
    if not py.exists():
        run([sys.executable, "-m", "venv", home / ".venv"])
    if not marker.exists():
        pip = [py, "-m", "pip", "install", "-q", "--disable-pip-version-check"]
        torch = os.environ.get("CINEMA_TORCH", "auto").lower()
        if torch == "auto":
            torch = "cuda" if has_nvidia() else ("mps" if sys.platform == "darwin" else "")
        if not torch:
            raise SystemExit(
                "no NVIDIA GPU found, and Wan 2.2 video on a CPU takes hours per shot. Use Comfy "
                "Cloud instead (run the command with CINEMA_COMFY=cloud), or force a CPU install "
                "with CINEMA_TORCH=cpu")
        say(f"installing PyTorch ({torch}) -- a few GB, first run only")
        if torch == "cuda":
            run(pip + ["torch", "torchvision", "torchaudio", "--index-url", TORCH_CUDA_INDEX])
        elif torch == "cpu" and sys.platform != "darwin":
            run(pip + ["torch", "torchvision", "torchaudio", "--index-url", TORCH_CPU_INDEX])
        else:
            run(pip + ["torch", "torchvision", "torchaudio"])
        run(pip + ["-r", home / "requirements.txt"])
        marker.write_text(torch)
    nodes = home / "custom_nodes" / NODES.name
    if NODES.exists():
        if nodes.exists():
            shutil.rmtree(nodes)
        shutil.copytree(NODES, nodes)  # copied each run so an update reaches ComfyUI
    return py


def start(home: Path, py: Path, port: int) -> str:
    args = ["main.py", "--listen", "127.0.0.1", "--port", str(port)]
    args += shlex.split(os.environ.get("CINEMA_COMFY_ARGS", ""))
    if venv_python(home).exists() and (home / ".cinema-installed").read_text().strip() == "cpu":
        if "--cpu" not in args:
            args.append("--cpu")
    log = (home / "comfyui.log").open("ab")
    kw: dict = {"cwd": home, "stdout": log, "stderr": subprocess.STDOUT, "stdin": subprocess.DEVNULL}
    if WIN:
        # its own process group, no console window: it outlives this script and the terminal tab
        kw["creationflags"] = 0x00000200 | 0x08000000  # NEW_PROCESS_GROUP | NO_WINDOW
    else:
        kw["start_new_session"] = True
    proc = subprocess.Popen([str(py), *args], **kw)
    (home / "comfyui.pid").write_text(str(proc.pid))
    say(f"starting ComfyUI on :{port} (log: {home / 'comfyui.log'})")
    deadline = time.monotonic() + 600
    while time.monotonic() < deadline:
        url = comfy_answers(port)
        if url:
            say(f"ComfyUI is up at {url}")
            return url
        if proc.poll() is not None:
            raise SystemExit(f"ComfyUI exited (code {proc.returncode}); see {home / 'comfyui.log'}")
        time.sleep(3)
    raise SystemExit(f"ComfyUI did not answer within 10 minutes; see {home / 'comfyui.log'}")


def main() -> int:
    if os.environ.get("COMFY_API_KEY") or os.environ.get("COMFY_API_KEY_FILE"):
        say("COMFY_API_KEY is set: rendering on Comfy Cloud, nothing to install")
        print("")
        return 0
    if os.environ.get("CINEMA_COMFY", "").lower() == "cloud":
        say("Comfy Cloud chosen: nothing to install. Paste your Comfy API key in the studio's "
            "Settings (get one at https://platform.comfy.org)")
        print("")
        return 0
    if os.environ.get("COMFY_BASE_URL"):
        say(f"using your ComfyUI at {os.environ['COMFY_BASE_URL']}")
        print(os.environ["COMFY_BASE_URL"])
        return 0
    port = int(os.environ.get("CINEMA_COMFY_PORT", "8188"))
    url = comfy_answers(port)
    if url:
        say(f"found ComfyUI already running at {url}")
        print(url)
        return 0
    if not port_free(port):
        raise SystemExit(f"port {port} is taken by something that is not ComfyUI; "
                         "set CINEMA_COMFY_PORT to another port and run again")
    if not shutil.which("git"):
        raise SystemExit("installing ComfyUI needs git: https://git-scm.com/downloads")
    home = Path(os.environ.get("CINEMA_COMFY_HOME") or REPO / "ComfyUI")
    version, models = read_spec()
    py = install(home, version)
    fetch_models(wanted(models, os.environ.get("CINEMA_MODELS", "all").lower()), home / "models")
    print(start(home, py, port))
    return 0


if __name__ == "__main__":
    if "--self-test" in sys.argv:
        v, ms = read_spec()
        assert v.startswith("v"), v
        assert len(ms) == 8 and all(m.get("sha256") and m.get("filename") for m in ms), ms
        assert {m["type"] for m in wanted(ms, "keyframe")} == {"checkpoints"}
        assert len(wanted(ms, "video")) == 7 and wanted(ms, "none") == []
        print(f"self-test ok: ComfyUI {v}, {len(ms)} models")
        sys.exit(0)
    sys.exit(main())
