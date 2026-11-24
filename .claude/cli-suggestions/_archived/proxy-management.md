---
name: proxy-management
command_path: "evo proxy set|get|state <service> [<host> <port>] [--state on|off]"
occurrences: 3
framework: "Makefile"
entrypoint: "make <target>"
proposed_location: "evo_cli/commands/proxy.py (new top-level command, or under route group)"
learned_at: 2026-10-30T00:07:20
source_session: e38cc18e-0b04-488d-bb52-dbcea84d585e
---

# CLI Suggestion: `evo proxy set|get|state <service> [<host> <port>] [--state on|off]`

## Why
networksetup proxy commands appear 3+ times: during system proxy debugging, in test code, and in the _spoofdpi.py implementation. Wrapping these as a reusable utility allows independent system proxy management without depending on route-specific flow, useful for future proxy-related tasks (SOCKS tunnels, VPN integration, etc.).

## Observed calls (3x)
- `networksetup -getsecurewebproxy 'Wi-Fi'`
- `networksetup -setsecurewebproxy 'Wi-Fi' 127.0.0.1 18080`
- `networksetup -setsecurewebproxystate 'Wi-Fi' on`

## Proposed location
`evo_cli/commands/proxy.py (new top-level command, or under route group)`

## Implementation sketch
```python
@click.group()
def proxy():
    """Manage system proxy settings."""
    pass

@proxy.command()
@click.argument('service')
def get(service):
    """Get secure proxy for a network service."""
    cmd = resolve_executable(['networksetup', '-getsecurewebproxy', service])
    subprocess.run(cmd, check=True)

@proxy.command()
@click.argument('service')
@click.argument('host')
@click.argument('port', type=int)
def set(service, host, port):
    """Set secure proxy for a network service."""
    cmd = resolve_executable(['networksetup', '-setsecurewebproxy', service, host, str(port)])
    subprocess.run(cmd, check=True)

@proxy.command()
@click.argument('service')
@click.argument('state', type=click.Choice(['on', 'off']))
def state(service, state):
    """Enable or disable secure proxy."""
    cmd = resolve_executable(['networksetup', '-setsecurewebproxystate', service, state])
    subprocess.run(cmd, check=True)
```
