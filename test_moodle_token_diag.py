"""
test_moodle_token_diag.py — diagnostic tool for Moodle token retrieval (NUBiP proxy).

Launches step-by-step tests to identify why Moodle token retrieval fails:
  A) Direct login/token.php
  B) Playwright + Google OAuth with screenshots
  C) sesskey + launch.php (after successful session)

Usage:
  python test_moodle_token_diag.py                                # uses owner creds from moodle_config.json
  python test_moodle_token_diag.py EMAIL PASSWORD [MOODLE_URL]   # test specific user

Screenshots / HTML saved to /tmp/moodle_diag/
"""
import sys
import os
import json
import re
import base64
import urllib.parse
import requests

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from playwright.sync_api import sync_playwright

DEFAULT_URL = "https://elearn.nubip.edu.ua"
SHOTS = "/tmp/moodle_diag"
PROFILE = "/home/openclaw/.chrome-moodle-profile"


def log(*a):
    print("[DIAG]", *a, flush=True)


def load_cfg_creds():
    p = os.path.join(HERE, "data", "moodle_config.json")
    if os.path.exists(p):
        d = json.load(open(p, encoding="utf-8"))
        return (d.get("moodle_email", ""), d.get("moodle_password", ""),
                d.get("moodle_url", DEFAULT_URL).rstrip("/"))
    return "", "", DEFAULT_URL


def test_direct_login_token(email, password, moodle_url):
    log("=== ATTEMPT A: direct login/token.php ===")
    try:
        r = requests.post(f"{moodle_url}/login/token.php", data={
            "username": email, "password": password, "service": "moodle_mobile_app",
        }, timeout=30)
        res = r.json()
        log("response:", json.dumps(res, ensure_ascii=False)[:200])
        if res.get("token"):
            log("token obtained:", res["token"][:16], "...")
            return res["token"]
        log("error:", res.get("error"), res.get("errorcode"))
        return None
    except Exception as e:
        log("exception:", e)
        return None


def test_playwright(email, password, moodle_url):
    log("=== ATTEMPT B: Playwright + Google OAuth ===")
    os.makedirs(SHOTS, exist_ok=True)
    os.environ.setdefault("DISPLAY", ":10")

    try:
        with sync_playwright() as p:
            try:
                browser = p.chromium.launch_persistent_context(
                    PROFILE, headless=False, channel="chrome",
                    args=["--no-sandbox", "--disable-setuid-sandbox", "--disable-dev-shm-usage",
                          "--disable-blink-features=AutomationControlled", "--ignore-certificate-errors"],
                )
            except Exception as le:
                log("FAILED to start Chrome:", le)
                if "SingletonLock" in str(le) or "already in use" in str(le):
                    log("   CAUSE: Chrome profile locked by another process (SingletonLock).")
                elif "Executable doesn't exist" in str(le) or "channel" in str(le).lower():
                    log("   CAUSE: Google Chrome not installed (channel='chrome').")
                return None

            page = browser.pages[0] if browser.pages else browser.new_page()
            try:
                page.goto(f"{moodle_url}/login/index.php", wait_until="networkidle")
                page.screenshot(path=f"{SHOTS}/01_login.png")
                log("login page opened:", page.url)

                if page.query_selector('a[href*="logout"]'):
                    log("already logged in (subset)")
                    return _finish(browser, moodle_url)

                oauth = page.query_selector('a[href*="oauth2"]') or page.query_selector('a[href*="google"]')
                if not oauth:
                    log("Google OAuth button not found"); browser.close(); return None
                oauth.click()
                log("clicked OAuth, waiting for redirect/Google...")
                page.wait_for_timeout(3000)
                page.screenshot(path=f"{SHOTS}/02_after_oauth.png")

                # If on Google page — enter credentials
                if "google.com" in page.url or "accounts.google" in page.url:
                    log("on Google page, entering email...")
                    try:
                        page.wait_for_selector('input[type="email"]', state="visible", timeout=8000)
                        page.fill('input[type="email"]', email)
                        page.click("#identifierNext")
                        page.wait_for_timeout(2000)
                        page.fill('input[type="password"]', password)
                        page.click("#passwordNext")
                    except Exception as fe:
                        log("failed to fill Google form:", fe)
                        page.screenshot(path=f"{SHOTS}/03_google_fail.png")
                        browser.close(); return None

                # Wait for redirect back to Moodle
                try:
                    page.wait_for_url(re.compile(r"^https?://elearn\.nubip\.edu\.ua"), timeout=30000)
                    log("redirected to Moodle")
                except Exception:
                    url = page.url
                    log("no redirect, current URL:", url)
                    page.screenshot(path=f"{SHOTS}/04_no_redirect.png")
                    if "challenge" in url or "signin" in url or "v2" in url or "v3" in url:
                        log("   CAUSE: Google 2FA / challenge. twofa_code needed (interactive mode).")
                    browser.close(); return None

                return _finish(browser, moodle_url)
            except Exception as e:
                log("exception in scenario:", e)
                try:
                    page.screenshot(path=f"{SHOTS}/99_error.png")
                except Exception:
                    pass
                browser.close(); return None
    except Exception as e:
        log("fatal Playwright exception:", e)
        return None


