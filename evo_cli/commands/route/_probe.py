from __future__ import annotations

import socket
import ssl
import time

from evo_cli.commands.route import _dns

PORT = 443

# A name nobody blocks, used as the control in the SNI experiment below.
CONTROL_SNI = "example.com"

OPEN = "open"
DNS_POISONED = "dns-poisoned"
ADDRESS_BLOCKED = "address-blocked"
SNI_BLOCKED = "sni-blocked"
IP_BLOCKED = "ip-blocked"
UNREACHABLE = "unreachable"

VERDICT_TEXT = {
    OPEN: "reachable - nothing in the way",
    DNS_POISONED: "DNS is poisoned, the route itself is open",
    ADDRESS_BLOCKED: "the address your system resolves is blocked, another one works",
    SNI_BLOCKED: "DPI blocks the TLS handshake by server name",
    IP_BLOCKED: "the address itself is blocked or dead",
    UNREACHABLE: "no address answers at all",
}


def _ms(start):
    return (time.perf_counter() - start) * 1000


def tls_probe(ip, sni, timeout=6.0):
    """Open one TLS connection to ``ip`` announcing ``sni``.

    Certificate checking is off on purpose: the question is whether the
    handshake is allowed to finish, not whether the certificate matches the
    name we announced.
    """
    return _tls_over(lambda sock: sock.connect((ip, PORT)), ip, sni, timeout)


def proxy_probe(host, proxy, timeout=6.0):
    """Like ``tls_probe``, but through the HTTP proxy at ``proxy``: CONNECT first, then the handshake.

    A raw socket never sees a bypass that lives in a proxy, so this is how to tell whether it works.
    """

    def connect(sock):
        sock.connect(proxy)
        sock.sendall(f"CONNECT {host}:{PORT} HTTP/1.1\r\nHost: {host}:{PORT}\r\n\r\n".encode())
        reply = b""
        while b"\r\n\r\n" not in reply:
            chunk = sock.recv(4096)
            if not chunk:
                break
            reply += chunk
        status = reply.split(b"\r\n", 1)[0].decode(errors="replace")
        if status.split()[1:2] != ["200"]:
            raise OSError(f"proxy answered {status or 'nothing'}")

    return _tls_over(connect, f"{proxy[0]}:{proxy[1]}", host, timeout)


def _tls_over(connect, ip, sni, timeout):
    result = {"ip": ip, "sni": sni, "tcp": False, "tls": False, "ms": None, "error": None, "reset": False}
    started = time.perf_counter()
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(timeout)
    try:
        connect(sock)
        result["tcp"] = True
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
        with context.wrap_socket(sock, server_hostname=sni):
            result["tls"] = True
            result["ms"] = _ms(started)
    except ConnectionResetError as exc:
        result["error"] = str(exc)
        result["reset"] = True
    except TimeoutError as exc:
        result["error"] = f"timeout: {exc}"
    except ssl.SSLError as exc:
        result["error"] = str(exc)
        result["reset"] = "reset" in str(exc).lower() or "ILLEGAL_MESSAGE" in str(exc)
    except OSError as exc:
        result["error"] = str(exc)
    finally:
        try:
            sock.close()
        except OSError:
            pass
    return result


def rank(host, candidates, timeout=6.0):
    rows = []
    for ip in candidates:
        row = tls_probe(ip, host, timeout=timeout)
        rows.append(row)
    rows.sort(key=lambda r: (not r["tls"], r["ms"] if r["ms"] is not None else 1e9))
    return rows


def reachable(host, timeout=6.0):
    """Whether a browser going straight to what the system resolves gets a handshake."""
    ips = [ip for ip in _dns.system_resolve(host) or [] if not _dns.is_bogus(ip)]
    return any(tls_probe(ip, host, timeout=timeout)["tls"] for ip in ips)


def candidates(answers):
    """The system's own addresses first, since those are what a browser connects to, then every trusted one."""
    system = [ip for ip in answers["system"] or [] if not _dns.is_bogus(ip)]
    return system + [ip for ip in answers["trusted"] if ip not in system]


def diagnose(host, timeout=6.0, include_vn=True):
    answers = _dns.gather(host, include_vn=include_vn)
    system_ips = answers["system"] or []
    trusted = answers["trusted"]

    # A name the system cannot resolve at all, while trusted resolvers can, is as dead to a browser as a bogus one.
    poisoned = (bool(system_ips) and all(_dns.is_bogus(ip) for ip in system_ips)) or (not system_ips and bool(trusted))
    if not poisoned and system_ips and trusted:
        poisoned = not set(system_ips) & set(trusted) and any(_dns.is_bogus(ip) for ip in system_ips)

    addresses = candidates(answers)
    probes = rank(host, addresses, timeout=timeout) if addresses else []
    working = [row for row in probes if row["tls"]]
    system_ok = any(row["ip"] in system_ips for row in working)

    control = None
    if addresses and not working:
        control = tls_probe(addresses[0], CONTROL_SNI, timeout=timeout)

    verdict = _verdict(addresses, probes, working, control, poisoned, system_ok)

    return {
        "host": host,
        "verdict": verdict,
        "poisoned": poisoned,
        "system_ips": system_ips,
        "answers": answers["resolvers"],
        "candidates": addresses,
        "probes": probes,
        "working": working,
        "control": control,
        "best": working[0]["ip"] if working else None,
    }


def _verdict(candidates, probes, working, control, poisoned, system_ok=True):
    if not candidates:
        return UNREACHABLE
    if working:
        if poisoned:
            return DNS_POISONED
        # Some address works, but not the one the system hands the browser: the name only opens once it is pinned.
        return OPEN if system_ok else ADDRESS_BLOCKED
    if control and control["tls"]:
        return SNI_BLOCKED
    if any(row["tcp"] for row in probes):
        return IP_BLOCKED
    return UNREACHABLE
