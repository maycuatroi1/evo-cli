# Evolution CLI (Develop by Dev And for Dev)

[![codecov](https://codecov.io/gh/maycuatroi/evo-cli/branch/main/graph/badge.svg?token=evo-cli_token_here)](https://codecov.io/gh/maycuatroi/evo-cli)
[![CI](https://github.com/maycuatroi/evo-cli/actions/workflows/main.yml/badge.svg)](https://github.com/maycuatroi/evo-cli/actions/workflows/main.yml)

Awesome evo_cli created by maycuatroi

## Install it from PyPI

```bash
pip install evo-cli
```

### Available Commands

`evo` is built with [click](https://click.palletsprojects.com/) and
[rich](https://rich.readthedocs.io/). Run `evo --help` or `evo <command> -h`
for colorized help, option tables, and examples.

#### SSH Setup

Set up SSH with key-based authentication:

```bash
evo setupssh
```

Options:
- `-H, --host` - SSH server hostname or IP address
- `-u, --user` - SSH username
- `-p, --password` - SSH password (prefer the interactive prompt)
- `-P, --port` - SSH port (default: 22)
- `-i, --identity` - Existing private key to install instead of generating one

#### Miniconda Installation

Install Miniconda with cross-platform support:

```bash
evo miniconda
```

Options:
- `-p, --prefix` - Installation directory (default: ~/miniconda3 or %USERPROFILE%\miniconda3)
- `-f, --force` - Force reinstallation even if Miniconda is already installed

#### Cloudflare SSH Tunnel

Expose this Ubuntu machine's SSH server through a Cloudflare named tunnel, so you can reach it from anywhere without opening a public inbound port:

```bash
evo cfssh -H dev.example.com
```

It installs `cloudflared`, logs in to Cloudflare, creates a named tunnel, writes `/etc/cloudflared/config.yml` with an `ssh://` ingress rule, routes a proxied DNS record, and installs the `cloudflared` systemd service. Requires a Cloudflare account with a domain managed in Cloudflare.

Options:
- `-H, --hostname` - Public hostname for SSH, e.g. `dev.example.com`
- `-n, --name` - Tunnel name (default: first label of the hostname)
- `-P, --ssh-port` - Local SSH port to forward (default: 22)
- `--no-service` - Configure only, do not install the systemd service

To connect from a client machine, install `cloudflared` and add to `~/.ssh/config`:

```
Host dev.example.com
  User <your-user>
  ProxyCommand cloudflared access ssh --hostname %h
```

#### Fix Claude Code

Detect and fix the Claude Code 2.1.154-2.1.158 tool-result delivery bug (commands run but their output is returned to the model empty, duplicated, or out of order):

```bash
evo f-claude
```

It checks the installed version against the affected range, disables the auto-updater in `~/.claude/settings.json` (backing it up first), downgrades to a known-good build, respawns background sessions, and verifies the result.

Options:
- `-c, --check` - Diagnose only; make no changes
- `--pin-version` - Known-good version to install when downgrading (default: 2.1.153)
- `--no-downgrade` - Only disable the auto-updater; skip the reinstall
- `-y, --yes` - Skip the confirmation prompt
- `-f, --force` - Apply the fix even if the version is not in the affected range
- `--unpin` - Undo the fix: re-enable the auto-updater and install the latest build

#### Credentials

`evo` owns the omelet credential store. The source of truth is a folder of one JSON file per service
(`~/.omelet.d/credentials/`), which compiles into a flat `~/.omelet.json` that older consumers read
directly.

```bash
evo cred doctor                       # health + expiry of every credential, exit 1 if any expired
evo cred get openai_api_key           # print one value by dotted path, nothing else on stdout
evo cred add openai_api_key           # prompt with no echo, write the folder file, recompile
evo cred auth --service google-drive  # first-time OAuth consent, stores the refresh token
evo cred refresh --all                # refresh Google OAuth access tokens
evo cred compile                      # rebuild ~/.omelet.json from the folder
evo cred sync push                    # push the folder to a private GitHub repo via gh
```

Read a value into the environment without it reaching your shell history:

```bash
eval "$(evo cred get --export OPENAI_API_KEY openai_api_key)"
```

Configuration is env-driven, with no personal defaults baked in:

- `OMELET_DIR` - store root (default `~/.omelet.d`; the folder is `$OMELET_DIR/credentials`)
- `OMELET_CONFIG` - compiled flat file (default `~/.omelet.json`)
- `OMELET_SYNC_REPO` - required for `sync`; a **private** GitHub repo as `owner/repo`
- `OMELET_SYNC_DIR` - folder name inside the sync repo (default `credentials`)
- `RCLONE_DRIVE_CLIENT_ID` / `RCLONE_DRIVE_CLIENT_SECRET` - OAuth client for the rclone refresh

`evo cred sync push` refuses to push to a repo whose visibility is not `PRIVATE`. Never echo a value
into a shared terminal or a log; `doctor` and `list` only ever print a masked preview.

`evo cred auth` performs the initial OAuth consent that `refresh` cannot: it starts a loopback server,
opens the consent screen, and writes the resulting refresh token into the store. Create the OAuth
client in the Cloud Console first (APIs & Services -> Credentials -> Create OAuth client ID ->
**Desktop app**) and pass the downloaded JSON with `--client-secrets`; there is no gcloud equivalent
for creating an OAuth client.

`evo gdrive` reads its token from the `google_drive` entry through this store rather than parsing
`~/.omelet.json` itself, so a refresh writes to the folder and recompiles instead of racing whatever
else has the flat file open.

#### Harness Repositories

Fast-forward every available repository declared in a harness manifest:

```bash
evo harness pull
```

Run the command from a harness directory or any registered member repository. Use `--harness PATH`
when a repository belongs to multiple harnesses or the harness is not registered.

```bash
evo harness pull --harness ~/github/my-project-harness
evo harness pull --repo backend --repo frontend
evo harness pull --dry-run
```

The command reads `harness.yaml` and its optional `harness.local.yaml` overlay. Repositories marked
`present: false` are skipped. Repositories with uncommitted changes are not modified, and every pull
uses `git pull --ff-only` so the command never creates merge commits.

#### Text to Speech

Synthesise speech through Vbee (Vietnamese) or OpenAI `gpt-4o-mini-tts`, and play it right away:

```bash
evo tts speak "Xin chào, bản build đã xong"
evo tts speak -f notes.md -o notes.mp3
evo tts speak "hello there" -p openai -V nova --instructions "calm and encouraging"
git log -1 --format=%s | evo tts speak
```

`speak` is the realtime path. Text longer than the provider's per-request limit (Vbee 300
characters, OpenAI 4000) is split on sentence boundaries and the audio is joined back into one file,
so the first words start playing while the rest is still being synthesised.

For bulk work use the batch path, which goes through Vbee's async API and polls
`/v1/tts/requests/{id}` until each audio link appears:

```bash
evo tts batch chapters/ -o audio/          # one mp3 per .txt/.md file
evo tts batch a.txt b.txt -c 8             # 8 items in flight
evo tts batch --manifest jobs.jsonl        # {"id":.., "text":.., "voice":..} per line
```

OpenAI has no batch speech endpoint, so with `-p openai` the items are parallelised locally instead.

Voice codes come from `evo tts voices` (`-l en-US`, `--gender male`, `-p openai`, `--json`).

Credentials live in the omelet store, never in flags or source:

```bash
evo cred add vbee.app_id --from-stdin      # UUID from https://studio.vbee.vn/apps
evo cred add vbee.token --from-stdin       # JWT from the same app page
evo cred add openai_api_key --from-stdin
```

`VBEE_APP_ID`, `VBEE_TOKEN`, and `OPENAI_API_KEY` override the store when set. `--provider auto`
(the default) picks Vbee when its credentials exist, otherwise OpenAI.

Playback uses whichever of `ffplay`, `mpv`, `cvlc`, `afplay`, or `paplay`/`aplay` is on PATH, and
falls back to PowerShell's `MediaPlayer` on Windows. Without any of them the audio is still written
to disk and the command warns.

The same engine backs the `evo-tts` MCP server in
[agent-skills](https://github.com/maycuatroi1/agent-skills), which gives an agent a `speak` tool.
