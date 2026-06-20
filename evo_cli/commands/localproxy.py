"""
evo localproxy — HTTPS reverse proxy mapping https://{domain}.local -> https://{domain}.

Use case: browse / test a real HTTPS site under a local alias. A request to
``https://edunext.ptudev.net.local`` is TLS-terminated locally with a leaf cert
issued by a small local CA, the ``.local`` suffix is stripped, and the request is
forwarded over TLS to the real ``https://edunext.ptudev.net``.

Rewrites that make logins / redirects stay inside the alias:
  - outbound (request):  Host, Origin, Referer   {domain}.local -> {domain}
  - inbound  (response): Location, Content-Location, Access-Control-Allow-Origin
                         {domain} -> {domain}.local ; Set-Cookie Domain= gets a
                         trailing ``.local`` so cookies bind to the alias host.

Windows-first (no /etc/hosts, lsof, or sudo) but works cross-platform.
HTTP/1.1 end to end (ALPN pins http/1.1); chunked, keep-alive, Expect:
100-continue and WebSocket upgrades are handled. Response bodies are streamed
untouched, so gzip/br pass through transparently.
"""
import datetime
import os
import re
import signal
import socket
import ssl
import threading
from pathlib import Path

import rich_click as click
from rich.table import Table
from rich.text import Text

from evo_cli.console import console, error, info, step, success, warning


# ── Certificate authority / leaf generation ──────────────────────────────────

def _cert_dir(custom=None):
    p = Path(custom) if custom else Path.home() / ".evo" / "proxy-certs"
    p.mkdir(parents=True, exist_ok=True)
    return p


def _load_or_create_ca(cert_dir):
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    key_path = cert_dir / "evo-ca-key.pem"
    crt_path = cert_dir / "evo-ca-cert.pem"
    if key_path.exists() and crt_path.exists():
        key = serialization.load_pem_private_key(key_path.read_bytes(), password=None)
        crt = x509.load_pem_x509_certificate(crt_path.read_bytes())
        return key, crt, crt_path

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([
        x509.NameAttribute(NameOID.COMMON_NAME, "Evo Local CA"),
        x509.NameAttribute(NameOID.ORGANIZATION_NAME, "Evo CLI"),
    ])
    now = datetime.datetime.now(datetime.timezone.utc)
    crt = (
        x509.CertificateBuilder()
        .subject_name(name).issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(days=1))
        .not_valid_after(now + datetime.timedelta(days=3650))
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=True, key_cert_sign=True, crl_sign=True,
                content_commitment=False, key_encipherment=False,
                data_encipherment=False, key_agreement=False,
                encipher_only=False, decipher_only=False,
            ),
            critical=True,
        )
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False)
        .sign(key, hashes.SHA256())
    )
    key_path.write_bytes(key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.TraditionalOpenSSL,
        serialization.NoEncryption(),
    ))
    crt_path.write_bytes(crt.public_bytes(serialization.Encoding.PEM))
    return key, crt, crt_path


def _make_leaf(cert_dir, ca_key, ca_crt, dns_names):
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    now = datetime.datetime.now(datetime.timezone.utc)
    crt = (
        x509.CertificateBuilder()
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, dns_names[0])]))
        .issuer_name(ca_crt.subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(days=1))
        .not_valid_after(now + datetime.timedelta(days=825))
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(x509.SubjectAlternativeName([x509.DNSName(d) for d in dns_names]), critical=False)
        .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
        .sign(ca_key, hashes.SHA256())
    )
    key_path = cert_dir / "leaf-key.pem"
    chain_path = cert_dir / "leaf-chain.pem"
    key_path.write_bytes(key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.TraditionalOpenSSL,
        serialization.NoEncryption(),
    ))
    chain_path.write_bytes(
        crt.public_bytes(serialization.Encoding.PEM)
        + ca_crt.public_bytes(serialization.Encoding.PEM)
    )
    return chain_path, key_path


def _server_ctx(chain_path, key_path):
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(certfile=str(chain_path), keyfile=str(key_path))
    try:
        ctx.set_alpn_protocols(["http/1.1"])
    except NotImplementedError:
        pass
    return ctx


# ── Hosts file + trust store helpers ─────────────────────────────────────────

_MARKER = "# evo-cli localproxy"


def _hosts_path():
    if os.name == "nt":
        root = os.environ.get("SystemRoot", r"C:\Windows")
        return Path(root) / "System32" / "drivers" / "etc" / "hosts"
    return Path("/etc/hosts")


