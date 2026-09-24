"""OpenVPN profiles, TOTP and a persistent, unprivileged management client."""

import base64
import binascii
import hashlib
import hmac
import json
import os
import re
import selectors
import shlex
import shutil
import socket
import struct
import subprocess
import sys
import time
from collections import deque
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from evo_cli.credentials.store import CredentialError, compile_flat, read_flat, set_value

APP = Path("/Applications/OpenVPN Connect/OpenVPN Connect.app/Contents/MacOS/OpenVPN Connect")
INLINE = {"ca", "cert", "key", "tls-auth", "tls-crypt", "tls-crypt-v2", "extra-certs"}
# Only self-contained client profiles: never execute scripts/plugins or follow includes as root.
OPTIONS = set(
    """
client tls-client push-peer-info dev dev-type proto remote remote-random remote-random-hostname
nobind lport rport port persist-key persist-tun
resolv-retry connect-retry connect-retry-max connect-timeout float tun-mtu mssfix topology
auth cipher data-ciphers data-ciphers-fallback key-direction remote-cert-tls remote-cert-ku remote-cert-eku
verify-x509-name tls-version-min tls-version-max tls-cipher tls-ciphersuites reneg-sec reneg-bytes reneg-pkts
auth-user-pass static-challenge auth-nocache pull pull-filter route route-ipv6 route-gateway route-metric
redirect-gateway redirect-private route-nopull dhcp-option explicit-exit-notify keepalive ping ping-restart
ping-exit sndbuf rcvbuf verb mute allow-compression comp-lzo compress setenv
""".split()
)


def profile_name(name):
    if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_-]{0,47}", name):
        raise CredentialError("Profile names must be 1-48 letters, digits, underscores or hyphens.")
    return name


def profiles():
    from evo_cli.credentials.registry import config_path

    if not config_path().exists():
        return {}
    result = read_flat().get("openvpn", {}).get("profiles", {})
    if not isinstance(result, dict):
        raise CredentialError("Invalid openvpn.profiles in evo cred.")
    return result


def get_profile(name):
    profile_name(name)
    result = profiles().get(name)
    if not isinstance(result, dict):
        raise CredentialError(f"Unknown profile '{name}'. Run evo openvpn list.")
    return result


def save_profile(name, data):
    profile_name(name)
    previous_umask = os.umask(0o077)
    try:
        set_value(f"openvpn.profiles.{name}", data)
        compile_flat()
    finally:
        os.umask(previous_umask)


def validate_config(text):
    """Validate without exposing private key material in errors; return endpoint metadata."""
    block = None
    remotes = []
    for number, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if block:
            if line == f"</{block}>":
                block = None
            continue
        if not line or line.startswith(("#", ";")):
            continue
        if line.startswith("<") and line.endswith(">"):
            block = line[1:-1]
            if block not in INLINE:
                raise CredentialError(f"Unsupported inline block at line {number}.")
            continue
        try:
            words = shlex.split(line, comments=True)
        except ValueError:
            raise CredentialError(f"Invalid OpenVPN syntax at line {number}.") from None
        option = words[0].removeprefix("--")
        if option not in OPTIONS:
            raise CredentialError(
                f"Unsupported OpenVPN directive at line {number}; use a self-contained client profile."
            )
        if option == "setenv" and words[1:] != ["CLIENT_CERT", "0"]:
            raise CredentialError(f"Unsupported setenv at line {number}.")
        if option == "auth-user-pass" and len(words) != 1:
            raise CredentialError("auth-user-pass must not refer to a file; use evo openvpn credentials.")
        if option == "dev" and (len(words) != 2 or not re.fullmatch(r"(?:tun|utun)\d*", words[1])):
            raise CredentialError("Only TUN client profiles are supported.")
        if option == "remote":
            if len(words) not in (2, 3, 4):
                raise CredentialError(f"Invalid remote at line {number}.")
            remotes.append(" ".join(words[1:]))
    if block:
        raise CredentialError("Unclosed inline block in OpenVPN profile.")
    if not remotes:
        raise CredentialError("No remote endpoint in OpenVPN profile.")
    return remotes


