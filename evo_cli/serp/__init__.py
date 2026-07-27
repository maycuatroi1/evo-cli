"""SerpApi integration.

`creds` resolves the API key (env -> omelet store -> serpapi config.toml),
`install` bootstraps the official Go CLI, `client` runs searches through that
CLI when it is present and falls back to the HTTPS API when it is not, and
`render` prints the JSON payload as readable results.
"""
