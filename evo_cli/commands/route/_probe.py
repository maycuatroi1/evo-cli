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
SNI_BLOCKED = "sni-blocked"
IP_BLOCKED = "ip-blocked"
UNREACHABLE = "unreachable"

VERDICT_TEXT = {
    OPEN: "reachable - nothing in the way",
    DNS_POISONED: "DNS is poisoned, the route itself is open",
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
    result = {"ip": ip, "sni": sni, "tcp": False, "tls": False, "ms": None, "error": None, "reset": False}
    started = time.perf_counter()
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(timeout)
    try:
        sock.connect((ip, PORT))
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


def diagnose(host, timeout=6.0, include_vn=True):
    answers = _dns.gather(host, include_vn=include_vn)
    system_ips = answers["system"] or []
    trusted = answers["trusted"]

    poisoned = bool(system_ips) and all(_dns.is_bogus(ip) for ip in system_ips)
    if not poisoned and system_ips and trusted:
        poisoned = not set(system_ips) & set(trusted) and any(_dns.is_bogus(ip) for ip in system_ips)

    candidates = trusted or [ip for ip in system_ips if not _dns.is_bogus(ip)]
    probes = rank(host, candidates, timeout=timeout) if candidates else []
    working = [row for row in probes if row["tls"]]

    control = None
    if candidates and not working:
        control = tls_probe(candidates[0], CONTROL_SNI, timeout=timeout)

    verdict = _verdict(candidates, probes, working, control, poisoned)

    return {
        "host": host,
        "verdict": verdict,
        "poisoned": poisoned,
        "system_ips": system_ips,
        "answers": answers["resolvers"],
        "candidates": candidates,
        "probes": probes,
        "working": working,
        "control": control,
        "best": working[0]["ip"] if working else None,
    }


def _verdict(candidates, probes, working, control, poisoned):
    if not candidates:
        return UNREACHABLE
    if working:
        return DNS_POISONED if poisoned else OPEN
    if control and control["tls"]:
        return SNI_BLOCKED
    if any(row["tcp"] for row in probes):
        return IP_BLOCKED
    return UNREACHABLE
