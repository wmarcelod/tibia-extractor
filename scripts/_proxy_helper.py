"""Helper compartilhado: ler WEBSHARE_PROXY do env e pre-resolver o host.

Motivo: a VPS Hostinger onde o tibiadb roda nao resolve `p.webshare.io`
com o DNS padrao. Pre-resolvemos via 1.1.1.1 e reescrevemos a URL do
proxy com o IP bruto pra curl_cffi funcionar.
"""
from __future__ import annotations

import os
import socket
from urllib.parse import urlparse


def _query_dns(host: str, server: str = "1.1.1.1") -> str | None:
    """Query DNS direto pra evitar o resolver do SO."""
    try:
        q_id = os.urandom(2)
        parts = host.encode().split(b".")
        q = q_id + b"\x01\x00\x00\x01\x00\x00\x00\x00\x00\x00"
        for p in parts:
            q += bytes([len(p)]) + p
        q += b"\x00\x00\x01\x00\x01"
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(5)
        s.sendto(q, (server, 53))
        data, _ = s.recvfrom(512)
        s.close()
        # procura pelo A record (type=1): ultimos 4 bytes da primeira resposta
        if len(data) >= 4:
            return ".".join(str(b) for b in data[-4:])
    except Exception:
        return None
    return None


def resolve_host(host: str) -> str:
    if not host or host.replace(".", "").isdigit():
        return host
    try:
        return socket.gethostbyname(host)
    except OSError:
        pass
    ip = _query_dns(host)
    return ip or host


def proxies_from_env() -> dict | None:
    url = (os.environ.get("WEBSHARE_PROXY")
           or os.environ.get("HTTPS_PROXY")
           or os.environ.get("HTTP_PROXY"))
    if not url:
        return None
    u = urlparse(url)
    if u.hostname and not u.hostname.replace(".", "").isdigit():
        ip = resolve_host(u.hostname)
        if ip != u.hostname:
            userinfo = f"{u.username}:{u.password}@" if u.username else ""
            port = f":{u.port}" if u.port else ""
            url = f"{u.scheme}://{userinfo}{ip}{port}{u.path or ''}"
    return {"http": url, "https": url}
