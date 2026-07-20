"""
evo plantuml - install, check and render PlantUML diagrams.

A self-contained PlantUML toolbox: it downloads the official plantuml.jar
(no system package needed), checks the runtime (Java + Graphviz), renders
.puml files to PNG/SVG/PDF and scaffolds vivid, ready-to-edit templates.

When Graphviz is missing, rendering falls back to PlantUML's pure-Java
Smetana layout engine so most diagrams still work with Java alone.
"""

import json as jsonlib
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

import rich_click as click
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from evo_cli.console import console, download_file, error, info, step, success, warning

GITHUB_LATEST = "https://api.github.com/repos/plantuml/plantuml/releases/latest"
GITHUB_TAG = "https://api.github.com/repos/plantuml/plantuml/releases/tags/{tag}"
FALLBACK_VERSION = "1.2026.6"
FALLBACK_URL = "https://github.com/plantuml/plantuml/releases/download/v{ver}/plantuml-{ver}.jar"

FORMATS = ["png", "svg", "pdf", "txt", "eps", "vdx", "latex"]

THEMES = [
    "amiga",
    "aws-orange",
    "black-knight",
    "bluegray",
    "blueprint",
    "carbon-gray",
    "cerulean",
    "cerulean-outline",
    "crt-amber",
    "crt-green",
    "cyborg",
    "cyborg-outline",
    "hacker",
    "lightgray",
    "mars",
    "materia",
    "materia-outline",
    "metal",
    "mimeograph",
    "minty",
    "mono",
    "plain",
    "reddress-darkblue",
    "reddress-darkgreen",
    "reddress-darkorange",
    "reddress-darkred",
    "reddress-lightblue",
    "reddress-lightgreen",
    "reddress-lightorange",
    "reddress-lightred",
    "sandstone",
    "silver",
    "sketchy",
    "sketchy-outline",
    "spacelab",
    "spacelab-white",
    "superhero",
    "superhero-outline",
    "toy",
    "united",
    "vibrant",
]

EPILOG = Text.from_markup(
    "[bold]Examples[/bold]\n\n"
    "  [cyan]evo plantuml install[/cyan]                       download plantuml.jar + check runtime\n"
    "  [cyan]evo plantuml install --with-deps[/cyan]           also install Java + Graphviz\n"
    "  [cyan]evo plantuml check[/cyan]                         verify Java / Graphviz / jar\n"
    "  [cyan]evo plantuml new sequence -o login.puml[/cyan]    scaffold a vivid template\n"
    "  [cyan]evo plantuml render login.puml[/cyan]             render to PNG next to the source\n"
    "  [cyan]evo plantuml render diagrams/ -f svg[/cyan]       render a whole folder to SVG\n"
    "  [cyan]evo plantuml render login.puml -t cerulean --open[/cyan]\n"
    "  [cyan]evo plantuml render login.puml --watch[/cyan]     re-render on every save\n"
    "  [cyan]evo plantuml themes[/cyan]                        list bundled themes\n\n"
    "[dim]Diagrams that need layout (class, component, state, activity) use Graphviz when\n"
    "present and fall back to the built-in Smetana engine otherwise.[/dim]"
)


def base_dir():
    d = Path.home() / ".evo" / "plantuml"
    d.mkdir(parents=True, exist_ok=True)
    return d


def jar_path():
    return base_dir() / "plantuml.jar"


def version_path():
    return base_dir() / "version.txt"


def installed_version():
    p = version_path()
    if p.exists():
        return p.read_text(encoding="utf-8").strip()
    return None


def find_java():
    return shutil.which("java")


def find_dot():
    return shutil.which("dot")


def java_version():
    java = find_java()
    if not java:
        return None
    try:
        r = subprocess.run([java, "-version"], capture_output=True, text=True)
        out = (r.stderr or r.stdout or "").strip().splitlines()
        return out[0] if out else None
    except OSError:
        return None


def dot_version():
    dot = find_dot()
    if not dot:
        return None
    try:
        r = subprocess.run([dot, "-V"], capture_output=True, text=True)
        return (r.stderr or r.stdout or "").strip() or None
    except OSError:
        return None


def fetch_json(url):
    req = urllib.request.Request(url, headers={"User-Agent": "evo-cli/plantuml"})
    with urllib.request.urlopen(req, timeout=20) as resp:
        return jsonlib.loads(resp.read().decode("utf-8"))


