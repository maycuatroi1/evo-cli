"""Curated library of common MCP servers, shared across evo commands.

This is the single source of truth for the MCP servers evo knows about. Both
``evo mcp add`` / ``evo mcp list`` (commands/mcp.py) and ``evo setup opencode``
(commands/opencode.py) read from this catalog, so a server is defined once and
wired everywhere.

Each entry is a transport-neutral spec:

  - remote: {"transport": "http" | "sse", "url": ...}
  - local:  {"transport": "stdio", "command": [...], "env": {...}}

plus metadata used by ``evo mcp list``: "category", "homepage", "description".
"""

MCP_REGISTRY = {
    # --- Local servers (run on demand via npx; nothing to host) ---
    "playwright": {
        "transport": "stdio",
        "command": ["npx", "-y", "@playwright/mcp@latest"],
        "category": "browser",
        "homepage": "https://github.com/microsoft/playwright-mcp",
        "description": "Playwright (Microsoft) - drive a real browser: navigate, click, snapshot.",
    },
    "exa": {
        "transport": "http",
        "url": "https://mcp.exa.ai/mcp",
        "category": "search",
        "homepage": "https://exa.ai/docs/reference/exa-mcp",
        "description": "Exa - AI web search (hosted MCP, free tier, no API key).",
    },
    "sequential-thinking": {
        "transport": "stdio",
        "command": ["npx", "-y", "@modelcontextprotocol/server-sequential-thinking"],
        "category": "reasoning",
        "homepage": "https://github.com/modelcontextprotocol/servers/tree/main/src/sequentialthinking",
        "description": "Sequential Thinking - structured step-by-step reasoning scratchpad.",
    },
    "memory": {
        "transport": "stdio",
        "command": ["npx", "-y", "@modelcontextprotocol/server-memory"],
        "category": "memory",
        "homepage": "https://github.com/modelcontextprotocol/servers/tree/main/src/memory",
        "description": "Memory - persistent knowledge-graph memory across sessions.",
    },
    # --- Remote servers (hosted; OAuth/token on first use where noted) ---
    "context7": {
        "transport": "http",
        "url": "https://mcp.context7.com/mcp",
        "category": "docs",
        "homepage": "https://github.com/upstash/context7",
        "description": "Context7 - up-to-date library docs and code examples.",
    },
    "deepwiki": {
        "transport": "sse",
        "url": "https://mcp.deepwiki.com/sse",
        "category": "docs",
        "homepage": "https://deepwiki.com",
        "description": "DeepWiki - ask questions about any public GitHub repo.",
    },
    "github": {
        "transport": "http",
        "url": "https://api.githubcopilot.com/mcp/",
        "category": "dev",
        "homepage": "https://github.com/github/github-mcp-server",
        "description": "GitHub - repos, issues, PRs, code search. OAuth/PAT on first use.",
    },
    "notion": {
        "transport": "http",
        "url": "https://mcp.notion.com/mcp",
        "category": "productivity",
        "homepage": "https://github.com/makenotion/notion-mcp-server",
        "description": "Notion workspace - pages, databases, search. OAuth on first use.",
    },
}


def is_remote(spec):
    """Whether a spec describes a hosted (remote) MCP server."""
    return spec["transport"] in ("http", "sse")


def to_opencode_config(spec):
    """Translate a registry spec into an OpenCode ``mcp`` entry."""
    if is_remote(spec):
        config = {"type": "remote", "url": spec["url"], "enabled": True}
        if spec.get("headers"):
            config["headers"] = dict(spec["headers"])
        return config
    config = {"type": "local", "command": list(spec["command"]), "enabled": True}
    if spec.get("env"):
        config["environment"] = dict(spec["env"])
    return config


def opencode_servers(*names):
    """Build an OpenCode ``mcp`` mapping for the named registry servers.

    Order follows the arguments so callers control how servers appear in config.
    """
    return {name: to_opencode_config(MCP_REGISTRY[name]) for name in names}
