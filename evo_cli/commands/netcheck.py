import json as jsonlib
import socket
import ssl
import struct
import sys
import time
import urllib.error
import urllib.request

import rich_click as click
from rich.table import Table
from rich.text import Text

from evo_cli.console import console, error, info, step, success, warning

DEFAULT_HOSTS = ["google.com", "facebook.com", "github.com"]
PORT = 443
SPEED_BYTES = 25_000_000
SPEED_URL = "https://speed.cloudflare.com/__down?bytes={n}"

# Thresholds used to turn raw numbers into a plain-language verdict.
DNS_SLOW_MS = 100
LATENCY_HIGH_MS = 150
JITTER_HIGH_MS = 80

EPILOG = Text.from_markup(
    "[bold]Examples[/bold]\n\n"
    "  [cyan]evo netcheck[/cyan]                         check google, facebook, github\n"
    "  [cyan]evo netcheck example.com 1.1.1.1[/cyan]     check your own hosts\n"
    "  [cyan]evo netcheck --speed[/cyan]                 also run a download throughput test\n"
    "  [cyan]evo netcheck -c 10[/cyan]                    take 10 latency samples per host\n"
    "  [cyan]evo netcheck --json[/cyan]                  machine-readable output\n\n"
    "[dim]Splits every connection into DNS / TCP / TLS / TTFB so you can see which\n"
    "phase is slow, measures latency + jitter + packet loss, and flags the classic\n"
    "'broken IPv6' case where DNS hands out AAAA records the host cannot reach.[/dim]"
)


def _ms(start):
    return (time.perf_counter() - start) * 1000


def resolve(host, family):
    """Return resolved addresses for ``family`` (raises socket.gaierror on failure)."""
    return socket.getaddrinfo(host, PORT, family, socket.SOCK_STREAM)


# --- Raw AAAA lookup ---------------------------------------------------------
# socket.getaddrinfo(AF_INET6) is filtered by the OS when the host has no IPv6
# source address (Windows returns "getaddrinfo failed"), which would hide the
# very "broken IPv6" case we want to catch. So query DNS for AAAA records
# directly over UDP - independent of local IPv6 connectivity, no dependencies.
PUBLIC_RESOLVERS = ["1.1.1.1", "8.8.8.8"]


def dns_resolvers():
    """Local nameservers first (Linux/mac resolv.conf), then public fallbacks."""
    found = []
    try:
        with open("/etc/resolv.conf") as handle:
            for line in handle:
                line = line.strip()
                if line.startswith("nameserver"):
                    parts = line.split()
                    if len(parts) > 1:
                        found.append(parts[1])
    except OSError:
        pass
    for r in PUBLIC_RESOLVERS:
        if r not in found:
            found.append(r)
    return found


def _encode_qname(host):
    out = bytearray()
    for label in host.rstrip(".").split("."):
        raw = label.encode("idna") if any(ord(c) > 127 for c in label) else label.encode()
        out.append(len(raw))
        out.extend(raw)
    out.append(0)
    return bytes(out)


def _skip_name(data, off):
    """Advance past a (possibly compressed) DNS name and return the new offset."""
    while off < len(data):
        length = data[off]
        if length == 0:
            return off + 1
        if length & 0xC0 == 0xC0:  # compression pointer (2 bytes)
            return off + 2
        off += 1 + length
    return off


def dns_query_aaaa(host, resolvers, timeout=2.0):
    """Return AAAA addresses from DNS, [] if none published, or None if DNS unreachable."""
    header = struct.pack(">HHHHHH", 0xABCD, 0x0100, 1, 0, 0, 0)
    packet = header + _encode_qname(host) + struct.pack(">HH", 28, 1)  # QTYPE=AAAA, QCLASS=IN
    for resolver in resolvers:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.settimeout(timeout)
        try:
            sock.sendto(packet, (resolver, 53))
            data, _ = sock.recvfrom(4096)
        except OSError:
            continue
        finally:
            sock.close()
        ancount = struct.unpack(">H", data[6:8])[0]
        off = _skip_name(data, 12) + 4  # skip question name + qtype/qclass
        addrs = []
        for _ in range(ancount):
            off = _skip_name(data, off)
            if off + 10 > len(data):
                break
            rtype, _rclass, _ttl, rdlength = struct.unpack(">HHIH", data[off : off + 10])
            off += 10
            rdata = data[off : off + rdlength]
            off += rdlength
            if rtype == 28 and rdlength == 16:
                addrs.append(socket.inet_ntop(socket.AF_INET6, rdata))
        return addrs
    return None