def pick_jar_asset(assets):
    candidates = []
    for a in assets:
        name = a.get("name", "")
        if not name.endswith(".jar"):
            continue
        if "javadoc" in name or "sources" in name:
            continue
        candidates.append(a)
    exact = [a for a in candidates if re.match(r"^plantuml-\d.*\.jar$", a["name"])]
    pool = exact or candidates
    if not pool:
        return None
    pool.sort(key=lambda a: len(a["name"]))
    return pool[0]


def resolve_download(release):
    try:
        url = GITHUB_LATEST if release in (None, "latest") else GITHUB_TAG.format(tag=release)
        data = fetch_json(url)
        tag = data.get("tag_name", "").lstrip("v")
        asset = pick_jar_asset(data.get("assets", []))
        if asset:
            return asset["browser_download_url"], (tag or asset["name"])
    except (urllib.error.URLError, OSError, ValueError, KeyError) as exc:
        warning(f"GitHub API lookup failed ({exc}); using fallback {FALLBACK_VERSION}")
    ver = FALLBACK_VERSION if release in (None, "latest") else release.lstrip("v")
    return FALLBACK_URL.format(ver=ver), ver


def detect_package_manager():
    for mgr in ("apt-get", "dnf", "yum", "pacman", "zypper", "brew", "winget", "choco"):
        if shutil.which(mgr):
            return mgr
    return None


def install_packages(packages, assume_yes):
    mgr = detect_package_manager()
    if not mgr:
        warning("No supported package manager found. Install manually: " + ", ".join(packages))
        return False
    sudo = [] if (os.name == "nt" or mgr == "brew" or os.geteuid() == 0) else ["sudo"]
    yes = ["-y"] if assume_yes else []
    pkgmap = {
        "java": {
            "apt-get": "default-jre",
            "dnf": "java-latest-openjdk",
            "yum": "java-latest-openjdk",
            "pacman": "jre-openjdk",
            "zypper": "java-openjdk",
            "brew": "openjdk",
            "winget": "EclipseAdoptium.Temurin.21.JRE",
            "choco": "temurin",
        },
        "graphviz": {
            "apt-get": "graphviz",
            "dnf": "graphviz",
            "yum": "graphviz",
            "pacman": "graphviz",
            "zypper": "graphviz",
            "brew": "graphviz",
            "winget": "Graphviz.Graphviz",
            "choco": "graphviz",
        },
    }
    cmds = {
        "apt-get": lambda p: sudo + ["apt-get", "install"] + yes + p,
        "dnf": lambda p: sudo + ["dnf", "install"] + yes + p,
        "yum": lambda p: sudo + ["yum", "install"] + yes + p,
        "pacman": lambda p: sudo + ["pacman", "-S", "--noconfirm" if assume_yes else "--needed"] + p,
        "zypper": lambda p: sudo + ["zypper", "install"] + yes + p,
        "brew": lambda p: ["brew", "install"] + p,
        "choco": lambda p: ["choco", "install"] + (["-y"] if assume_yes else []) + p,
    }
    resolved = [pkgmap[pkg][mgr] for pkg in packages if pkg in pkgmap and mgr in pkgmap[pkg]]
    if not resolved:
        warning(f"Don't know how to install {packages} with {mgr}.")
        return False
    if mgr == "apt-get":
        subprocess.run(sudo + ["apt-get", "update"], check=False)
    if mgr == "winget":
        ok = True
        for pkg in packages:
            ident = pkgmap[pkg][mgr]
            cmd = ["winget", "install", "-e", "--id", ident]
            console.print(f"[cmd]$ {' '.join(cmd)}[/cmd]")
            ok = subprocess.run(cmd).returncode == 0 and ok
        return ok
    cmd = cmds[mgr](resolved)
    console.print(f"[cmd]$ {' '.join(cmd)}[/cmd]")
    return subprocess.run(cmd).returncode == 0


def run_jar(args, capture=True):
    java = find_java()
    if not java:
        raise click.ClickException("Java not found. Run `evo plantuml install --with-deps` first.")
    if not jar_path().exists():
        raise click.ClickException("plantuml.jar not found. Run `evo plantuml install` first.")
    cmd = [java, "-jar", str(jar_path())] + list(args)
    return subprocess.run(cmd, capture_output=capture, text=True)


