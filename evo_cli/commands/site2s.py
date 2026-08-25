import json
import os
import re
import sys
import tempfile
import time
import urllib.parse

import rich_click as click
from rich.text import Text

from evo_cli.console import console, error, info, step, success, warning

BASE32_ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567"
GATE_HOSTS = {"site2s.com", "98sub.net", "traffic2s.com", "seo2s.com"}
GOOGLE_REFERER = "https://www.google.com/"
CAPTCHA_URL = "https://98sub.net/site2s/captcha.php"
GETLINK_URL = "https://site2s.com/rest/connect"
GETLINKCAMP_URL = "https://site2s.com/rest/getLinkCamp"
PROFILE_DIR = os.path.join(tempfile.gettempdir(), "evo-site2s-profile")
ENCODED_RE = re.compile(r'encodedRedirectUrl\s*=\s*"([A-Z2-7=]+)"')
TOKEN_RE = re.compile(r'token\s*=\s*"([0-9a-fA-F]{16,})"')
STATUS_RE = re.compile(r'const\s+status\s*=\s*"([^"]*)"')
ERRORMSG_RE = re.compile(r'const\s+errormsg\s*=\s*"([^"]*)"')

RECAPTCHA_RESPONSE_JS = """() => {
    const ta = document.querySelector('textarea[name="g-recaptcha-response"]');
    if (ta && ta.value) return ta.value;
    try { return (typeof grecaptcha !== 'undefined') ? (grecaptcha.getResponse() || '') : ''; }
    catch (e) { return ''; }
}"""

SUBMIT_FORM_JS = """() => {
    const b = document.querySelector('button[type="submit"][name="submit"]');
    if (!b) return false;
    b.click();
    return true;
}"""

CLIENT_IP_JS = """async () => {
    try {
        const text = await fetch('https://one.one.one.one/cdn-cgi/trace').then(r => r.text());
        const line = text.split('\\n').find(l => l.startsWith('ip='));
        if (line) return line.slice(3).trim();
    } catch (e) { }
    try {
        const data = await fetch('https://api.ipify.org?format=json').then(r => r.json());
        return data.ip || '';
    } catch (e) { }
    return '';
}"""

FETCH_JSON_JS = """async (args) => {
    const res = await fetch(args.url, {
        method: 'GET',
        headers: { 'X-Requested-With': 'XMLHttpRequest' },
        credentials: args.credentials,
    });
    const body = await res.text();
    try { return JSON.parse(body); }
    catch (e) { return { status: 'error', message: 'HTTP ' + res.status, body: body.slice(0, 300) }; }
}"""

EPILOG = Text.from_markup(
    "[bold]Examples[/bold]\n\n"
    "  [cyan]evo site2s https://site2s.com/b8ijsi3b[/cyan]      resolve the final link\n"
    "  [cyan]evo site2s b8ijsi3b[/cyan]                         alias also works\n"
    "  [cyan]evo site2s <url> --json[/cyan]                     print machine-readable result\n"
    "  [cyan]evo site2s <url> --manual[/cyan]                   solve the reCAPTCHAs yourself in the window\n"
    "  [cyan]evo site2s <url> --headless[/cyan]                 no window (use under xvfb-run)\n\n"
    "[dim]Layer 1 (Base32 redirect) is decoded with no captcha. Task/campaign links need two\n"
    "reCAPTCHAs: one on site2s.com to register the device, one on 98sub.net for the token.[/dim]"
)


def base32_decode(encoded):
    bits = ""
    for ch in encoded:
        if ch == "=":
            break
        val = BASE32_ALPHABET.find(ch.upper())
        if val == -1:
            raise ValueError(f"invalid base32 char: {ch!r}")
        bits += format(val, "05b")
    out = []
    i = 0
    while i < len(bits) - 7:
        out.append(chr(int(bits[i : i + 8], 2)))
        i += 8
    return "".join(out)


def normalize_url(target):
    target = target.strip()
    if re.fullmatch(r"[A-Za-z0-9_-]+", target):
        return f"https://site2s.com/{target}", target
    if not target.startswith("http"):
        target = "https://" + target
    alias = urllib.parse.urlparse(target).path.strip("/").split("/")[-1]
    return target, alias


def host_of(url):
    return urllib.parse.urlparse(url).netloc.lower().split(":")[0]


def is_gate(url):
    host = host_of(url)
    return any(host == g or host.endswith("." + g) for g in GATE_HOSTS)


def load_browser():
    try:
        from patchright.sync_api import sync_playwright
    except ImportError as exc:
        raise click.ClickException(
            "patchright is required for Cloudflare/reCAPTCHA handling.\n"
            "  Install it with:  pip install patchright\n"
            "  It drives the locally installed Google Chrome."
        ) from exc
    return sync_playwright


def new_context(sync_playwright, headless):
    p = sync_playwright().start()
    args = ["--no-sandbox", "--disable-blink-features=AutomationControlled"]
    ctx = p.chromium.launch_persistent_context(
        user_data_dir=PROFILE_DIR,
        channel="chrome",
        headless=headless,
        no_viewport=True,
        args=args,
    )
    return p, ctx


