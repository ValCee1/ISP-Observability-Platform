from routeros_exporter import topology as topo

from .conftest import load


def test_default_gateway_only_active():
    gws = topo.parse_default_gateways(load("ip_route.json"))
    assert gws == ["10.10.6.82"]  # the distance-2 inactive default is excluded


def test_neighbors_parsed():
    nbrs = topo.parse_neighbors(load("ip_neighbor.json"))
    idents = {n.identity for n in nbrs}
    assert {"sector-4B", "sector-4E", "AF60-BASE-A"} <= idents


def test_build_edges_upstream_and_neighbors():
    gws = topo.parse_default_gateways(load("ip_route.json"))
    nbrs = topo.parse_neighbors(load("ip_neighbor.json"))
    edges = topo.build_edges(
        "pop2", gws, nbrs,
        router_addresses={"pop1": ["192.168.10.1"], "pop2": ["10.10.13.254"]},
        backhaul_networks=["10.10.6.80/29"],
    )
    upstream = [e for e in edges if e.type == "upstream"]
    assert len(upstream) == 1
    assert upstream[0].dst == "backhaul:10.10.6.82"  # in the backhaul subnet

    neigh = {e.dst: e.via_interface for e in edges if e.type == "neighbor"}
    assert neigh["sector-4B"] == "ether2"


def test_build_edges_resolves_named_upstream_router():
    edges = topo.build_edges(
        "pop2", ["192.168.10.1"], [],
        router_addresses={"pop1": ["192.168.10.1"], "pop2": ["10.10.13.254"]},
    )
    assert [e.dst for e in edges if e.type == "upstream"] == ["pop1"]


def test_render_generated_yaml_shape():
    edges = {
        "pop2": [
            topo.Edge("pop2", "pop1", "upstream"),
            topo.Edge("pop2", "sector-4B", "neighbor", "ether2"),
        ]
    }
    out = topo.render_generated_yaml(edges, {"pop2": "BASE"})
    assert "_generated_by" in out
    assert "BASE" in out
    assert "pop1" in out
