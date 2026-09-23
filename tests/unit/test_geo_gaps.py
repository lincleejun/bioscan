"""`bioscan names geo-gaps`: which unlabelled species matter at a place. Review aid only; the
prior itself is unchanged (unlabelled rows keep p_geo = 0)."""
import numpy as np
import pytest

from bioscan.cli import main as cli
from bioscan.service.adapters import geo

SCI = ["Tyto furcata", "Tyto javanica", "Junco hyemalis", "Junco insularis", "Anas theodori", "Camptorhynchus labradorius"]
COMMON = ["American Barn Owl", "Eastern Barn Owl", "Dark-eyed Junco", "Guadalupe Junco", "Mascarene Teal", "Labrador Duck"]
LABELS = ["Tyto alba_Barn Owl", "", "Junco hyemalis_Dark-eyed Junco", "", "", ""]


class Source:
    """The BirdNET geo model's shape: labels and probs(lat, lon, week) aligned to them."""
    labels = ["Junco hyemalis_Dark-eyed Junco", "Tyto alba_Barn Owl", "Anas platyrhynchos_Mallard"]

    def probs(self, lat, lon, week):
        self.asked = (lat, lon, week)
        return np.array([0.6, 0.3, 0.9])


def test_gaps_only_where_the_genus_lives():
    p = geo.LocationPrior(Source(), LABELS)
    found = p.gaps(SCI, COMMON, 0, 0, None, 0.05)
    assert [g["scientific"] for g in found] == ["Junco insularis", "Tyto javanica"]      # sorted by congener p
    assert found[0] == {"scientific": "Junco insularis", "common": "Guadalupe Junco",
                        "congener": "Junco hyemalis", "congener_p_geo": 0.6}
    assert p.gaps(SCI, COMMON, 0, 0, None, 0.5)[0]["scientific"] == "Junco insularis"
    assert len(p.gaps(SCI, COMMON, 0, 0, None, 0.7)) == 0
    # the prior itself is untouched: unlabelled rows still get p_geo 0
    np.testing.assert_allclose(p.p_geo(0, 0, None), [0.3, 0, 0.6, 0, 0, 0])


def test_gaps_raises_when_the_source_fails():
    """Unlike p_geo (None: the visual result must survive), the review aid reports the failure."""
    class Broken(Source):
        def probs(self, lat, lon, week):
            raise RuntimeError("model down")

    p = geo.LocationPrior(Broken(), LABELS)
    assert p.p_geo(0, 0, None) is None
    with pytest.raises(RuntimeError):
        p.gaps(SCI, COMMON, 0, 0, None, 0.05)


def test_geo_gaps_cli(tmp_path, capsys):
    m = tmp_path / "avilist_map.csv"
    m.write_text("scientific,common,birdnet_label\n" + "".join(f"{s},{c},{lab}\n" for s, c, lab in zip(SCI, COMMON, LABELS)))
    (tmp_path / "candidates.csv").write_text("side,scientific,candidate,distance,candidate_in_avilist\n"
                                             "birdnet,Junco insularis,Junco insularus,1,False\n")
    birdnet = Source()
    a = cli.parser().parse_args(["names", "geo-gaps", "--lat", "32.9", "--lon", "-118.5", "--date", "2026-05-01",
                                 "--map", str(m)])
    assert cli.cmd_names_geo_gaps(a, birdnet) == 0
    out = capsys.readouterr().out
    assert birdnet.asked == (32.9, -118.5, 17)
    assert "2 unlabelled species" in out and "Junco insularis (Guadalupe Junco)  <- congener Junco hyemalis p_geo 0.6" in out
    assert "candidates: Junco insularus" in out and "Labrador" not in out
