"""CLI interface for evo_cli project.

Be creative! do whatever you want!

- Install click or typer and create a CLI app
- Use builtin argparse
- Start a web application
- Import things from your .base module
"""

import argparse
import sys
from evo_cli.ssh_setup import setup_ssh, show_usage as show_ssh_usage
from evo_cli.miniconda_setup import install_miniconda, show_usage as show_miniconda_usage

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
            show_ssh_usage()
            return
            
        setup_ssh(ssh_args)
    elif args.command == "miniconda":
        # Parse arguments for miniconda installation
        miniconda_parser = argparse.ArgumentParser(description="Install Miniconda with OS-specific settings")
        miniconda_parser.add_argument('-p', '--prefix', help='Installation directory')
        miniconda_parser.add_argument('-f', '--force', action='store_true', help='Force reinstallation')
        miniconda_parser.add_argument('--help-examples', action='store_true', help='Show usage examples')
        
        miniconda_args = miniconda_parser.parse_args(sys.argv[2:])
        
        if miniconda_args.help_examples:
            show_miniconda_usage()
            return
            
        install_miniconda(miniconda_args)
    else:
        print(f"Unknown command: {args.command}")
        print("Available commands:")
        print("  setupssh  - Set up SSH key-based authentication")
        print("  miniconda - Install Miniconda (cross-platform)")
        parser.print_help()
