from routeros_exporter.client import _split


def test_split_strips_trailing_print():
    # librouteros issues the "print" sentence itself on iteration - a
    # literal trailing "print" segment must be dropped or RouterOS sees
    # "/ppp/active/print/print" and rejects it ("no such command").
    # CONFIRMED against a live router (2026-09-12).
    assert _split("/ppp/active/print") == ["ppp", "active"]
    assert _split("/system/resource/print") == ["system", "resource"]


def test_split_leaves_non_print_paths_alone():
    assert _split("/export") == ["export"]
    assert _split("/ip/route/print") == ["ip", "route"]
    assert _split("///ppp//secret/print//") == ["ppp", "secret"]
