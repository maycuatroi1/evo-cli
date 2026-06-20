import rich_click as click

from evo_cli import __version__
from evo_cli.commands.cloudflare import cfssh
from evo_cli.commands.fix_claude import f_claude
from evo_cli.commands.gdrive import gdrive
from evo_cli.commands.localproxy import localproxy
from evo_cli.commands.miniconda import miniconda
from evo_cli.commands.site2s import site2s
from evo_cli.commands.ssh import setupssh

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


cli.add_command(setupssh)
cli.add_command(miniconda)
cli.add_command(cfssh)
cli.add_command(f_claude)
cli.add_command(gdrive)
cli.add_command(site2s)
cli.add_command(localproxy)


def main():
    cli()
