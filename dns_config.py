import socket

import dns.resolver


_resolver = dns.resolver.Resolver(configure=True)
_resolver.nameservers = ["8.8.8.8", "1.1.1.1"]
_original_getaddrinfo = socket.getaddrinfo


def _getaddrinfo_with_public_dns(
    host: str,
    port: int | str,
    family: int = 0,
    type: int = 0,
    proto: int = 0,
    flags: int = 0,
) -> list[tuple[int, int, int, str, tuple[str, int]]]:
    try:
        address = _resolver.resolve(host, "A")[0].to_text()
        return _original_getaddrinfo(address, port, family, type, proto, flags)
    except Exception:
        return _original_getaddrinfo(host, port, family, type, proto, flags)


socket.getaddrinfo = _getaddrinfo_with_public_dns