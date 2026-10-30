import os
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path.insert(0, str(HERE.parent))

# Keep the module-level data dir out of the repo during tests.
os.environ.setdefault("CINEMA_DATA_DIR", str(HERE / "_data"))


@pytest.fixture()
def data_dir(tmp_path):
    return tmp_path / "data"


@pytest.fixture()
def probe():
    """A current-core ComfyUI node registry (SaveVideo + CreateVideo)."""
    return {"SaveVideo": {}, "CreateVideo": {}, "SaveAnimatedWEBP": {}, "WanImageToVideo": {}}


class FakeComfy:
    """Stands in for ComfyClient: records uploads, returns a fixed output."""

    base_url = "http://fake"
    surface = "local"

    def __init__(self, payload=b"FAKEVIDEO", outputs=None):
        self.payload = payload
        self.uploads = []
        self.graphs = []
        self.outputs = outputs or [
            {"kind": "video", "filename": "cinema_00001.mp4", "subfolder": "", "type": "output", "node": "101"}
        ]

    def object_info(self):
        return {"SaveVideo": {}, "CreateVideo": {}}

    def reachable(self):
        return True

    def version(self):
        return "0.3.fake"

    def upload_image(self, data, filename, subfolder=""):
        self.uploads.append((filename, data))
        return filename

    def run(self, graph, timeout=3600.0, interval=1.0, on_status=None):
        self.graphs.append(graph)
        if on_status:
            on_status("submitted")
            on_status("running")
        return {"prompt_id": "p1", "outputs": list(self.outputs), "entry": {}}

    def download(self, output):
        return self.payload

    def test_credentials(self):
        return {
            "ok": True,
            "surface": self.surface,
            "base_url": self.base_url,
            "key_set": False,
            "status": 200,
            "error": None,
        }


@pytest.fixture()
def fake_comfy():
    return FakeComfy()
