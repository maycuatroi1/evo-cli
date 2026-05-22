import os
import platform
import subprocess
import tempfile

import rich_click as click
from rich.panel import Panel
from rich.text import Text

from evo_cli.console import console, download_file, error, info, step, warning

EPILOG = Text.from_markup(
    "[bold]Examples[/bold]\n\n"
    "  [cyan]evo miniconda[/cyan]\n"
    "  [cyan]evo miniconda -p /opt/miniconda3[/cyan]\n"
    "  [cyan]evo miniconda --force[/cyan]"
)


def is_windows():
    return platform.system() == "Windows"


def is_conda_installed(prefix):
    conda = os.path.join(prefix, "condabin", "conda.bat" if is_windows() else "conda")
    return os.path.exists(conda)


def get_default_install_path():
    home = os.environ.get("USERPROFILE", "") if is_windows() else os.path.expanduser("~")
    return os.path.join(home, "miniconda3")


def installer_url():
    system = platform.system()
    machine = platform.machine().lower()
    if system == "Windows":
        return "https://repo.anaconda.com/miniconda/Miniconda3-latest-Windows-x86_64.exe"
    if system == "Darwin":
        arch = "arm64" if machine in ("arm64", "aarch64") else "x86_64"
        return f"https://repo.anaconda.com/miniconda/Miniconda3-latest-MacOSX-{arch}.sh"
    arch = "aarch64" if machine == "aarch64" else "x86_64"
    return f"https://repo.anaconda.com/miniconda/Miniconda3-latest-Linux-{arch}.sh"


def install_miniconda_windows(prefix):
    with tempfile.TemporaryDirectory() as tmp:
        installer = os.path.join(tmp, "miniconda.exe")
        download_file(installer_url(), installer, "Miniconda installer")
        with console.status(f"[info]Installing Miniconda to {prefix}[/info]", spinner="dots"):
            subprocess.run(
                [installer, "/InstallationType=JustMe", "/RegisterPython=0", "/S", f"/D={prefix}"],
                check=True,
            )
        scripts = os.path.join(prefix, "Scripts")
        condabin = os.path.join(prefix, "condabin")
        current = subprocess.run(
            ["powershell", "-Command", "Write-Output $env:PATH"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        if scripts not in current and condabin not in current:
            info("Adding Miniconda to PATH")
            subprocess.run(["setx", "PATH", f"{scripts};{condabin};{current}"], check=True)
    return f"{os.path.join(prefix, 'condabin', 'conda.bat')} init"


def install_miniconda_unix(prefix):
    with tempfile.TemporaryDirectory() as tmp:
        installer = os.path.join(tmp, "miniconda.sh")
        download_file(installer_url(), installer, "Miniconda installer")
        with console.status(f"[info]Installing Miniconda to {prefix}[/info]", spinner="dots"):
            subprocess.run(["bash", installer, "-b", "-p", prefix], check=True, capture_output=True)
    rc_file = os.path.expanduser("~/.bashrc")
    if platform.system() == "Darwin" and os.path.exists(os.path.expanduser("~/.zshrc")):
        rc_file = os.path.expanduser("~/.zshrc")
    existing = ""
    if os.path.exists(rc_file):
        with open(rc_file) as handle:
            existing = handle.read()
    if prefix not in existing:
        info(f"Adding Miniconda to PATH in [accent]{rc_file}[/accent]")
        with open(rc_file, "a") as handle:
            handle.write("\n# >>> conda initialize >>>\n")
            handle.write(f'export PATH="{prefix}/bin:$PATH"\n')
            handle.write("# <<< conda initialize <<<\n")
    return f"source {rc_file}"


def run_install_miniconda(prefix, force):
    prefix = prefix or get_default_install_path()
    if is_conda_installed(prefix) and not force:
        warning(f"Miniconda already installed at [accent]{prefix}[/accent]")
        info("Pass --force to reinstall.")
        return
    info(f"Target prefix: [accent]{prefix}[/accent]")
    try:
        if is_windows():
            hint = install_miniconda_windows(prefix)
        else:
            hint = install_miniconda_unix(prefix)
    except subprocess.CalledProcessError as exc:
        error(f"Installer exited with an error: {exc}")
        return
    except Exception as exc:
        error(f"Miniconda installation failed: {exc}")
        return
    console.print()
    console.print(
        Panel(
            f"Miniconda installed at [accent]{prefix}[/accent]\nRestart your terminal or run:  [accent]{hint}[/accent]",
            title="miniconda complete",
            border_style="success",
            expand=False,
        )
    )


@click.command("miniconda", epilog=EPILOG)
@click.option("-p", "--prefix", type=click.Path(), help="Installation directory. Defaults to `~/miniconda3`.")
@click.option("-f", "--force", is_flag=True, help="Reinstall even if Miniconda is already present.")
def miniconda(prefix, force):
    """Install Miniconda for the current OS and wire it into your shell PATH.

    Works on Linux, macOS, and Windows. The installer is downloaded straight
    from the official Anaconda repository.
    """
    step("evo miniconda")
    run_install_miniconda(prefix, force)
