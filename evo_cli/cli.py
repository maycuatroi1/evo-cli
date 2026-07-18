import rich_click as click

from evo_cli import __version__
from evo_cli.commands.claude_code import setup_claude
from evo_cli.commands.cloudflare import cfssh
from evo_cli.commands.download import download
from evo_cli.commands.fix_claude import f_claude
from evo_cli.commands.gdrive import gdrive
from evo_cli.commands.gh import setup_gh
from evo_cli.commands.harness import harness_group
from evo_cli.commands.hwid import hwid
from evo_cli.commands.hwid_reset import hwid_reset
from evo_cli.commands.localproxy import localproxy
from evo_cli.commands.mcp import mcp_group
from evo_cli.commands.miniconda import miniconda
from evo_cli.commands.netcheck import netcheck
from evo_cli.commands.opencode import setup_opencode
from evo_cli.commands.plantuml import plantuml
from evo_cli.commands.site2s import site2s
from evo_cli.commands.ssh import setupssh
from evo_cli.commands.sysmon import sysmon
from evo_cli.commands.update import update
from evo_cli.commands.wifi import wifi


@click.group("setup", help="Set up development tools and environments.")
def setup_group():
    """Commands for bootstrapping tools on a fresh machine."""


click.rich_click.USE_MARKDOWN = True
click.rich_click.SHOW_ARGUMENTS = True
click.rich_click.STYLE_OPTIONS_TABLE_BOX = "SIMPLE"
click.rich_click.STYLE_COMMANDS_TABLE_BOX = "SIMPLE"
click.rich_click.STYLE_OPTION = "bold cyan"
click.rich_click.STYLE_COMMAND = "bold cyan"
click.rich_click.STYLE_SWITCH = "bold green"

CONTEXT_SETTINGS = {"help_option_names": ["-h", "--help"]}


@click.group(context_settings=CONTEXT_SETTINGS)
@click.version_option(__version__, "-v", "--version", prog_name="evo")
def cli():
    """**EVO CLI** - a developer toolbox for setting up dev machines.

    Bootstrap a fresh machine fast: passwordless SSH, Miniconda, and
    Cloudflare SSH tunnels. Run any command with `-h` for details.
    """


setup_group.add_command(setup_claude)
setup_group.add_command(setup_gh)
setup_group.add_command(setup_opencode)
setup_group.add_command(miniconda)
setup_group.add_command(setupssh)

cli.add_command(setup_group)
cli.add_command(cfssh)
cli.add_command(download)
cli.add_command(f_claude)
cli.add_command(gdrive)
cli.add_command(harness_group)
cli.add_command(hwid)
cli.add_command(hwid_reset)
cli.add_command(site2s)
cli.add_command(localproxy)
cli.add_command(mcp_group)
cli.add_command(netcheck)
cli.add_command(plantuml)
cli.add_command(sysmon)
cli.add_command(update)
cli.add_command(wifi)


def main():
    cli()