def parse_totp(value):
    value = value.strip()
    settings = {"secret": value, "algorithm": "SHA1", "digits": 6, "period": 30}
    if value.startswith("otpauth:"):
        uri = urlparse(value)
        if uri.scheme != "otpauth" or uri.netloc != "totp":
            raise CredentialError("Only otpauth://totp enrollment is supported.")
        query = parse_qs(uri.query)
        if any(len(v) != 1 for v in query.values()):
            raise CredentialError("Ambiguous OTP enrollment parameters.")
        settings["secret"] = query.get("secret", [""])[0]
        settings["algorithm"] = query.get("algorithm", ["SHA1"])[0].upper()
        try:
            settings["digits"] = int(query.get("digits", ["6"])[0])
            settings["period"] = int(query.get("period", ["30"])[0])
        except ValueError:
            raise CredentialError("Invalid OTP digits or period.") from None
    settings["secret"] = settings["secret"].upper().replace(" ", "").rstrip("=")
    totp(settings, at=0)
    return settings


def totp(settings, at=None):
    algorithm = settings.get("algorithm", "SHA1")
    digits, period = settings.get("digits", 6), settings.get("period", 30)
    if algorithm not in ("SHA1", "SHA256", "SHA512") or digits not in (6, 8):
        raise CredentialError("OTP requires SHA1/SHA256/SHA512 and 6 or 8 digits.")
    if not isinstance(period, int) or not 1 <= period <= 300:
        raise CredentialError("OTP period must be 1-300 seconds.")
    secret = settings.get("secret", "")
    try:
        key = base64.b32decode(secret + "=" * (-len(secret) % 8), casefold=True)
    except (ValueError, binascii.Error):
        raise CredentialError("Invalid base32 OTP secret.") from None
    if not key:
        raise CredentialError("OTP secret is empty.")
    counter = int(time.time() if at is None else at) // period
    digest = hmac.new(key, struct.pack(">Q", counter), algorithm.lower()).digest()
    offset = digest[-1] & 15
    number = struct.unpack(">I", digest[offset : offset + 4])[0] & 0x7FFFFFFF
    return str(number % (10**digits)).zfill(digits)


def qr_totp(path):
    decoder = shutil.which("zbarimg")
    if not decoder:
        raise CredentialError("QR decoding needs zbarimg (brew install zbar), or enter the secret without --qr.")
    result = subprocess.run(
        [decoder, "--quiet", "--raw", str(Path(path).resolve())], capture_output=True, text=True, timeout=30
    )
    values = [line for line in result.stdout.splitlines() if line.startswith("otpauth://")]
    if result.returncode or len(values) != 1:
        raise CredentialError("Expected exactly one otpauth QR code in the image.")
    return parse_totp(values[0])


def app_profiles():
    if not APP.exists():
        return []
    result = subprocess.run([str(APP), "--list-profiles"], capture_output=True, text=True, timeout=15)
    if result.returncode:
        raise CredentialError("Cannot list OpenVPN Connect profiles.")
    try:
        return json.loads(result.stdout)
    except ValueError:
        raise CredentialError("Unexpected OpenVPN Connect profile list response.") from None


def runtime_dir():
    root = Path(os.environ.get("EVO_OPENVPN_HOME", str(Path.home() / ".evo" / "openvpn")))
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    if root.is_symlink() or root.stat().st_uid != os.getuid():
        raise CredentialError("OpenVPN runtime directory must be owned by the current user, not a symlink.")
    root.chmod(0o700)
    return root


def session_path(name):
    profile_name(name)
    return runtime_dir() / hashlib.sha256(name.encode()).hexdigest()[:12]


def private_write(path, text):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "w") as handle:
        os.fchmod(handle.fileno(), 0o600)
        handle.write(text)


def remember(name):
    private_write(runtime_dir() / "last", profile_name(name))


def last_profile():
    try:
        return (runtime_dir() / "last").read_text().strip()
    except OSError:
        return None


def request(name, command="status"):
    endpoint = str(session_path(name)) + ".sock"
    with socket.socket(socket.AF_UNIX) as client:
        client.settimeout(2)
        try:
            client.connect(endpoint)
        except (FileNotFoundError, ConnectionRefusedError):
            path = Path(str(session_path(name)) + ".json")
            if path.exists():
                last = json.loads(path.read_text())
                if last["state"] not in ("FAILED", "STOPPED") or last.get("cleanup_failed"):
                    return {
                        "profile": name,
                        "state": "UNKNOWN",
                        "error": "Worker unavailable; inspect OpenVPN processes.",
                    }
                if last.get("error"):
                    return {"profile": name, "state": "STOPPED", "error": last["error"]}
            return {"profile": name, "state": "STOPPED"}
        client.sendall(command.encode() + b"\n")
        # The worker closes after one reply; a log reply spans several reads.
        chunks = []
        while chunk := client.recv(65536):
            chunks.append(chunk)
        return json.loads(b"".join(chunks))


