import rich_click as click

from evo_cli.commands.route.check import check
from evo_cli.commands.route.fix import close_route, open_route
from evo_cli.commands.route.probe import probe
from evo_cli.commands.route.status import status

HELP = """Find a way through to somewhere the network is keeping from you.

Two blocks look identical from a browser and need opposite fixes. A **poisoned
DNS** answer hands you a dead address while the route itself stays open, so
pinning the real address fixes it. A **DPI filter** reading the server name out
of your TLS handshake blocks every address for that name, and no hosts or DNS
edit can touch it.

- `check <host>` - what stands in the way, and which of the two it is
- `probe <host>` - every address for the name, ranked by what answers
- `open <host>` - apply the fix the diagnosis calls for
- `close <host>` - undo it again
- `status` - which fixes are currently in place

`check` and `probe` are pure Python sockets and need no privileges. `open` and
`close` edit hosts or manage a service, so they want administrator rights.
"""


@click.group("route", help=HELP)
def route_group():
    pass


for command in (check, probe, open_route, close_route, status):
    route_group.add_command(command)
