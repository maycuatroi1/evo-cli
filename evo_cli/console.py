import subprocess
import urllib.request

from rich.console import Console
from rich.progress import (
    BarColumn,
    DownloadColumn,
    Progress,
    TextColumn,
    TimeRemainingColumn,
    TransferSpeedColumn,
)
from rich.theme import Theme

EVO_THEME = Theme(
    {
        "info": "cyan",
        "success": "bold green",
        "warning": "bold yellow",
        "error": "bold red",
        "step": "bold magenta",
        "cmd": "dim",
        "accent": "bold cyan",
    }
)

console = Console(theme=EVO_THEME)


class CommandError(RuntimeError):
    pass


def info(message):
    console.print(f"[info]INFO[/info]  {message}")


def success(message):
    console.print(f"[success]DONE[/success]  {message}")


def warning(message):
    console.print(f"[warning]WARN[/warning]  {message}")


def error(message):
    console.print(f"[error]FAIL[/error]  {message}")


def step(title):
    console.print()
    console.rule(f"[step]{title}[/step]", style="step", align="left")


def run_command(cmd, capture=False, check=True, input_text=None, status=None):
    cmd = [str(part) for part in cmd]
    console.print(f"[cmd]$ {' '.join(cmd)}[/cmd]")
    if status:
        with console.status(f"[info]{status}[/info]", spinner="dots"):
            result = subprocess.run(cmd, capture_output=True, text=True, input=input_text)
        output = (result.stdout or "").strip()
        if output:
            console.print(output)
    else:
        result = subprocess.run(cmd, capture_output=capture, text=True, input=input_text)

    if result.returncode != 0:
        message = (result.stderr or "").strip()
        if message and (status or check):
            console.print(f"[error]{message}[/error]")
        if check:
            raise CommandError(f"command failed: {' '.join(cmd)}")
    return result


def download_file(url, destination, description="Downloading"):
    request = urllib.request.Request(url, headers={"User-Agent": "evo-cli"})
    with urllib.request.urlopen(request) as response:
        total = int(response.headers.get("Content-Length") or 0)
        columns = [
            TextColumn("[info]{task.description}[/info]"),
            BarColumn(),
            DownloadColumn(),
            TransferSpeedColumn(),
            TimeRemainingColumn(),
        ]
        with Progress(*columns, console=console) as progress:
            task = progress.add_task(description, total=total or None)
            with open(destination, "wb") as handle:
                while True:
                    chunk = response.read(65536)
                    if not chunk:
                        break
                    handle.write(chunk)
                    progress.update(task, advance=len(chunk))