def _finish(browser, moodle_url):
    """After successful login: extract cookie + sesskey + launch.php token."""
    cookies = browser.cookies()
    session = next((c["value"] for c in cookies if c["name"] == "MoodleSession"), None)
    if not session:
        log("MoodleSession cookie not found"); browser.close(); return None
    log("MoodleSession obtained:", session[:16], "...")
    browser.close()

    headers = {"Cookie": f"MoodleSession={session}"}
    try:
        r = requests.get(f"{moodle_url}/user/profile.php", headers=headers, timeout=30)
        m = re.search(r'"sesskey":"([^"]+)"', r.text)
        if not m:
            log("sesskey not found in HTML"); return None
        sesskey = m.group(1)
        log("sesskey:", sesskey)
    except Exception as e:
        log("profile.php error:", e); return None

    try:
        r2 = requests.get(
            f"{moodle_url}/admin/tool/mobile/launch.php"
            f"?service=moodle_mobile_app&passport=12345&sesskey={sesskey}",
            headers=headers, allow_redirects=False, timeout=30)
        loc = r2.headers.get("Location", "")
        log("launch.php Location:", loc[:120])
        if "token=" not in loc:
            log("token not found in Location"); return None
        b64 = loc.replace("moodlemobile://token=", "")
        raw = base64.b64decode(urllib.parse.unquote(b64)).decode("utf-8")
        token = raw.split(":::")[1] if ":::" in raw else raw
        log("TOKEN OBTAINED:", token[:16], "...")
        # quick verify
        info = requests.get(f"{moodle_url}/webservice/rest/server.php", params={
            "wstoken": token, "wsfunction": "core_webservice_get_site_info",
            "moodlewsrestformat": "json"}).json()
        log("site_info fullname:", info.get("fullname"), "| errorcode:", info.get("errorcode"))
        return token
    except Exception as e:
        log("launch.php error:", e); return None


if __name__ == "__main__":
    if len(sys.argv) >= 3:
        email, password = sys.argv[1], sys.argv[2]
        moodle_url = (sys.argv[3] if len(sys.argv) > 3 else DEFAULT_URL).rstrip("/")
    else:
        email, password, moodle_url = load_cfg_creds()
        moodle_url = moodle_url or DEFAULT_URL

    log(f"email={email} url={moodle_url}")
    log("SingletonLock exists:", "YES" if os.path.exists(os.path.join(PROFILE, "SingletonLock")) else "NO")

    a = test_direct_login_token(email, password, moodle_url)
    if a:
        log("VERDICT: direct method works — Playwright can be removed.")
        sys.exit(0)

    b = test_playwright(email, password, moodle_url)
    if b:
        log("VERDICT: Playwright works, token obtained.")
    else:
        log("VERDICT: token NOT obtained — see screenshots in", SHOTS, "and messages above.")