def probe(host, timeout):
    """Time the DNS / TCP / TLS / TTFB phases of one HTTPS connection."""
    res = {
        "host": host,
        "ip": None,
        "dns_ms": None,
        "tcp_ms": None,
        "tls_ms": None,
        "ttfb_ms": None,
        "total_ms": None,
        "aaaa": [],
        "has_aaaa": False,
        "error": None,
    }
    started = time.perf_counter()
    try:
        t = time.perf_counter()
        infos = resolve(host, socket.AF_INET)
        res["dns_ms"] = _ms(t)
        res["ip"] = infos[0][4][0]

        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        t = time.perf_counter()
        sock.connect((res["ip"], PORT))
        res["tcp_ms"] = _ms(t)

        ctx = ssl.create_default_context()
        t = time.perf_counter()
        ssock = ctx.wrap_socket(sock, server_hostname=host)
        res["tls_ms"] = _ms(t)

        request = (
            f"HEAD / HTTP/1.1\r\nHost: {host}\r\n"
            "User-Agent: evo-cli/netcheck\r\nAccept: */*\r\nConnection: close\r\n\r\n"
        )
        t = time.perf_counter()
        ssock.sendall(request.encode())
        ssock.recv(1)
        res["ttfb_ms"] = _ms(t)
        ssock.close()
        res["total_ms"] = _ms(started)
    except (socket.gaierror, socket.timeout, OSError, ssl.SSLError) as exc:
        res["error"] = str(exc)
    return res


def latency_samples(ip, count, timeout):
    """Measure TCP-connect RTT to ``ip`` ``count`` times; return (rtts_ms, failures)."""
    rtts = []
    fails = 0
    for _ in range(count):
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        try:
            t = time.perf_counter()
            sock.connect((ip, PORT))
            rtts.append(_ms(t))
        except OSError:
            fails += 1
        finally:
            sock.close()
    return rtts, fails


def measure_host(host, count, timeout, resolvers):
    row = probe(host, timeout)
    aaaa = dns_query_aaaa(host, resolvers)
    row["aaaa"] = aaaa or []
    row["has_aaaa"] = bool(aaaa)
    row.update({"avg_ms": None, "min_ms": None, "max_ms": None, "jitter_ms": None, "loss": None})
    if row["ip"] and count > 0:
        rtts, fails = latency_samples(row["ip"], count, timeout)
        total = count
        row["loss"] = (fails / total) * 100 if total else 0.0
        if rtts:
            row["avg_ms"] = sum(rtts) / len(rtts)
            row["min_ms"] = min(rtts)
            row["max_ms"] = max(rtts)
            row["jitter_ms"] = row["max_ms"] - row["min_ms"]
    return row


