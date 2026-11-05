"""Run by the com.evo.route.dns LaunchAgent: re-pick how the opened hosts resolve after a change of network."""

from evo_cli.commands.route import _spoofdpi

if __name__ == "__main__":
    _spoofdpi.refresh()