def _flush_dns():
    if os.name == "nt":
        try:
            import subprocess
            subprocess.run(["ipconfig", "/flushdns"], capture_output=True)
        except Exception:
            pass


def _setup_hosts(local_hosts):
    path = _hosts_path()
    entries = [f"127.0.0.1 {h} {_MARKER}" for h in local_hosts]
    try:
        content = path.read_text(encoding="utf-8", errors="ignore") if path.exists() else ""
        kept = [
            ln for ln in content.splitlines()
            if _MARKER not in ln and not any(h in ln.split() for h in local_hosts)
        ]
        path.write_text("\n".join(kept + entries) + "\n", encoding="utf-8")
        success(f"Updated hosts file ({len(local_hosts)} entries)")
        _flush_dns()
        return True
    except PermissionError:
        warning("Cannot edit the hosts file (need Administrator).")
        warning("Run the terminal as Administrator, or add these lines manually:")
        for e in entries:
            console.print(f"  [cmd]{e}[/cmd]")
        return False


def _cleanup_hosts():
    path = _hosts_path()
    try:
        if not path.exists():
            return
        kept = [ln for ln in path.read_text(encoding="utf-8", errors="ignore").splitlines()
                if _MARKER not in ln]
        path.write_text("\n".join(kept) + "\n", encoding="utf-8")
        _flush_dns()
        warning("Removed hosts entries.")
    except PermissionError:
        pass


def _install_ca(ca_crt_path):
    if os.name == "nt":
        import subprocess
        info("Installing CA into the Windows user Root store...")
        r = subprocess.run(["certutil", "-addstore", "-user", "-f", "Root", str(ca_crt_path)],
                           capture_output=True, text=True)
        if r.returncode == 0:
            success("CA trusted (Chrome / Edge will accept .local certs)")
            console.print("[cmd]Firefox: set security.enterprise_roots.enabled=true, "
                          "or import the CA in its own store.[/cmd]")
        else:
            error(f"certutil failed: {r.stdout.strip()} {r.stderr.strip()}")
            console.print(f'[cmd]Install manually: certutil -addstore -user -f Root "{ca_crt_path}"[/cmd]')
    else:
        warning(f"Import this CA into your OS / browser trust store: {ca_crt_path}")


# ── HTTP/1.1 message helpers ─────────────────────────────────────────────────

def _parse_headers(raw_lines):
    """raw_lines: list of header line bytes (CRLF-terminated). -> [[name, value], ...]"""
    parsed = []
    for ln in raw_lines:
        s = ln.rstrip(b"\r\n")
        if not s:
            continue
        if s[:1] in (b" ", b"\t"):  # obsolete line folding
            if parsed:
                parsed[-1][1] += b" " + s.strip()
            continue
        name, _, val = s.partition(b":")
        parsed.append([name.strip(), val.strip()])
    return parsed


def _read_head(reader):
    """Read a request/response head. -> (start_line_bytes, parsed_headers) or None on EOF."""
    while True:
        line = reader.readline()
        if not line:
            return None
        start_line = line.rstrip(b"\r\n")
        if start_line:  # tolerate stray blank lines between messages
            break
    raw = []
    while True:
        h = reader.readline()
        if not h or h in (b"\r\n", b"\n"):
            break
        raw.append(h)
    return start_line, _parse_headers(raw)


def _serialize(start_line, parsed):
    out = [start_line.rstrip(b"\r\n"), b"\r\n"]
    for name, val in parsed:
        out += [name, b": ", val, b"\r\n"]
    out.append(b"\r\n")
    return b"".join(out)


def _hget(parsed, name):
    nl = name.lower()
    for n, v in parsed:
        if n.lower() == nl:
            return v
    return None


def _hset(parsed, name, value):
    nl = name.lower()
    for pair in parsed:
        if pair[0].lower() == nl:
            pair[1] = value
            return
    parsed.append([name, value])


def _hdel(parsed, name):
    nl = name.lower()
    parsed[:] = [p for p in parsed if p[0].lower() != nl]


def _status_code(start_line):
    parts = start_line.split(b" ", 2)
    if len(parts) >= 2 and parts[1].isdigit():
        return int(parts[1])
    return None


def _conn_close(parsed):
    v = _hget(parsed, b"Connection")
    return v is not None and b"close" in v.lower()


_COOKIE_DOMAIN_RE = re.compile(rb"(?i)(;\s*domain=)([^;]+)")


def _rewrite_cookie_domain(value):
    def repl(m):
        dom = m.group(2).strip()
        if dom.lower().endswith(b".local"):
            return m.group(0)
        return m.group(1) + dom + b".local"
    return _COOKIE_DOMAIN_RE.sub(repl, value)


