"""Pure parsing of RouterOS API rows into plain dataclasses/dicts.

Collectors never touch Prometheus or the network - they take the list-of-dicts
that ``APIClient.query`` returns and return structured data. That keeps them
fully unit-testable from ``tests/fixtures/*.json`` (captured API output) and
keeps the metric wiring in one place (``routeros_exporter/metrics.py``).
"""
