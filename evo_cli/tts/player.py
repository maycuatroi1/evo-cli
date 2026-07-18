import platform
import shutil
import subprocess
import sys

WINDOWS_SCRIPT = """
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName PresentationCore
$player = New-Object System.Windows.Media.MediaPlayer
$player.Open([uri]'{path}')
$waited = 0
while (-not $player.NaturalDuration.HasTimeSpan -and $waited -lt 100) {{
    Start-Sleep -Milliseconds 50
    $waited++
}}
$player.Play()
if ($player.NaturalDuration.HasTimeSpan) {{
    Start-Sleep -Milliseconds ([int]$player.NaturalDuration.TimeSpan.TotalMilliseconds + 400)
}} else {{
    Start-Sleep -Seconds 5
}}
$player.Stop()
$player.Close()
"""


def _players(path):
    candidates = [
        ["ffplay", "-nodisp", "-autoexit", "-loglevel", "quiet", path],
        ["mpv", "--no-video", "--really-quiet", path],
        ["cvlc", "--play-and-exit", "--intf", "dummy", path],
    ]
    system = platform.system()
    if system == "Darwin":
        candidates.insert(0, ["afplay", path])
    elif system == "Linux":
        candidates.append(["paplay", path])
        candidates.append(["aplay", path])
    return candidates


def _play_windows(path):
    powershell = shutil.which("powershell") or shutil.which("pwsh")
    if not powershell:
        return False
    script = WINDOWS_SCRIPT.format(path=str(path).replace("'", "''"))
    result = subprocess.run(
        [powershell, "-NoProfile", "-NonInteractive", "-Command", script],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    return result.returncode == 0


def play(path):
    path = str(path)
    for command in _players(path):
        if not shutil.which(command[0]):
            continue
        result = subprocess.run(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if result.returncode == 0:
            return True
    if platform.system() == "Windows":
        return _play_windows(path)
    return False


def player_hint():
    if platform.system() == "Windows":
        return "install ffmpeg (`winget install Gyan.FFmpeg`) or make sure powershell is on PATH"
    if platform.system() == "Darwin":
        return "afplay ships with macOS; otherwise `brew install ffmpeg`"
    return "install one of: ffmpeg (ffplay), mpv, vlc, alsa-utils"


def write_stdout(data):
    stream = getattr(sys.stdout, "buffer", None)
    if stream is None:
        raise RuntimeError("stdout is not binary-capable")
    stream.write(data)
    stream.flush()
