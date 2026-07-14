import shutil
import subprocess
import sys
import urllib.request

from rich.console import Console
from rich.markup import escape
from rich.progress import (
    BarColumn,
    DownloadColumn,
    Progress,
    TextColumn,
    TimeRemainingColumn,
    TransferSpeedColumn,
)
from rich.theme import Theme

for _stream in (sys.stdout, sys.stderr):
    _reconfigure = getattr(_stream, "reconfigure", None)
    if _reconfigure is not None:
        try:
            _reconfigure(encoding="utf-8", errors="replace")
        except (ValueError, OSError):
            pass

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


def resolve_executable(cmd):
    cmd = [str(part) for part in cmd]
    if cmd:
        resolved = shutil.which(cmd[0])
        if resolved:
            cmd[0] = resolved
    return cmd


def run_command(cmd, capture=False, check=True, input_text=None, status=None, timeout=None, stdin=None):
    cmd = [str(part) for part in cmd]
    # Escape the echo: a command containing brackets (an apt sources line, a
    # regex) would otherwise be parsed as rich markup and printed with those
    # parts silently missing, i.e. showing a command we did not run.
    console.print(f"[cmd]$ {escape(' '.join(cmd))}[/cmd]")
    exec_cmd = resolve_executable(cmd)

    run_kwargs = {
        "text": True,
        "encoding": "utf-8",
        "errors": "replace",
        "input": input_text,
        "timeout": timeout,
    }
    # subprocess.run rejects passing both `input` and `stdin`. Only attach an
    # explicit stdin (e.g. DEVNULL) when we are not feeding input, so callers can
    # detach a child from the terminal and avoid it blocking on an inherited TTY.
    if input_text is None and stdin is not None:
        run_kwargs["stdin"] = stdin

    try:
        if status:
            with console.status(f"[info]{status}[/info]", spinner="dots"):
                result = subprocess.run(exec_cmd, capture_output=True, **run_kwargs)
            output = (result.stdout or "").strip()
            if output:
                console.print(output)
        else:
            result = subprocess.run(exec_cmd, capture_output=capture, **run_kwargs)
    except subprocess.TimeoutExpired as exc:
        console.print(f"[error]command timed out after {timeout}s: {' '.join(cmd)}[/error]")
        if check:
            raise CommandError(f"command timed out after {timeout}s: {' '.join(cmd)}") from exc
        return exc

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