def _safe_close(sock):
    try:
        sock.close()
    except OSError:
        pass


def _send_simple(sock, code, msg):
    reason = {403: b"Forbidden", 421: b"Misdirected Request",
              502: b"Bad Gateway", 404: b"Not Found"}.get(code, b"Error")
    body = msg + b"\n"
    resp = (b"HTTP/1.1 %d %s\r\n" % (code, reason)
            + b"Content-Type: text/plain; charset=utf-8\r\n"
            + b"Content-Length: %d\r\n" % len(body)
            + b"Connection: close\r\n\r\n" + body)
    try:
        sock.sendall(resp)
    except OSError:
        pass


# ── Body relaying ────────────────────────────────────────────────────────────

def _relay_n(reader, out_sock, n):
    remaining = n
    while remaining > 0:
        chunk = reader.read(min(65536, remaining))
        if not chunk:
            break
        out_sock.sendall(chunk)
        remaining -= len(chunk)


def _relay_chunked(reader, out_sock):
    while True:
        size_line = reader.readline()
        if not size_line:
            break
        out_sock.sendall(size_line)
        try:
            size = int(size_line.strip().split(b";", 1)[0], 16)
        except ValueError:
            break
        if size == 0:  # last chunk -> relay trailers up to blank line
            while True:
                t = reader.readline()
                out_sock.sendall(t)
                if not t or t in (b"\r\n", b"\n"):
                    break
            break
        _relay_n(reader, out_sock, size)
        out_sock.sendall(reader.read(2))  # trailing CRLF


def _relay_until_eof(reader, out_sock):
    while True:
        chunk = reader.read1(65536)
        if not chunk:
            break
        out_sock.sendall(chunk)


def _transfer_body(reader, out_sock, parsed, is_request, method=b"GET", status=None):
    """Relay a message body. Returns True only for a close-delimited response."""
    if not is_request:
        if method == b"HEAD" or status in (204, 304) or (status is not None and 100 <= status < 200):
            return False
    te = (_hget(parsed, b"Transfer-Encoding") or b"").lower()
    if b"chunked" in te:
        _relay_chunked(reader, out_sock)
        return False
    cl = _hget(parsed, b"Content-Length")
    if cl is not None:
        try:
            n = int(cl.strip())
        except ValueError:
            n = 0
        _relay_n(reader, out_sock, n)
        return False
    if is_request:
        return False  # no framing => no request body
    _relay_until_eof(reader, out_sock)  # response body delimited by connection close
    return True


# ── Reverse proxy ────────────────────────────────────────────────────────────

