from __future__ import annotations

import json as jsonlib
import socket
import struct
import urllib.error
import urllib.parse
import urllib.request

PORT = 443

PUBLIC_RESOLVERS = [
    ("8.8.8.8", "google"),
    ("1.1.1.1", "cloudflare"),
    ("9.9.9.9", "quad9"),
    ("208.67.222.222", "opendns"),
]

VN_RESOLVERS = [
    ("210.245.0.10", "fpt"),
    ("203.113.131.1", "vnpt"),
    ("183.91.160.11", "viettel"),
]

DOH_ENDPOINT = "https://dns.google/resolve"

# A resolver that answers with one of these is not answering, it is lying: an
# ISP hijack points the name at the machine itself or at the resolver's own
# address so the connection dies locally instead of visibly being blocked.
BOGUS_PREFIXES = ("127.", "0.")


def _encode_qname(host):
    out = bytearray()
    for label in host.rstrip(".").split("."):
        raw = label.encode("idna") if any(ord(c) > 127 for c in label) else label.encode()
        out.append(len(raw))
        out.extend(raw)
    out.append(0)
    return bytes(out)


def _skip_name(data, off):
    while off < len(data):
        length = data[off]
        if length == 0:
            return off + 1
        if length & 0xC0 == 0xC0:
            return off + 2
        off += 1 + length
    return off


def query_a(host, resolver, timeout=2.5):
    header = struct.pack(">HHHHHH", 0x4242, 0x0100, 1, 0, 0, 0)
    packet = header + _encode_qname(host) + struct.pack(">HH", 1, 1)
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.settimeout(timeout)
    try:
        sock.sendto(packet, (resolver, 53))
        data, _ = sock.recvfrom(4096)
    except OSError:
        return None
    finally:
        sock.close()

    if len(data) < 12:
        return None
    ancount = struct.unpack(">H", data[6:8])[0]
    off = _skip_name(data, 12) + 4
    found = []
    for _ in range(ancount):
        off = _skip_name(data, off)
        if off + 10 > len(data):
            break
        rtype, _rclass, _ttl, rdlength = struct.unpack(">HHIH", data[off : off + 10])
        off += 10
        rdata = data[off : off + rdlength]
        off += rdlength
        if rtype == 1 and rdlength == 4:
            found.append(socket.inet_ntop(socket.AF_INET, rdata))
    return found


def system_resolve(host):
    try:
        infos = socket.getaddrinfo(host, PORT, socket.AF_INET, socket.SOCK_STREAM)
    except socket.gaierror:
        return None
    seen = []
    for info in infos:
        ip = info[4][0]
        if ip not in seen:
            seen.append(ip)
    return seen


def doh_resolve(host, timeout=8.0):
    url = f"{DOH_ENDPOINT}?name={urllib.parse.quote(host)}&type=A"
    request = urllib.request.Request(url, headers={"accept": "application/dns-json", "User-Agent": "evo-cli/route"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = jsonlib.loads(response.read().decode("utf-8", "replace"))
    except (urllib.error.URLError, OSError, ValueError, TimeoutError):
        return None
    return [a["data"] for a in payload.get("Answer") or [] if a.get("type") == 1]


def is_bogus(ip):
    return any(ip.startswith(prefix) for prefix in BOGUS_PREFIXES)


def gather(host, timeout=2.5, include_vn=True):
    resolvers = list(PUBLIC_RESOLVERS)
    if include_vn:
        resolvers += VN_RESOLVERS

    answers = {}
    for address, label in resolvers:
        answers[label] = query_a(host, address, timeout)

    doh = doh_resolve(host)
    if doh is not None:
        answers["doh"] = doh

    return {
        "system": system_resolve(host),
        "resolvers": answers,
        "trusted": _trusted_union(answers),
    }


def _trusted_union(answers):
    trusted_labels = {label for _, label in PUBLIC_RESOLVERS} | {"doh"}
    out = []
    for label, ips in answers.items():
        if label not in trusted_labels or not ips:
            continue
        for ip in ips:
            if not is_bogus(ip) and ip not in out:
                out.append(ip)
    return out