# install -------------------------------------------------------------------


def do_install(release, force, with_deps, assume_yes):
    if with_deps:
        missing = []
        if not find_java():
            missing.append("java")
        if not find_dot():
            missing.append("graphviz")
        if missing:
            info(f"Installing system packages: [accent]{', '.join(missing)}[/accent]")
            install_packages(missing, assume_yes)
        else:
            info("Java and Graphviz already present.")

    jar = jar_path()
    if jar.exists() and not force:
        warning(f"plantuml.jar already present at [accent]{jar}[/accent] (version {installed_version() or 'unknown'})")
        info("Pass --force to re-download.")
    else:
        url, ver = resolve_download(release)
        info(f"Downloading PlantUML [accent]{ver}[/accent]")
        with tempfile.TemporaryDirectory() as tmp:
            tmp_jar = Path(tmp) / "plantuml.jar"
            try:
                download_file(url, str(tmp_jar), "plantuml.jar")
            except (urllib.error.URLError, OSError) as exc:
                raise click.ClickException(f"Download failed: {exc}")
            shutil.move(str(tmp_jar), str(jar))
        version_path().write_text(ver, encoding="utf-8")
        success(f"Installed plantuml.jar ({ver}) at [accent]{jar}[/accent]")

    report_environment()


def report_environment():
    jv = java_version()
    dv = dot_version()
    console.print()
    table = Table(show_header=True, header_style="accent", expand=False)
    table.add_column("Component", style="info", no_wrap=True)
    table.add_column("Status", no_wrap=True)
    table.add_column("Detail", style="dim")
    table.add_row(
        "plantuml.jar",
        "[success]ok[/success]" if jar_path().exists() else "[error]missing[/error]",
        f"{jar_path()} ({installed_version() or '-'})",
    )
    table.add_row("Java", "[success]ok[/success]" if jv else "[error]missing[/error]", jv or "needed to run PlantUML")
    table.add_row(
        "Graphviz (dot)",
        "[success]ok[/success]" if dv else "[warning]missing[/warning]",
        dv or "optional - Smetana fallback will be used",
    )
    console.print(table)


# check ---------------------------------------------------------------------


def do_check():
    report_environment()
    if not find_java() or not jar_path().exists():
        error("PlantUML is not ready. Run `evo plantuml install --with-deps`.")
        sys.exit(1)

    r = run_jar(["-version"])
    out = (r.stdout or "") + (r.stderr or "")
    for line in out.strip().splitlines()[:4]:
        console.print(f"[dim]{line}[/dim]")

    info("Testing Graphviz integration...")
    rt = run_jar(["-testdot"])
    test_out = ((rt.stdout or "") + (rt.stderr or "")).strip()
    if "Error" in test_out or "cannot" in test_out.lower():
        warning("Graphviz not usable by PlantUML; layout diagrams will use Smetana.")
        for line in test_out.splitlines()[:4]:
            console.print(f"[dim]{line}[/dim]")
    else:
        for line in test_out.splitlines()[:4]:
            console.print(f"[dim]{line}[/dim]")
        success("Graphviz works with PlantUML.")

    step("Verdict")
    if find_dot():
        success("Ready to render every diagram type.")
    else:
        success("Ready to render (Graphviz missing: using Smetana for layout diagrams).")


# render --------------------------------------------------------------------


def collect_sources(target):
    p = Path(target)
    if p.is_dir():
        files = sorted(set(p.rglob("*.puml")) | set(p.rglob("*.plantuml")) | set(p.rglob("*.pu")))
        return [str(f) for f in files]
    if p.exists():
        return [str(p)]
    raise click.ClickException(f"No such file or directory: {target}")


def build_config(theme, engine):
    lines = []
    if engine == "smetana" or (engine == "auto" and not find_dot()):
        lines.append("!pragma layout smetana")
    if theme:
        lines.append(f"!theme {theme}")
    if not lines:
        return None
    fd, path = tempfile.mkstemp(suffix=".puml", prefix="evo-config-")
    with os.fdopen(fd, "w", encoding="utf-8") as h:
        h.write("\n".join(lines) + "\n")
    return path


