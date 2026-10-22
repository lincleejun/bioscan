"""The review page and the cull page's tiles: embedded data, star cuts, image URLs."""

import json
import re

from bioscan import aesthetic
from bioscan.cli import aespage
from bioscan.cli import cull as cc


def _row(path, score=0.5, **kw):
    return dict(path=path, jpg=None, score=score, stars=5, scene=None, reject_reasons=[], species=None,
                common=None, level=None, lineage=None) | kw


def _const(page, name):
    m = re.search(rf"const {name}=(.*?);const ", page)
    return json.loads(m.group(1))


def test_script_breaking_filename_stays_data(tmp_path):
    p = str(tmp_path / "</script><img src=x onerror=alert(1)>&\u2028.jpg")
    rows = [_row(p, reject_reasons=["</script>"], scene="<b>"), _row(str(tmp_path / "b.jpg"), scene="x")]
    out = tmp_path / "p.html"
    aespage.write_html(rows, [], str(out), "t")
    page = out.read_text(encoding="utf-8")
    assert page.count("</script>") == 1
    data = _const(page, "DATA")
    assert data[0]["p"] == p and data[0]["rr"] == ["</script>"]
    assert _const(page, "SCENES") == ["<b>", "x"]
    assert _const(page, "REASONS") == ["</script>"]


def test_cuts_are_the_stars_cuts(tmp_path):
    scores = [1.0, 0.9, 0.8, 0.7, 0.6, 0.5, 0.4]
    out = tmp_path / "p.html"
    aespage.write_html([_row(str(tmp_path / f"{i}.jpg"), s) for i, s in enumerate(scores)], [], str(out), "t")
    assert _const(out.read_text(encoding="utf-8"), "CUTS") == aesthetic.quintile_stars(scores)[1]


def test_figure_src_is_url_encoded(tmp_path):
    tile = cc.figure(str(tmp_path / "a#b?c%d.jpg"), None, tmp_path, "")
    assert 'src="a%23b%3Fc%25d.jpg"' in tile


def test_page_builds_image_urls_through_the_encoder(tmp_path):
    out = tmp_path / "p.html"
    aespage.write_html([_row(str(tmp_path / "a#b.jpg"))], [], str(out), "t")
    page = out.read_text(encoding="utf-8")
    assert "map(encodeURIComponent)" in page
    assert 'src="${esc(url(r.t))}"' in page and "lbi.src=url(" in page and "im.src=url(" in page
    assert not re.search(r"\.src=(?!url\(|'')", page)