def wait_past_cloudflare(page, timeout):
    deadline = time.time() + timeout
    while time.time() < deadline:
        page.wait_for_timeout(1500)
        title = (page.title() or "").lower()
        if "just a moment" not in title and "moment" not in title:
            return True
    return False


def wait_for_device_cookies(ctx, page, timeout=40):
    deadline = time.time() + timeout
    while time.time() < deadline:
        names = {c["name"] for c in ctx.cookies("https://site2s.com/")}
        if "dv" in names and "visitorInfo" in names:
            return True
        page.wait_for_timeout(1000)
    return False


def fetch_redirect_url(ctx, page, site_url, timeout):
    info(f"Loading [accent]{site_url}[/accent] (passing Cloudflare)...")
    page.goto(site_url, wait_until="domcontentloaded", timeout=60000)
    if not wait_past_cloudflare(page, timeout):
        raise click.ClickException("timed out waiting for Cloudflare challenge to clear")
    if not wait_for_device_cookies(ctx, page):
        warning("Device fingerprint cookies were not set; getLink may reject the token.")
    html = page.content()
    match = ENCODED_RE.search(html)
    if not match:
        return None
    decoded = base32_decode(match.group(1))
    info(f"Layer-1 redirect: [accent]{decoded}[/accent]")
    return decoded


def get_recaptcha_response(page):
    return page.evaluate(RECAPTCHA_RESPONSE_JS)


def click_recaptcha_checkbox(page):
    try:
        anchor = page.frame_locator('iframe[title="reCAPTCHA"]').locator("#recaptcha-anchor")
        anchor.click(timeout=15000)
        return True
    except Exception:
        return False


def solve_recaptcha(page, manual, captcha_wait, label):
    if manual:
        wait_for = max(captcha_wait, 240)
        warning(f"Solve the {label} reCAPTCHA in the browser window (up to {wait_for}s)...")
    else:
        wait_for = max(captcha_wait, 30)
        info(f"Ticking the {label} reCAPTCHA checkbox...")
        if not click_recaptcha_checkbox(page):
            warning("Could not tick the checkbox; waiting in case it auto-passes.")

    deadline = time.time() + wait_for
    while time.time() < deadline:
        response = get_recaptcha_response(page)
        if response:
            return response
        page.wait_for_timeout(1000)
    return ""


def fetch_json(page, url, credentials="include"):
    data = page.evaluate(FETCH_JSON_JS, {"url": url, "credentials": credentials})
    return data or {}


def register_device(page, alias, manual, captcha_wait):
    response = solve_recaptcha(page, manual, captcha_wait, "site2s")
    if not response:
        raise click.ClickException(
            "site2s reCAPTCHA not solved (an image challenge was most likely shown). "
            "Re-run with --manual to solve it yourself."
        )
    client_ip = page.evaluate(CLIENT_IP_JS) or ""
    variants = [client_ip]
    if client_ip and ":" not in client_ip:
        variants.append("::ffff:" + client_ip)
    query = urllib.parse.urlencode(
        {
            "alias": alias,
            "g-recaptcha-response": response,
            "client_ip": client_ip,
            "ip_variants": json.dumps(variants),
        }
    )
    data = fetch_json(page, GETLINKCAMP_URL + "?" + query)
    if data.get("status") != "success":
        raise click.ClickException(
            f"device registration failed: {data.get('message', json.dumps(data, ensure_ascii=False))}"
        )
    info("Device registered with site2s.")


def submit_captcha_form(page):
    submitted = False
    try:
        page.click('button[type="submit"][name="submit"]', timeout=5000)
        submitted = True
    except Exception:
        pass
    if not submitted and not page.evaluate(SUBMIT_FORM_JS):
        raise click.ClickException("captcha form not found on the 98sub page")
    try:
        page.wait_for_load_state("domcontentloaded", timeout=20000)
    except Exception:
        pass


def read_captcha_result(page, timeout=25):
    token = None
    status = ""
    errormsg = ""
    deadline = time.time() + timeout
    while time.time() < deadline:
        html = page.content()
        status_match = STATUS_RE.search(html)
        if status_match:
            status = status_match.group(1)
        error_match = ERRORMSG_RE.search(html)
        if error_match:
            errormsg = error_match.group(1)
        token_match = TOKEN_RE.search(html)
        if token_match:
            token = token_match.group(1)
            break
        if status == "error":
            break
        page.wait_for_timeout(1500)
    return token, status, errormsg


