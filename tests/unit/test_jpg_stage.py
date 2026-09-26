"""The jpg stage's copy: the 2048 image untouched by default, the detail copy when a larger edge is
asked for, and the edge option's check."""
import pytest
from PIL import Image

from bioscan.plugins.jpg import MANIFEST
from bioscan.plugins.jpg import stage as j


def test_edge_picks_the_detail_copy_only_above_the_image(tmp_path):
    image, detail = Image.new("RGB", (2048, 1365)), Image.new("RGB", (3072, 2048))
    out = j.jpg(image, "/x/DSC1.ARW", str(tmp_path), "abcdef0123", detail=detail)
    assert out == {"path": str(tmp_path / "DSC1-abcdef01.jpg"), "width": 2048, "height": 1365}
    assert j.jpg(image, "/x/DSC1.ARW", str(tmp_path), "abcdef0123", edge=3072, detail=detail)["width"] == 3072
    assert j.jpg(image, "/x/DSC1.ARW", str(tmp_path), "abcdef0123", edge=2500, detail=detail)["width"] == 2500
    assert j.jpg(image, "/x/DSC1.ARW", str(tmp_path), "abcdef0123", edge=3072, detail=None)["width"] == 2048
    assert j.jpg(image, "/x/DSC1.ARW", str(tmp_path), "abcdef0123", edge=1024, detail=detail)["width"] == 1024
    assert Image.open(tmp_path / "DSC1-abcdef01.jpg").size == (1024, 683)


def test_edge_option_is_checked():
    MANIFEST.check({"out_dir": "/tmp/x", "edge": 2048})
    for bad in (100, "2048", True, 2048.0):
        with pytest.raises(ValueError, match="options.jpg.edge"):
            MANIFEST.check({"out_dir": "/tmp/x", "edge": bad})
