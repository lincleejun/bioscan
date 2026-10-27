"""Split daughters' own ranges (data/names/split_ranges.csv): BirdNET geo 3.0 gives the daughters of one split the
same label, so the location prior zeroes a daughter outside the 1-degree cells it was recorded in (with a margin)."""
import numpy as np

from bioscan import naming
from bioscan.service import names
from bioscan.service.adapters import geo


class Source:
    labels = ["Tringa semipalmata_Willet"]

    def probs(self, lat, lon, week):
        return np.array([0.6])


def prior(ranges):
    return geo.LocationPrior(Source(), ["Tringa semipalmata_Willet"] * 3, ranges=ranges)


def test_a_daughter_keeps_the_shared_value_only_inside_its_range():
    prairie, saltmarsh = frozenset({(47, -101)}), frozenset({(39, -75)})       # North Dakota, New Jersey
    p = prior([prairie, saltmarsh, None])
    assert p.p_geo(47.5, -100.5, None).tolist() == [0.6, 0.0, 0.6]           # the third row has no range: unchanged
    assert p.p_geo(39.4, -74.6, None).tolist() == [0.0, 0.6, 0.6]
    assert p.p_geo(48.1, -99.2, None).tolist() == [0.6, 0.0, 0.6]           # one cell away: the margin
    assert p.p_geo(50.5, -100.5, None).tolist() == [0.0, 0.0, 0.6]          # three cells away: out


def test_no_ranges_is_the_old_prior():
    assert prior(None).p_geo(10.0, 10.0, None).tolist() == [0.6, 0.6, 0.6]


def test_ranges_are_read_by_concept_and_cut_with_the_rows(tmp_path):
    f = tmp_path / "split_ranges.csv"
    f.write_text("list_version,concept,scientific,cells,records,names,gbif_keys\n"
                 "avilist-2026,avibase:p,Tringa inornata,\"47,-101;48,-100\",12,Tringa semipalmata inornata,1\n")
    assert naming.read_ranges(f) == {"avibase:p": frozenset({(47, -101), (48, -100)})}
    assert naming.read_ranges(tmp_path / "missing.csv") == {}
    tax = [["Animalia", "Chordata", "Mammalia", "Primates", "Hominidae", g, f"{g} x"] for g in ("Homo", "Pan")]
    nl = names.NameList("m", "mammal", [t[6] for t in tax], ["", ""], tax, np.zeros((2, 4), np.float32), ["exact"] * 2,
                        concept=["mdd:1", "mdd:2"], ranges=[None, frozenset({(1, 1)})])
    assert names.wildlife(nl).ranges == [frozenset({(1, 1)})]                  # Homo's row goes, ranges stay aligned