def solve_captcha_for_token(ctx, site_url, alias, manual, captcha_wait):
    page = ctx.new_page()
    captcha_page = CAPTCHA_URL + "?" + urllib.parse.urlencode({"w": site_url, "v": "0"})
    info(f"Opening captcha page for alias [accent]{alias}[/accent]...")
    page.set_extra_http_headers({"referer": GOOGLE_REFERER})
    page.goto(captcha_page, wait_until="domcontentloaded", timeout=60000)
    page.wait_for_timeout(1500)

    try:
        page.click("#getLinkButton", timeout=5000)
    except Exception:
        pass
    page.wait_for_timeout(1500)

    if not solve_recaptcha(page, manual, captcha_wait, "98sub"):
        page.close()
        raise click.ClickException(
            "98sub reCAPTCHA not solved (an image challenge was most likely shown). "
            "Re-run with --manual to solve it yourself, or the IP may be flagged "
            "(try a residential IP)."
        )

    info("reCAPTCHA solved, submitting...")
    submit_captcha_form(page)
    page.wait_for_timeout(3000)

    token, status, errormsg = read_captcha_result(page)
    page.close()
    if not token and status == "success":
        raise click.ClickException(
            "captcha accepted but the server handed out an empty token. This alias was "
            "most likely already claimed from this device/IP recently - wait a few "
            "minutes, or clear the profile at " + PROFILE_DIR
        )
    if not token:
        detail = errormsg or status or "no reason given"
        raise click.ClickException(f"captcha accepted but no token was issued (server said: {detail})")
    info(f"Token issued: [accent]{token}[/accent]")
    return token


def call_getlink(page, token, credentials):
    url = GETLINK_URL + "?" + urllib.parse.urlencode({"action": "getLink", "token": token})
    return fetch_json(page, url, credentials)


def open_campaign_site(ctx, target):
    info(f"Campaign target site: [accent]{target}[/accent]")
    page = ctx.new_page()
    page.goto(target, wait_until="domcontentloaded", timeout=60000)
    page.wait_for_timeout(2000)
    return page


def poll_getlink(ctx, page, token, timeout):
    info("Polling getLink (waiting out the server countdown ~70s)...")
    deadline = time.time() + timeout
    delay = 5
    last = {}
    campaign_page = None
    with console.status("[info]Waiting for the link to be released...[/info]", spinner="dots"):
        while time.time() < deadline:
            source = campaign_page or page
            credentials = "same-origin" if campaign_page else "include"
            data = call_getlink(source, token, credentials)
            last = data
            if data.get("status") == "success" and data.get("urlrespone"):
                return data
            target = (data.get("debug") or {}).get("jobtf_http")
            if target and campaign_page is None:
                campaign_page = open_campaign_site(ctx, target)
                continue
            time.sleep(delay)
    raise click.ClickException(
        f"link not released within timeout. Last response: {json.dumps(last, ensure_ascii=False)}"
    )


def run(target, headless, manual, timeout, captcha_wait, as_json):
    site_url, alias = normalize_url(target)
    sync_playwright = load_browser()

    if not headless and sys.platform.startswith("linux") and not os.environ.get("DISPLAY"):
        warning("No DISPLAY found. Headful Chrome needs a display; run under `xvfb-run` or pass --headless.")

    p, ctx = new_context(sync_playwright, headless)
    try:
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        redirect_url = fetch_redirect_url(ctx, page, site_url, timeout)

        if redirect_url and not is_gate(redirect_url):
            final = redirect_url
            slug = None
        else:
            if redirect_url:
                info("Layer-1 points to a task gate; running captcha + token flow.")
            register_device(page, alias, manual, captcha_wait)
            token = solve_captcha_for_token(ctx, site_url, alias, manual, captcha_wait)
            data = poll_getlink(ctx, page, token, timeout)
            final = data.get("urlrespone")
            slug = data.get("slug")
    finally:
        try:
            ctx.close()
        finally:
            p.stop()

    if as_json:
        console.print_json(json.dumps({"alias": alias, "final_url": final, "slug": slug}))
    else:
        success(f"Final link: [accent]{final}[/accent]")
        if slug:
            info(f"slug: {slug}")
    return final


@click.command("site2s", epilog=EPILOG)
@click.argument("target")
@click.option("--headless", is_flag=True, help="Run Chrome headless (needs xvfb; Cloudflare may flag it).")
@click.option("--manual", is_flag=True, help="Pause for you to solve the reCAPTCHAs in the window.")
@click.option("--timeout", default=150, show_default=True, help="Overall timeout per stage (seconds).")
@click.option("--captcha-wait", default=40, show_default=True, help="Seconds to wait for reCAPTCHA auto-pass.")
@click.option("--json", "as_json", is_flag=True, help="Print result as JSON.")
def site2s(target, headless, manual, timeout, captcha_wait, as_json):
    """Resolve a **Site2S** short link to its real destination.

    `TARGET` is a `https://site2s.com/<alias>` URL or just the `<alias>`.

    Site2S hides the destination behind Cloudflare, a device fingerprint, a fake
    "search this keyword on Google" task, two reCAPTCHAs, and a server-side
    countdown. This command drives a real Chrome (via patchright) to pass
    Cloudflare, register the device through `getLinkCamp`, mint a token on the
    98sub captcha widget, then read the final link from `getLink` with the
    campaign site as the referrer.

    Direct links resolve instantly with no captcha. Task/campaign links need both
    reCAPTCHAs solved - they auto-pass on clean IPs, otherwise use `--manual`.
    """
    step("evo site2s")
    try:
        run(target, headless, manual, timeout, captcha_wait, as_json)
    except click.ClickException:
        raise
    except Exception as exc:
        error(str(exc))
        sys.exit(1)