def output_for(src, outdir, fmt):
    src_path = Path(src)
    ext = "tex" if fmt == "latex" else fmt
    target_dir = Path(outdir) if outdir else src_path.parent
    return target_dir / f"{src_path.stem}.{ext}"


def render_once(sources, fmt, outdir, config, scale, charset, extra):
    args = [f"-t{fmt}", "-charset", charset]
    if outdir:
        args += ["-o", str(Path(outdir).resolve())]
    if config:
        args += ["-config", config]
    if scale:
        args += [f"-Sdpi={scale}"]
    args += list(extra)
    args += sources
    r = run_jar(args, capture=True)
    out = ((r.stdout or "") + (r.stderr or "")).strip()
    return r.returncode, out


def do_render(target, fmt, outdir, theme, engine, scale, charset, do_open, watch):
    sources = collect_sources(target)
    if not sources:
        warning("No .puml / .plantuml / .pu files found.")
        return
    if outdir:
        Path(outdir).mkdir(parents=True, exist_ok=True)

    config = build_config(theme, engine)
    extra = []
    if engine == "smetana":
        info("Layout engine: Smetana (pure Java)")
    elif not find_dot():
        info("Graphviz not found: using Smetana layout engine")

    def _render():
        code, out = render_once(sources, fmt, outdir, config, scale, charset, extra)
        if out:
            level = error if code != 0 else (warning if "Warning" in out else info)
            for line in out.splitlines()[:8]:
                level(line)
        if code != 0:
            error("Rendering failed.")
            return False
        for src in sources:
            produced = output_for(src, outdir, fmt)
            mark = "[success]ok[/success]" if produced.exists() else "[warning]?[/warning]"
            console.print(f"  {mark}  {src} -> [accent]{produced}[/accent]")
        return True

    try:
        ok = _render()
        if ok:
            success(f"Rendered {len(sources)} file(s) to {fmt.upper()}.")
            if do_open and not watch:
                open_file(output_for(sources[0], outdir, fmt))

        if watch:
            info("Watching for changes - Ctrl+C to stop.")
            mtimes = {s: _mtime(s) for s in sources}
            while True:
                time.sleep(1)
                for s in list(sources):
                    m = _mtime(s)
                    if m != mtimes.get(s):
                        mtimes[s] = m
                        console.print(f"[cmd]changed: {s}[/cmd]")
                        render_once([s], fmt, outdir, config, scale, charset, extra)
                        produced = output_for(s, outdir, fmt)
                        success(f"re-rendered -> {produced}")
    except KeyboardInterrupt:
        console.print()
        info("Stopped watching.")
    finally:
        if config:
            try:
                os.unlink(config)
            except OSError:
                pass


def _mtime(path):
    try:
        return os.path.getmtime(path)
    except OSError:
        return None


def open_file(path):
    path = str(path)
    try:
        if os.name == "nt":
            os.startfile(path)  # noqa: S606
        elif platform.system() == "Darwin":
            subprocess.run(["open", path], check=False)
        else:
            subprocess.run(["xdg-open", path], check=False)
    except OSError as exc:
        warning(f"Could not open {path}: {exc}")


# new -----------------------------------------------------------------------

