"""Thin wrapper around librouteros so the collectors don't import it directly
(keeps them unit-testable with a fake client) and so every API path we use is
listed in one place for the read-only API-user permission audit in the PR.

API paths used, all read-only:
  /ppp/active/print        subscriber sessions
  /ppp/secret/print        provisioned accounts
  /log/print               ppp connect/disconnect/auth-failure events
  /system/resource/print   version / board / uptime
  /system/routerboard/print
  /ip/route/print          default route + nexthops  (topology)
  /ip/neighbor/print       MNDP/LLDP neighbours       (topology)
  /interface/print         running interfaces         (topology)
  /export                  full config text           (backup) - run via a
                           command channel, not /print

A dedicated RouterOS user in the "read" group is enough for all of the above
except nothing here needs write. See routeros_exporter/README.md for the
exact `/user add` line.
"""

from __future__ import annotations

import contextlib
from typing import Any, Iterator, Protocol


class RouterOSError(RuntimeError):
    pass


class APIClient(Protocol):
    """The surface the collectors depend on. The real implementation wraps
    librouteros; tests pass a fake."""

    def query(self, path: str, **where: Any) -> list[dict[str, Any]]: ...

    def command(self, path: str, **params: Any) -> list[dict[str, Any]]: ...

    def close(self) -> None: ...


class LibRouterOSClient:
    """Real client. Constructed per poll cycle - RouterOS drops idle API
    sockets and reconnecting is cheap, so we don't hold a long-lived pool."""

    def __init__(
        self,
        host: str,
        username: str,
        password: str,
        port: int = 8728,
        use_tls: bool = False,
        timeout: float = 10.0,
    ) -> None:
        if not username or not password:
            raise RouterOSError(
                f"{host}: no API credentials - check the credentials file "
                f"(ROUTEROS_CREDENTIALS_FILE) has an entry for this router"
            )
        self._host = host
        self._kwargs: dict[str, Any] = dict(
            username=username,
            password=password,
            port=port,
            timeout=timeout,
        )
        if use_tls:
            import ssl

            ctx = ssl.create_default_context()
            # RouterOS default cert is self-signed; operators who want strict
            # verification point at their own CA via the config file. Matching
            # snmp_exporter, transport auth is community/credential-based, not
            # PKI, so this is a deliberate default not an oversight.
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
            self._kwargs["ssl_wrapper"] = ctx.wrap_socket
        self._api = None

    def _connect(self):
        if self._api is None:
            import librouteros

            try:
                self._api = librouteros.connect(host=self._host, **self._kwargs)
            except Exception as exc:  # noqa: BLE001 - surface any failure the same way
                raise RouterOSError(f"{self._host}: API connect failed: {exc}") from exc
        return self._api

    def query(self, path: str, **where: Any) -> list[dict[str, Any]]:
        api = self._connect()
        try:
            resource = api.path(*_split(path))
            if where:
                return list(resource.select().where(*_where(resource, where)))
            return list(resource)
        except Exception as exc:  # noqa: BLE001
            raise RouterOSError(f"{self._host}: query {path} failed: {exc}") from exc

    def command(self, path: str, **params: Any) -> list[dict[str, Any]]:
        api = self._connect()
        try:
            return list(api(_cmd(path), **params))
        except Exception as exc:  # noqa: BLE001
            raise RouterOSError(f"{self._host}: command {path} failed: {exc}") from exc

    def close(self) -> None:
        if self._api is not None:
            with contextlib.suppress(Exception):
                self._api.close()
            self._api = None


def _split(path: str) -> list[str]:
    """"/ppp/active/print" -> ["ppp", "active"].

    librouteros' `.path(*parts)` already issues a `print` sentence when the
    resulting resource is iterated - a literal "print" segment in the input
    path (our collectors all call e.g. "/ppp/active/print", mirroring the
    raw RouterOS API sentence) would otherwise double up into
    "/ppp/active/print/print", which RouterOS rejects with "no such
    command". CONFIRMED against a live router (2026-09-12): querying
    without stripping "print" fails; stripping it works.
    """
    parts = [p for p in path.strip("/").split("/") if p]
    if parts and parts[-1] == "print":
        parts = parts[:-1]
    return parts


def _cmd(path: str) -> str:
    return "/" + path.strip("/") + ("" if path.startswith("/") else "")


def _where(resource, where: dict[str, Any]):
    from librouteros.query import Key  # local import keeps module import light

    return [Key(k) == v for k, v in where.items()]


@contextlib.contextmanager
def connect(router) -> Iterator[APIClient]:
    """Context manager yielding a connected client for a RouterConfig."""
    cli = LibRouterOSClient(
        host=router.host,
        username=router.username,
        password=router.password,
        port=router.port,
        use_tls=router.use_tls,
    )
    try:
        yield cli
    finally:
        cli.close()