def check_ipv6(rows, timeout):
    """Detect broken IPv6: AAAA records exist in DNS but the host cannot reach them."""
    for row in rows:
        addrs = row.get("aaaa") or []
        if not addrs:
            continue
        ip = addrs[0]
        sock = socket.socket(socket.AF_INET6, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        try:
            t = time.perf_counter()
            sock.connect((ip, PORT))
            return {"status": "healthy", "host": row["host"], "ip": ip, "rtt_ms": _ms(t)}
        except OSError as exc:
            return {"status": "broken", "host": row["host"], "ip": ip, "error": str(exc)}
        finally:
            sock.close()
    return {"status": "none"}


def speed_test(timeout):
    url = SPEED_URL.format(n=SPEED_BYTES)
    req = urllib.request.Request(url, headers={"User-Agent": "evo-cli/netcheck"})
    total = 0
    started = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            while True:
                chunk = resp.read(65536)
                if not chunk:
                    break
                total += len(chunk)
    except (urllib.error.URLError, socket.timeout, OSError) as exc:
        return {"error": str(exc)}
    elapsed = time.perf_counter() - started
    mbps = (total * 8) / elapsed / 1e6 if elapsed else 0.0
    return {"bytes": total, "seconds": elapsed, "mbps": mbps}


def fmt(value, suffix="", missing="-"):
    if value is None:
        return missing
    return f"{value:.0f}{suffix}"


def render_table(rows):
    table = Table(show_header=True, header_style="accent", expand=False)
    table.add_column("Host", style="info", no_wrap=True)
    table.add_column("IP", style="dim", no_wrap=True, min_width=15)
    table.add_column("DNS", justify="right")
    table.add_column("TCP", justify="right")
    table.add_column("TLS", justify="right")
    table.add_column("TTFB", justify="right")
    table.add_column("Latency avg/jit", justify="right")
    table.add_column("Loss", justify="right")
    table.add_column("v6", justify="center")

    for r in rows:
        if r["error"]:
            table.add_row(r["host"], "[error]unreachable[/error]", "-", "-", "-", "-", "-", "-", "-")
            continue
        latency = f"{fmt(r['avg_ms'])}/{fmt(r['jitter_ms'])}ms" if r["avg_ms"] is not None else "-"
        loss = r["loss"]
        loss_text = "-" if loss is None else (f"[error]{loss:.0f}%[/error]" if loss > 0 else "0%")
        v6 = "[success]yes[/success]" if r["has_aaaa"] else "[dim]no[/dim]"
        table.add_row(
            r["host"],
            r["ip"] or "-",
            fmt(r["dns_ms"], "ms"),
            fmt(r["tcp_ms"], "ms"),
            fmt(r["tls_ms"], "ms"),
            fmt(r["ttfb_ms"], "ms"),
            latency,
            loss_text,
            v6,
        )
    console.print(table)


def build_notes(rows, ipv6):
    notes = []
    for r in rows:
        if r["error"]:
            notes.append(("error", f"{r['host']}: cannot connect - {r['error']}"))
            continue
        if r["dns_ms"] and r["dns_ms"] > DNS_SLOW_MS:
            notes.append(("warning", f"{r['host']}: slow DNS ({r['dns_ms']:.0f}ms)"))
        if r["avg_ms"] and r["avg_ms"] > LATENCY_HIGH_MS:
            notes.append(("warning", f"{r['host']}: high latency ({r['avg_ms']:.0f}ms avg)"))
        if r["loss"]:
            notes.append(("warning", f"{r['host']}: {r['loss']:.0f}% packet loss"))
        if r["jitter_ms"] and r["jitter_ms"] > JITTER_HIGH_MS:
            notes.append(("warning", f"{r['host']}: high jitter ({r['jitter_ms']:.0f}ms)"))
    if ipv6["status"] == "broken":
        notes.append(
            (
                "warning",
                "broken IPv6: DNS returns AAAA records but the host can't reach IPv6 "
                f"({ipv6['host']} {ipv6['ip']}). This can cause lag on dual-stack sites; "
                "prefer IPv4 (Windows: set Tcpip6 DisabledComponents = 0x20, then reboot).",
            )
        )
    return notes


def run(hosts, count, timeout, want_speed, as_json):
    resolvers = dns_resolvers()
    rows = []
    for host in hosts:
        with console.status(f"[info]probing {host}...[/info]", spinner="dots"):
            rows.append(measure_host(host, count, timeout, resolvers))

    with console.status("[info]checking IPv6 reachability...[/info]", spinner="dots"):
        ipv6 = check_ipv6(rows, timeout)

    speed = None
    if want_speed:
        with console.status("[info]running throughput test (25MB)...[/info]", spinner="dots"):
            speed = speed_test(max(timeout, 60))

    if as_json:
        payload = {"hosts": rows, "ipv6": ipv6, "speed": speed}
        console.print_json(jsonlib.dumps(payload, ensure_ascii=False))
        return payload

    render_table(rows)

    if ipv6["status"] == "healthy":
        info(f"IPv6: reachable via [accent]{ipv6['host']}[/accent] ({fmt(ipv6['rtt_ms'], 'ms')})")
    elif ipv6["status"] == "broken":
        warning("IPv6: AAAA records resolve but cannot connect (broken / half-configured)")
    else:
        info("IPv6: no AAAA records served - IPv4-only, nothing to worry about")

    if speed is not None:
        if speed.get("error"):
            warning(f"Speed test failed: {speed['error']}")
        else:
            mb = speed["bytes"] / 1e6
            success(f"Throughput: [accent]{speed['mbps']:.0f} Mbps[/accent] ({mb:.0f}MB in {speed['seconds']:.1f}s)")

    notes = build_notes(rows, ipv6)
    step("Verdict")
    if not notes:
        success("Everything looks healthy - DNS, latency, loss and routing are all fine.")
    else:
        for level, message in notes:
            {"warning": warning, "error": error}.get(level, info)(message)

    return {"hosts": rows, "ipv6": ipv6, "speed": speed}


@click.command("netcheck", epilog=EPILOG)
@click.argument("hosts", nargs=-1)
@click.option("-c", "--count", default=4, show_default=True, help="Latency samples (TCP RTT) per host.")
@click.option("-t", "--timeout", default=5.0, show_default=True, help="Per-connection timeout (seconds).")
@click.option("-s", "--speed", "want_speed", is_flag=True, help="Also run a 25MB download throughput test.")
@click.option("--json", "as_json", is_flag=True, help="Print result as JSON.")
def netcheck(hosts, count, timeout, want_speed, as_json):
    """Diagnose why network access feels slow.

    For each `HOST` (default: google.com, facebook.com, github.com) this splits a
    real HTTPS connection into its **DNS**, **TCP**, **TLS** and **TTFB** phases so
    you can see exactly where the time goes, then takes several TCP round-trips to
    report **latency**, **jitter** and **packet loss**.

    It also flags the classic *broken IPv6* trap - where DNS hands out AAAA
    records the machine cannot actually reach, making dual-stack sites (Google,
    Facebook) lag while a plain `curl` looks fine - and, with `--speed`, measures
    real download throughput.

    Pure Python (sockets), so it needs no admin rights and works the same on
    Windows and Linux.
    """
    step("evo netcheck")
    targets = list(hosts) if hosts else DEFAULT_HOSTS
    try:
        run(targets, count, timeout, want_speed, as_json)
    except click.ClickException:
        raise
    except Exception as exc:
        error(str(exc))
        sys.exit(1)