TEMPLATES = {
    "sequence": """@startuml
!theme cerulean
skinparam backgroundColor #FDFDFD
skinparam shadowing true
skinparam roundCorner 12
skinparam sequence {
  ArrowColor #2C6FBB
  LifeLineBorderColor #2C6FBB
  ParticipantBorderColor #2C6FBB
  ParticipantBackgroundColor #E8F1FB
}
title Login flow

actor User as user
participant "Web App" as web #E8F1FB
participant "Auth API" as auth #FFF3E0
database "DB" as db #E8F5E9

user -> web : open /login
web -> auth : POST /token
activate auth
auth -> db : verify credentials
db --> auth : user record
auth --> web : JWT
deactivate auth
web --> user : set cookie + redirect
note right of user : Session established
@enduml
""",
    "class": """@startuml
!theme vibrant
skinparam shadowing true
skinparam roundCorner 10
skinparam classAttributeIconSize 0
title Domain model

class User {
  +id: UUID
  +email: String
  --
  +login(): Session
}

class Session {
  +token: String
  +expiresAt: DateTime
}

interface Repository<T> {
  +get(id): T
  +save(entity: T)
}

class UserRepository
Repository <|.. UserRepository
User "1" o-- "many" Session : owns >
UserRepository ..> User : manages
@enduml
""",
    "component": """@startuml
!theme aws-orange
skinparam shadowing true
skinparam roundCorner 12
title System components

package "Frontend" {
  [Web App] as web
}
package "Backend" {
  [API Gateway] as gw
  [Auth Service] as auth
  [Orders Service] as orders
}
database "PostgreSQL" as db
queue "Kafka" as bus

web --> gw : HTTPS
gw --> auth
gw --> orders
auth --> db
orders --> db
orders --> bus : events
@enduml
""",
    "activity": """@startuml
!theme materia
skinparam shadowing true
title Checkout

start
:Add items to cart;
if (Logged in?) then (yes)
  :Load saved address;
else (no)
  :Prompt sign-in;
endif
:Review order;
repeat
  :Try payment;
repeat while (Payment failed?) is (retry)
->success;
:Send confirmation email;
stop
@enduml
""",
    "state": """@startuml
!theme superhero
skinparam shadowing true
title Order lifecycle

[*] --> Pending
Pending --> Paid : payment ok
Pending --> Cancelled : timeout
Paid --> Shipped : dispatch
Shipped --> Delivered : courier
Delivered --> [*]
Cancelled --> [*]
@enduml
""",
    "usecase": """@startuml
!theme spacelab
skinparam shadowing true
left to right direction
title Shop use cases

actor Customer
actor Admin

rectangle Shop {
  Customer --> (Browse catalog)
  Customer --> (Place order)
  Customer --> (Track order)
  Admin --> (Manage products)
  Admin --> (View reports)
}
@enduml
""",
    "mindmap": """@startmindmap
!theme vibrant
* Project
** Backend
*** API
*** Database
** Frontend
*** Web
*** Mobile
** Ops
*** CI/CD
*** Monitoring
@endmindmap
""",
    "gantt": """@startgantt
!theme cerulean
project starts 2026-01-06
[Design] lasts 5 days
[Build] lasts 10 days
[Build] starts at [Design]'s end
[Test] lasts 4 days
[Test] starts at [Build]'s end
[Launch] happens at [Test]'s end
@endgantt
""",
    "er": """@startuml
!theme reddress-lightblue
skinparam shadowing true
title ER diagram

entity User {
  *id : UUID <<PK>>
  --
  email : String
  created_at : DateTime
}
entity Order {
  *id : UUID <<PK>>
  --
  user_id : UUID <<FK>>
  total : Decimal
}
entity Item {
  *id : UUID <<PK>>
  --
  order_id : UUID <<FK>>
  name : String
}
User ||--o{ Order
Order ||--|{ Item
@enduml
""",
    "json": """@startjson
!theme vibrant
{
  "user": "binhna",
  "roles": ["admin", "dev"],
  "active": true,
  "limits": { "cpu": 4, "ram_gb": 16 }
}
@endjson
""",
}


def do_new(kind, output, force, render, fmt):
    if kind not in TEMPLATES:
        raise click.ClickException(f"Unknown template '{kind}'. Choose from: {', '.join(sorted(TEMPLATES))}")
    out = Path(output) if output else Path(f"{kind}.puml")
    if out.exists() and not force:
        raise click.ClickException(f"{out} already exists. Pass --force to overwrite.")
    out.write_text(TEMPLATES[kind], encoding="utf-8")
    success(f"Created [accent]{out}[/accent] ({kind} template)")
    info(f"Render it: [accent]evo plantuml render {out}[/accent]")
    if render:
        do_render(str(out), fmt, None, None, "auto", None, "UTF-8", True, False)


# command group -------------------------------------------------------------


@click.group("plantuml", epilog=EPILOG, context_settings={"help_option_names": ["-h", "--help"]})
def plantuml():
    """Install, check and render **PlantUML** diagrams.

    Downloads the official `plantuml.jar` into `~/.evo/plantuml` (no system
    package needed), checks the runtime (Java + Graphviz), renders `.puml`
    files to PNG/SVG/PDF and scaffolds vivid templates with `evo plantuml new`.

    Run `evo plantuml <command> -h` for the options of each subcommand.
    """


