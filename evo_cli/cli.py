"""CLI interface for evo_cli project.

Be creative! do whatever you want!

- Install click or typer and create a CLI app
- Use builtin argparse
- Start a web application
- Import things from your .base module
"""

import argparse
import sys
from evo_cli.ssh_setup import setup_ssh, show_usage

def main():  # pragma: no cover
    """
    The main function executes on commands:
    `python -m evo_cli` and `$ evo_cli`.

    This is your program's entry point.

    You can change this function to do whatever you want.
    Examples:
        * Run a test suite
        * Run a server
        * Do some other stuff
        * Run a command line application (Click, Typer, ArgParse)
        * List all available tasks
        * Run an application (Flask, FastAPI, Django, etc.)
    """
    parser = argparse.ArgumentParser(
        description="EVO CLI - A collection of useful tools",
        usage="evo <command> [<args>]"
    )
    parser.add_argument("command", help="Command to run")
    
    # Parse just the command argument
    args = parser.parse_args(sys.argv[1:2])
    
    # Route to appropriate command handler
    if args.command == "setupssh":
        # Parse arguments for ssh setup
        ssh_parser = argparse.ArgumentParser(description="Set up SSH with key-based authentication")
        ssh_parser.add_argument('-H', '--host', help='SSH server hostname or IP address')
        ssh_parser.add_argument('-u', '--user', help='SSH username')
        ssh_parser.add_argument('-p', '--password', help='SSH password (not recommended, use interactive mode instead)')
        ssh_parser.add_argument('-i', '--identity', help='Path to existing identity file to use')
        ssh_parser.add_argument('--help-examples', action='store_true', help='Show usage examples')
        
        ssh_args = ssh_parser.parse_args(sys.argv[2:])
        
        if ssh_args.help_examples:
            show_usage()
            return
            
        setup_ssh(ssh_args)
    else:
        print(f"Unknown command: {args.command}")
        print("Available commands:")
        print("  setupssh - Set up SSH key-based authentication")
        parser.print_help()