def quote(value):
    if any(c in value for c in "\r\n\x00"):
        raise CredentialError("Credentials must not contain newlines or NUL characters.")
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def auth_commands(line, profile):
    # OpenVPN itself caches server-issued session tokens. This notification needs no reply.
    if line.startswith(">PASSWORD:Auth-Token:"):
        return ""
    if "Verification Failed" in line:
        raise CredentialError("VPN authentication rejected; check password/OTP enrollment before retrying.")
    if "Need 'Auth' username/password" not in line:
        raise CredentialError("Unsupported authentication challenge (private-key password or dynamic challenge).")
    username, password = profile.get("username", ""), profile.get("password", "")
    if not username or not password:
        raise CredentialError("Missing username/password; run evo openvpn credentials PROFILE.")
    if "SC:" in line:
        settings = profile.get("totp")
        if not settings:
            raise CredentialError("This profile needs OTP; run evo openvpn credentials PROFILE --otp.")
        password = "SCRV1:{}:{}".format(
            base64.b64encode(password.encode()).decode(), base64.b64encode(totp(settings).encode()).decode()
        )
    return f'username "Auth" {quote(username)}\npassword "Auth" {quote(password)}\n'


def worker(name, timeout):
    """Keep the management channel alive for reauthentication; never persist raw VPN logs."""
    import fcntl

    sudo_password = sys.stdin.readline().rstrip("\n")
    base = session_path(name)
    state_path = Path(str(base) + ".json")
    config = Path(str(base) + ".ovpn")
    control_path, management_path = str(base) + ".sock", str(base) + ".mgmt"
    state = {"profile": name, "state": "STARTING"}
    # Served over the control socket for diagnosis; kept in memory only, gone with the worker.
    log = deque(maxlen=200)
    lock = open(runtime_dir() / "active.lock", "a")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        lock.close()
        return
    process, management = None, None
    selector = selectors.DefaultSelector()
    control, listener = socket.socket(socket.AF_UNIX), socket.socket(socket.AF_UNIX)

    def publish():
        private_write(state_path, json.dumps(state))

    try:
        profile = get_profile(name)
        validate_config(profile["config"])
        private_write(config, profile["config"])
        for path, server in ((control_path, control), (management_path, listener)):
            Path(path).unlink(missing_ok=True)
            server.bind(path)
            os.chmod(path, 0o600)
            server.listen(2)
        selector.register(control, selectors.EVENT_READ, "control")
        selector.register(listener, selectors.EVENT_READ, "accept")
        publish()
        binary = shutil.which("openvpn")
        if not binary:
            raise CredentialError("OpenVPN binary missing; install openvpn first.")
        command = [
            binary,
            "--config",
            str(config),
            "--script-security",
            "1",
            "--remote-cert-tls",
            "server",
            "--management",
            management_path,
            "unix",
            "--management-client",
            "--management-query-passwords",
            "--management-hold",
            # No --remap-usr1/--connect-retry-max: soft restarts (ping-restart, connection-reset,
            # tls-error) must reconnect, not exit. The worker's deadline bounds the first connect,
            # and --management-client already exits OpenVPN if the worker goes away.
            "--auth-nocache",
            "--auth-retry",
            "none",
            "--connect-timeout",
            str(timeout),
            # Keep idle TCP sessions alive through NAT/firewalls; a server-pushed keepalive overrides it.
            "--ping",
            "10",
            "--verb",
            "3",
        ]
        if os.geteuid() != 0:
            command = ["sudo", "-S", "-p", "", "--"] + command
        process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        process.stdin.write((sudo_password + "\n").encode())
        process.stdin.close()
        sudo_password = ""
        deadline = time.monotonic() + timeout
        buffer = b""
        stopping = False
        while process.poll() is None:
            if time.monotonic() > deadline:
                raise CredentialError("VPN connection timed out." if not stopping else "VPN shutdown timed out.")
            for key, _ in selector.select(0.2):
                if key.data == "control":
                    with control.accept()[0] as client:
                        client.settimeout(1)
                        try:
                            command = client.recv(64).strip()
                            if command == b"disconnect":
                                if management:
                                    management.sendall(b"signal SIGTERM\n")
                                stopping = True
                                state["state"] = "STOPPING"
                                deadline = time.monotonic() + 15
                                publish()
                            reply = {**state, "log": list(log)} if command == b"log" else state
                            client.sendall(json.dumps(reply).encode())
                        except (OSError, TimeoutError):
                            pass
                elif key.data == "accept":
                    management = listener.accept()[0]
                    management.settimeout(2)
                    selector.unregister(listener)
                    selector.register(management, selectors.EVENT_READ, "management")
                    # The hold flag survives restarts and OpenVPN resets the release on each one: without
                    # "hold off" every reconnect would wait forever for another "hold release".
                    management.sendall(b"state on\nlog on\nbytecount 2\nhold off\nhold release\n")
                    if stopping:
                        management.sendall(b"signal SIGTERM\n")
                else:
                    chunk = management.recv(8192)
                    if not chunk:
                        selector.unregister(management)
                        if not stopping:
                            state["state"] = "STOPPING"
                            deadline = time.monotonic() + 15
                        continue
                    buffer += chunk
                    if len(buffer) > 131072:
                        raise CredentialError("Unexpected management response size.")
                    while b"\n" in buffer:
                        raw, buffer = buffer.split(b"\n", 1)
                        line = raw.decode(errors="replace").strip()
                        if line.startswith(">PASSWORD:"):
                            management.sendall(auth_commands(line, get_profile(name)).encode())
                        elif line.startswith(">BYTECOUNT:"):
                            # Served live over the control socket; not worth a disk write every 2s.
                            received, _, sent = line[11:].partition(",")
                            if received.isdigit() and sent.isdigit():
                                state["bytes_in"], state["bytes_out"] = int(received), int(sent)
                        elif line.startswith(">LOG:"):
                            stamp, _, rest = line[5:].partition(",")
                            if stamp.isdigit():
                                clock = time.strftime("%H:%M:%S", time.localtime(int(stamp)))
                                log.append(f"{clock}  {rest.partition(',')[2]}")
                        elif line.startswith(">FATAL:"):
                            raise CredentialError("OpenVPN reported a fatal error; check config, TLS and permissions.")
                        elif line.startswith(">STATE:"):
                            fields = line[7:].split(",")
                            if len(fields) >= 4:
                                state["state"] = fields[1]
                                if fields[1] == "CONNECTED" and fields[2] == "SUCCESS":
                                    state["vpn_ip"] = fields[3]
                                    state["since"] = time.time()
                                    # Once up, let OpenVPN retry for as long as the network is gone.
                                    deadline = float("inf")
                                elif fields[1] != "CONNECTED":
                                    state.pop("vpn_ip", None)
                                    state.pop("since", None)
                                if fields[1] == "RECONNECTING":
                                    state["reconnects"] = state.get("reconnects", 0) + 1
                                if fields[1] in ("RECONNECTING", "EXITING"):
                                    state["last_reason"] = fields[2]
                                publish()
        if stopping:
            state = {"profile": name, "state": "STOPPED"}
        else:
            reason = f" ({state['last_reason']})" if state.get("last_reason") else ""
            raise CredentialError(
                f"OpenVPN exited{reason}; check authentication, profile options, network and sudo access."
            )
    except Exception as exc:
        state = {
            "profile": name,
            "state": "FAILED",
            "error": str(exc)
            if isinstance(exc, CredentialError)
            else "OpenVPN worker failed; check local permissions and configuration.",
        }
    finally:
        if management:
            try:
                management.sendall(b"signal SIGTERM\n")
            except OSError:
                pass
            management.close()
        # Closing management triggers SIGTERM in OpenVPN, including if the worker crashes.
        if process:
            try:
                process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                state = {
                    "profile": name,
                    "state": "FAILED",
                    "cleanup_failed": True,
                    "error": "OpenVPN did not exit; inspect processes manually.",
                }
        publish()
        selector.close()
        control.close()
        listener.close()
        for path in (control_path, management_path, config):
            Path(path).unlink(missing_ok=True)
        lock.close()


if __name__ == "__main__":
    worker(sys.argv[1], int(sys.argv[2]))