@plantuml.command("install")
@click.option(
    "-r", "--release", default="latest", show_default=True, help="PlantUML release tag, e.g. `v1.2026.6`, or `latest`."
)
@click.option("-f", "--force", is_flag=True, help="Re-download even if plantuml.jar exists.")
@click.option("--with-deps", is_flag=True, help="Also install Java and Graphviz via the system package manager.")
@click.option("-y", "--yes", "assume_yes", is_flag=True, help="Assume yes for package-manager prompts.")
def install_cmd(release, force, with_deps, assume_yes):
    """Download plantuml.jar and check the runtime.

    The jar is fetched straight from the official GitHub releases. With
    `--with-deps` it also installs **Java** and **Graphviz** using whichever
    package manager it detects (apt, dnf, pacman, brew, winget, choco).
    """
    step("evo plantuml install")
    do_install(release, force, with_deps, assume_yes)


@plantuml.command("check")
def check_cmd():
    """Verify Java, Graphviz and plantuml.jar are working.

    Prints the version of each component and runs PlantUML's own `-testdot`
    so you know whether layout diagrams will use Graphviz or the Smetana
    fallback.
    """
    step("evo plantuml check")
    do_check()


@plantuml.command("render", epilog=EPILOG)
@click.argument("target", type=click.Path())
@click.option(
    "-f", "--format", "fmt", type=click.Choice(FORMATS), default="png", show_default=True, help="Output format."
)
@click.option(
    "-o", "--output", type=click.Path(), default=None, help="Output directory (default: next to each source)."
)
@click.option("-t", "--theme", type=str, default=None, help="Apply a PlantUML theme to every diagram.")
@click.option(
    "-e",
    "--engine",
    type=click.Choice(["auto", "dot", "smetana"]),
    default="auto",
    show_default=True,
    help="Layout engine. `auto` uses Graphviz when available.",
)
@click.option("--scale", type=int, default=None, help="Output DPI (e.g. 150, 300) for raster formats.")
@click.option("--charset", default="UTF-8", show_default=True, help="Source charset.")
@click.option("--open", "do_open", is_flag=True, help="Open the first rendered file when done.")
@click.option("-w", "--watch", is_flag=True, help="Re-render automatically when sources change.")
def render_cmd(target, fmt, output, theme, engine, scale, charset, do_open, watch):
    """Render a `.puml` file or a directory of them.

    `TARGET` may be a single file or a folder (rendered recursively, matching
    `*.puml`, `*.plantuml`, `*.pu`). Use `-t` to apply a theme, `-w` to watch
    and re-render on save, and `--open` to preview the result.
    """
    step("evo plantuml render")
    do_render(target, fmt, output, theme, engine, scale, charset, do_open, watch)


@plantuml.command("new")
@click.argument("kind", type=click.Choice(sorted(TEMPLATES)))
@click.option("-o", "--output", type=click.Path(), default=None, help="Output file (default: `<kind>.puml`).")
@click.option("-f", "--force", is_flag=True, help="Overwrite the file if it exists.")
@click.option("-r", "--render", is_flag=True, help="Render the new file right away.")
@click.option(
    "--format", "fmt", type=click.Choice(FORMATS), default="png", show_default=True, help="Format to use with --render."
)
def new_cmd(kind, output, force, render, fmt):
    """Scaffold a vivid, ready-to-edit diagram template.

    `KIND` is one of: sequence, class, component, activity, state, usecase,
    mindmap, gantt, er, json. Each template ships with a theme and styling so
    the output looks good immediately.
    """
    step("evo plantuml new")
    do_new(kind, output, force, render, fmt)


@plantuml.command("themes")
def themes_cmd():
    """List the built-in PlantUML themes you can pass to `render -t`."""
    step("evo plantuml themes")
    table = Table(show_header=False, expand=False, box=None)
    cols = 3
    for i in range(0, len(THEMES), cols):
        table.add_row(*[f"[cyan]{t}[/cyan]" for t in THEMES[i : i + cols]])
    console.print(table)
    console.print()
    info(f"{len(THEMES)} themes. Use: [accent]evo plantuml render diagram.puml -t cerulean[/accent]")
    console.print(
        Panel(
            "Themes are also set inside a file with `!theme <name>` on its own line right after `@startuml`.",
            border_style="info",
            expand=False,
        )
    )