class LocalReverseProxy:
    def __init__(self, domains, listen_port=443, upstream_port=443,
                 bind="127.0.0.1", insecure_upstream=False, timeout=30):
        self.domains = domains
        self.listen_port = listen_port
        self.upstream_port = upstream_port
        self.bind = bind
        self.timeout = timeout
        self.running = True
        self.local_to_real = {f"{d}.local": d for d in domains}
        self._ssl_ctx = None
        self._server = None

        self.up_ctx = ssl.create_default_context()
        if insecure_upstream:
            self.up_ctx.check_hostname = False
            self.up_ctx.verify_mode = ssl.CERT_NONE
        try:
            self.up_ctx.set_alpn_protocols(["http/1.1"])
        except NotImplementedError:
            pass

    def _route(self, host_header):
        if not host_header:
            return None
        h = host_header.decode("latin-1").strip().lower().split(":")[0]
        return self.local_to_real.get(h)

    def _rewrite_request(self, parsed, real_host):
        _hset(parsed, b"Host", real_host.encode())
        for hdr in (b"Origin", b"Referer"):
            v = _hget(parsed, hdr)
            if v is None:
                continue
            nv = v
            for local, real in self.local_to_real.items():
                nv = nv.replace(local.encode(), real.encode())
            if nv != v:
                _hset(parsed, hdr, nv)

    def _rewrite_response(self, parsed):
        for hdr in (b"Location", b"Content-Location", b"Access-Control-Allow-Origin"):
            v = _hget(parsed, hdr)
            if v is None:
                continue
            nv = v
            for local, real in self.local_to_real.items():
                # real -> local, but skip values already ending in .local
                nv = re.sub(re.escape(real).encode() + rb"(?!\.local)",
                            lambda m, _l=local: _l.encode(), nv)
            if nv != v:
                _hset(parsed, hdr, nv)
        for pair in parsed:
            if pair[0].lower() == b"set-cookie":
                pair[1] = _rewrite_cookie_domain(pair[1])

    def _connect_upstream(self, real_host):
        try:
            s = socket.create_connection((real_host, self.upstream_port), timeout=self.timeout)
            ss = self.up_ctx.wrap_socket(s, server_hostname=real_host)
            ss.settimeout(self.timeout)
            return ss, ss.makefile("rb")
        except (OSError, ssl.SSLError) as e:
            error(f"upstream {real_host}:{self.upstream_port} failed: {e}")
            return None, None

    def _relay_upgrade(self, tls, creader, usock, ureader):
        def pipe(reader, dst):
            try:
                while True:
                    data = reader.read1(65536)
                    if not data:
                        break
                    dst.sendall(data)
            except (OSError, ssl.SSLError):
                pass
            finally:
                try:
                    dst.shutdown(socket.SHUT_WR)
                except OSError:
                    pass
        t1 = threading.Thread(target=pipe, args=(creader, usock), daemon=True)
        t2 = threading.Thread(target=pipe, args=(ureader, tls), daemon=True)
        t1.start(); t2.start()
        t1.join(); t2.join()

    def _handle(self, raw_conn, addr):
        try:
            tls = self._ssl_ctx.wrap_socket(raw_conn, server_side=True)
        except (ssl.SSLError, OSError):
            _safe_close(raw_conn)
            return
        tls.settimeout(self.timeout)
        creader = tls.makefile("rb")
        upstream = None  # [sock, reader, real_host, reusable]
        try:
            while self.running:
                head = _read_head(creader)
                if head is None:
                    break
                start_line, parsed = head
                method = start_line.split(b" ", 1)[0].upper()
                real_host = self._route(_hget(parsed, b"Host"))
                if real_host is None:
                    _send_simple(tls, 421, b"Unknown .local host for this proxy")
                    break

                if upstream is None or upstream[2] != real_host or not upstream[3]:
                    if upstream is not None:
                        _safe_close(upstream[0])
                    s, r = self._connect_upstream(real_host)
                    if s is None:
                        _send_simple(tls, 502, b"Upstream connection failed")
                        break
                    upstream = [s, r, real_host, True]
                usock, ureader = upstream[0], upstream[1]

                expect_continue = False
                ev = _hget(parsed, b"Expect")
                if ev is not None and b"100-continue" in ev.lower():
                    expect_continue = True
                    _hdel(parsed, b"Expect")

                self._rewrite_request(parsed, real_host)
                try:
                    usock.sendall(_serialize(start_line, parsed))
                    if expect_continue:
                        tls.sendall(b"HTTP/1.1 100 Continue\r\n\r\n")
                    _transfer_body(creader, usock, parsed, is_request=True, method=method)
                except (OSError, ssl.SSLError):
                    upstream[3] = False
                    _send_simple(tls, 502, b"Upstream write failed")
                    break

                # Read the response, transparently passing through 1xx interim heads.
                resp = None
                while True:
                    resp = _read_head(ureader)
                    if resp is None:
                        break
                    rstart, rparsed = resp
                    status = _status_code(rstart)
                    self._rewrite_response(rparsed)
                    tls.sendall(_serialize(rstart, rparsed))
                    if status is not None and 100 <= status < 200 and status != 101:
                        continue
                    break
                if resp is None:
                    upstream[3] = False
                    _send_simple(tls, 502, b"Upstream closed connection")
                    break

                if status == 101:  # WebSocket / protocol switch
                    self._relay_upgrade(tls, creader, usock, ureader)
                    break

                close_delimited = _transfer_body(
                    ureader, tls, rparsed, is_request=False, method=method, status=status)

                if _conn_close(rparsed) or close_delimited:
                    upstream[3] = False
                if _conn_close(parsed) or close_delimited:
                    break
        except (ssl.SSLError, OSError, ConnectionError, ValueError):
            pass
        finally:
            if upstream is not None:
                _safe_close(upstream[0])
            _safe_close(tls)

    def serve(self, ssl_ctx):
        self._ssl_ctx = ssl_ctx
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind((self.bind, self.listen_port))
        srv.listen(128)
        srv.settimeout(1)
        self._server = srv
        while self.running:
            try:
                conn, addr = srv.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            conn.settimeout(self.timeout)
            threading.Thread(target=self._handle, args=(conn, addr), daemon=True).start()
        _safe_close(srv)


# ── CLI command ──────────────────────────────────────────────────────────────

