"""bioscan.plugin.plan: run order from reads/provides (ties by name), the models a plan needs, and
the errors a bad plan is (cycle, missing provider, unknown stage or option). Stdlib only."""
import pytest

from bioscan import plugin
from bioscan.plugin import Manifest
from bioscan.plugins import BUILTIN


def m(name, reads=(), provides=(), models=(), options=None):
    return Manifest(name, 1, name, reads=reads, provides=provides, models=lambda o: models,
                    options=options or {})


def plan_of(want, registry, options=None):
    return plugin.plan(want, plugin.merge_options(options, registry), registry)


def test_builtin_plan_is_todays_order_and_models():
    p = plugin.plan(["jpg", "identify", "embed"], plugin.merge_options(None))
    assert p.want == ("identify", "embed", "jpg")               # report order: plugins.BUILTIN
    assert p.stages == ("embed", "identify", "jpg")             # no reads/provides between them: by name
    assert p.models == ("bioclip", "owlv2", "siglip2") and p.frame and p.detail
    off = plugin.plan(["identify"], plugin.merge_options({"identify": {"species": False}}))
    assert off.models == ("owlv2", "siglip2")
    jpg = plugin.plan(["jpg"], plugin.merge_options(None))
    assert jpg.models == () and not jpg.frame and not jpg.detail


def test_providers_run_before_readers_whatever_their_names():
    reg = [m("zeta", provides=("boxes",)), m("alpha", reads=("boxes",), provides=("tally",)),
           m("beta", reads=("tally", "image")), m("gamma")]
    p = plan_of(["beta", "alpha", "zeta", "gamma"], reg)
    assert p.stages == ("gamma", "zeta", "alpha", "beta")
    assert p.want == ("zeta", "alpha", "beta", "gamma")


def test_a_stage_providing_a_base_fact_runs_before_its_readers():
    """geotag-shaped: provides "place" (a base fact identify reads) -> runs first."""
    geotag = m("geotag", reads=("time",), provides=("place",))
    p = plugin.plan(["identify", "geotag"], plugin.merge_options(None, [*BUILTIN, geotag]), [*BUILTIN, geotag])
    assert p.stages == ("geotag", "identify")
    assert plugin.plan(["identify"], plugin.merge_options(None)).stages == ("identify",)   # place is a base fact


def test_optional_read_orders_after_its_provider_and_plans_without_one():
    reg = [m("zeta", provides=("boxes",)), m("alpha", reads=("boxes?", "image"))]
    assert plugin.plan(["alpha", "zeta"], {"alpha": {}, "zeta": {}}, reg).stages == ("zeta", "alpha")
    assert plugin.plan(["alpha"], {"alpha": {}}, reg).stages == ("alpha",)
    assert plan_of(["scene"], BUILTIN).stages == ("scene",)                                    # scene alone still plans
    assert plan_of(["scene", "identify"], BUILTIN).stages == ("identify", "scene")


def test_missing_provider_names_the_stages_that_could_provide():
    reg = [*BUILTIN, m("count", reads=("boxes",))]
    with pytest.raises(ValueError, match=r"stage count reads 'boxes', which no stage of this run provides \(add identify\)"):
        plan_of(["count"], reg)
    assert plan_of(["count", "identify"], reg).stages == ("identify", "count")
    with pytest.raises(ValueError, match=r"reads 'nothing', which no stage of this run provides$"):
        plan_of(["x"], [m("x", reads=("nothing",))])


def test_cycle_is_an_error():
    reg = [m("a", reads=("y",), provides=("x",)), m("b", reads=("x",), provides=("y",)), m("c")]
    with pytest.raises(ValueError, match=r"stages \['a', 'b'\] read each other's facts"):
        plan_of(["a", "b", "c"], reg)
    assert plan_of(["a", "c"], [m("a", reads=("x",), provides=("x",)), m("c")]).stages == ("a", "c")   # itself: fine


def test_unknown_stage_and_options():
    with pytest.raises(ValueError, match=r"unknown products: \['video'\]"):
        plugin.plan(["video"], plugin.merge_options(None))
    with pytest.raises(ValueError, match=r"unknown options.identify: \['nope'\]"):
        plugin.merge_options({"identify": {"nope": 1}})
    with pytest.raises(ValueError, match=r"unknown option groups: \['video'\]"):
        plugin.merge_options({"video": {}})
    with pytest.raises(ValueError, match="options must be an object"):
        plugin.merge_options("x")


def test_models_are_the_union_under_the_options():
    reg = [m("a", models=("siglip2", "nima")), m("b", models=("siglip2",))]
    assert plan_of(["a", "b"], reg).models == ("nima", "siglip2")


def test_item_place_falls_back_to_a_provided_fact():
    class Dec:
        lat = lon = taken_at = None

    it = plugin.Item(Dec(), {"lat": None, "lon": None, "taken_at": None})
    assert (it.lat, it.lon) == (None, None)
    it.facts["place"] = (37.4, -122.1)
    assert (it.lat, it.lon) == (37.4, -122.1)
    it.inp["lat"] = 1.0
    assert it.lat == 1.0                      # the request still wins
