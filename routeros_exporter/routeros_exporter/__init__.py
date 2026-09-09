"""RouterOS API collector for the ISP Observability Platform.

This package fills the three gaps RouterOS SNMP cannot cover (see
``prometheus/rules/ppp-sessions.yml`` and ``prometheus/topology.yml`` for the
long-form explanation):

  1. Per-subscriber PPP/PPPoE session intelligence from ``/ppp/active`` and
     ``/ppp/secret`` - caller-id, real session uptime, assigned address,
     disconnect reason, provisioned-vs-online gap.
  2. Config backup + drift detection - periodic ``/export`` committed to a
     git repo, with a ``routeros_config_changed`` metric on every diff.
  3. Topology auto-discovery - default route / neighbour / interface walk to
     build the POP -> backhaul -> router -> sector tree that the currently
     hand-maintained ``prometheus/topology.yml`` describes.

Everything here is built against captured fixtures (``tests/fixtures/``) and
unit-tested. Assumptions that could only be confirmed against live hardware
are marked ``UNVERIFIED - needs live device`` in the code, mirroring the
``CONFIRMED (date)`` convention used elsewhere in the repo.
"""

__version__ = "0.1.0"