EPILOG = Text.from_markup(
    "[bold]Examples[/bold]\n\n"
    "  [cyan]evo localproxy edunext.ptudev.net --install-ca[/cyan]\n"
    "  [cyan]evo localproxy edunext.ptudev.net api.ptudev.net[/cyan]\n"
    "  [cyan]evo localproxy edunext.ptudev.net -p 8443[/cyan]   (if 443 is busy)\n"
    "  [cyan]evo localproxy edunext.ptudev.net --no-hosts[/cyan]"
)


def run_localproxy(domains, port, bind, no_hosts, cert_dir,
                   install_ca, insecure_upstream, upstream_port):
    norm = []
    for d in domains:
        d = re.sub(r"^https?://", "", d.strip().lower()).split("/")[0].split(":")[0]
        if d.endswith(".local"):
            d = d[: -len(".local")]
        if d and d not in norm:
            norm.append(d)
    if not norm:
        error("No valid domains given.")
        return
    local_hosts = [f"{d}.local" for d in norm]

    cdir = _cert_dir(cert_dir)
    ca_key, ca_crt, ca_crt_path = _load_or_create_ca(cdir)
    chain_path, key_path = _make_leaf(cdir, ca_key, ca_crt, local_hosts)
    ssl_ctx = _server_ctx(chain_path, key_path)

    if install_ca:
        _install_ca(ca_crt_path)
    if not no_hosts:
        _setup_hosts(local_hosts)

    def _url(host, p):
        return f"https://{host}" if p == 443 else f"https://{host}:{p}"

    table = Table(title="Local HTTPS Reverse Proxy", title_style="accent")
    table.add_column("Browse (local)", style="cyan")
    table.add_column("", style="white")
    table.add_column("Forwards to", style="green")
    for d in norm:
        table.add_row(_url(f"{d}.local", port), "->", _url(d, upstream_port))
    console.print(table)
    console.print(f"[cmd]CA cert: {ca_crt_path}[/cmd]")
    if not install_ca:
        console.print("[cmd]Trust the CA once so browsers don't warn:[/cmd]")
        if os.name == "nt":
            console.print(f'[cmd]  certutil -addstore -user -f Root "{ca_crt_path}"   '
                          "(or re-run with --install-ca)[/cmd]")
        else:
            console.print(f"[cmd]  import {ca_crt_path} into your trust store[/cmd]")
    info(f"Listening on {bind}:{port} — Ctrl+C to stop.")

    proxy = LocalReverseProxy(norm, listen_port=port, upstream_port=upstream_port,
                              bind=bind, insecure_upstream=insecure_upstream)

    def _shutdown(_sig, _frame):
        proxy.running = False
    try:
        signal.signal(signal.SIGINT, _shutdown)
        signal.signal(signal.SIGTERM, _shutdown)
    except (ValueError, AttributeError):
        pass

    try:
        proxy.serve(ssl_ctx)
    except OSError as exc:
        error(f"Cannot bind {bind}:{port} — {exc}")
        if port == 443:
            warning("Port 443 is likely in use. Try `-p 8443` and browse "
                    "https://{domain}.local:8443")
    except KeyboardInterrupt:
        pass
    finally:
        proxy.running = False
        if not no_hosts:
            _cleanup_hosts()
        success("Proxy stopped.")


@click.command("localproxy", epilog=EPILOG)
@click.argument("domains", nargs=-1, required=True)
@click.option("-p", "--port", type=int, default=443, show_default=True, help="Local HTTPS listen port.")
@click.option("-u", "--upstream-port", type=int, default=443, show_default=True,
              help="Upstream HTTPS port to forward to.")
@click.option("-b", "--bind", default="127.0.0.1", show_default=True, help="Bind address.")
@click.option("--no-hosts", is_flag=True, help="Don't modify the hosts file.")
@click.option("--cert-dir", type=click.Path(), default=None, help="Where to store the local CA / leaf certs.")
@click.option("--install-ca", is_flag=True, help="Install the local CA into the OS trust store.")
@click.option("--insecure-upstream", is_flag=True, help="Skip upstream TLS certificate verification.")
def localproxy(domains, port, upstream_port, bind, no_hosts, cert_dir, install_ca, insecure_upstream):
    """HTTPS reverse proxy: `https://{domain}.local` -> `https://{domain}`.

    Maps a `.local` alias of a real site to the real site over TLS so you can
    browse / test it under a local name. Host/Origin/Referer are rewritten
    outbound; Location/Set-Cookie/CORS rewritten inbound so logins and redirects
    stay inside the alias. A local CA issues the `.local` cert (trust it once
    with `--install-ca`). Editing the hosts file needs an elevated shell.
    """
    step("evo localproxy")
    run_localproxy(domains, port, bind, no_hosts, cert_dir, install_ca, insecure_upstream, upstream_port)
