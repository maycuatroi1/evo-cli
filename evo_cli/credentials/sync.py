import os
import shutil
import socket
import stat
import subprocess
import tempfile
from datetime import datetime
from pathlib import Path

from evo_cli.credentials.registry import credentials_dir
from evo_cli.credentials.store import CredentialError


def _force_remove(func, path, _exc_info):
    try:
        os.chmod(path, stat.S_IWRITE)
    except OSError:
        return
    func(path)


def _rmtree(path):
    shutil.rmtree(path, onerror=_force_remove)


def _run(cmd, cwd=None, check=True):
    result = subprocess.run(
        [str(part) for part in cmd],
        cwd=str(cwd) if cwd else None,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if check and result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip().splitlines()
        raise CredentialError(f"{' '.join(str(p) for p in cmd)} failed: {detail[-1] if detail else 'unknown error'}")
    return result


def sync_repo():
    repo = os.environ.get("OMELET_SYNC_REPO")
    if not repo:
        raise CredentialError("OMELET_SYNC_REPO env var required (format: owner/repo, must be PRIVATE)")
    return repo


def dir_in_repo():
    return os.environ.get("OMELET_SYNC_DIR", "credentials")


def require_gh():
    if shutil.which("gh") is None:
        raise CredentialError("gh CLI not found. Install: https://cli.github.com")
    if _run(["gh", "auth", "status"], check=False).returncode != 0:
        raise CredentialError("gh not authenticated. Run: gh auth login")


def require_private(repo):
    result = _run(["gh", "repo", "view", repo, "--json", "visibility", "-q", ".visibility"], check=False)
    visibility = result.stdout.strip()
    if visibility != "PRIVATE":
        raise CredentialError(
            f"refusing to push: repo {repo} visibility is '{visibility or 'unknown'}' (must be PRIVATE)"
        )


def _harden(root):
    for path in Path(root).rglob("*"):
        try:
            os.chmod(path, 0o600 if path.is_file() else 0o700)
        except OSError:
            pass
    try:
        os.chmod(root, 0o700)
    except OSError:
        pass


def push():
    repo = sync_repo()
    folder = dir_in_repo()
    source = credentials_dir()
    require_gh()
    if not source.is_dir():
        raise CredentialError(f"local credentials folder {source} not found (run `evo cred migrate`)")
    require_private(repo)

    tmp = Path(tempfile.mkdtemp())
    try:
        clone = tmp / "repo"
        _run(["gh", "repo", "clone", repo, str(clone)])

        target = clone / folder
        if target.exists():
            _rmtree(target)
        shutil.copytree(source, target)

        _run(["git", "add", "-A", folder], cwd=clone)
        if _run(["git", "diff", "--cached", "--quiet"], cwd=clone, check=False).returncode == 0:
            return None

        host = socket.gethostname()
        stamp = datetime.now().astimezone().isoformat(timespec="seconds")
        _run(["git", "commit", "-m", f"sync {folder} from {host} at {stamp}"], cwd=clone)

        if (
            _run(
                ["git", "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"], cwd=clone, check=False
            ).returncode
            == 0
        ):
            _run(["git", "push"], cwd=clone)
        else:
            branch = _run(["git", "symbolic-ref", "--short", "HEAD"], cwd=clone).stdout.strip()
            _run(["git", "push", "--set-upstream", "origin", branch], cwd=clone)
        return host
    finally:
        _rmtree(tmp)


def pull():
    repo = sync_repo()
    folder = dir_in_repo()
    dest = credentials_dir()
    require_gh()

    tmp = Path(tempfile.mkdtemp())
    try:
        clone = tmp / "repo"
        _run(["gh", "repo", "clone", repo, str(clone), "--", "--depth=1"])

        source = clone / folder
        if not source.is_dir():
            raise CredentialError(f"folder '{folder}' not found in repo {repo}")

        backup = None
        if dest.exists():
            backup = Path(str(dest) + ".bak." + datetime.now().strftime("%Y%m%d-%H%M%S"))
            shutil.copytree(dest, backup)
            _rmtree(dest)

        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(source, dest)
        _harden(dest)
        return backup
    finally:
        _rmtree(tmp)
