---
name: chrome-extension-proxy-interference
description: Debug system proxy ignored by Chrome due to extension proxy overrides
pattern_type: debugging_techniques
learned: true
learned_at: 2026-10-30T00:07:20
source_session: e38cc18e-0b04-488d-bb52-dbcea84d585e
---

## When to use
When macOS system proxy settings (set via `networksetup` or similar) are ignored by Chrome, or a proxy-based DPI bypass stops working in Chrome while working in other apps.

## How
1. Check Chrome's extension proxy settings in `~/Library/Application Support/Google/Chrome/<profile>/Preferences` and `Secure Preferences`
2. Search for proxy-related keys (e.g., `"proxy": {"mode": "direct"}`) in each extension's stored settings
3. Identify which extension is forcing proxy mode (often a VPN or proxy management extension)
4. Disable or remove the offending extension from Chrome's UI
5. Verify system proxy is now honored

## Why
Some Chrome extensions (VPN tools, proxy managers like Windscribe) explicitly set Chrome's proxy mode to "direct" in their stored preferences, which overrides system-level proxy configuration. Chrome itself respects its own proxy settings, not the OS proxy.

## Example
Windscribe VPN extension stores `{"proxy": {"mode": "direct"}}` in its prefs, causing Chrome to bypass the system proxy set up by `evo route open` (which uses SpoofDPI as a local HTTP proxy). Disabling the extension in Chrome preferences restored system proxy usage.
