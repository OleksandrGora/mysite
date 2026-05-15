import os, sys, time, re, json, urllib.parse, base64
sys.path.insert(0, '/home/openclaw/mysite')
from playwright.sync_api import sync_playwright
import requests

EMAIL = 't.brovenko@nubip.edu.ua'
PASSWORD = 'Nubip.1515'
MOODLE_URL = 'https://elearn.nubip.edu.ua'
USER_DATA_DIR = '/home/openclaw/mysite/data/playwright_profile'
CODE = '941456'

os.makedirs(USER_DATA_DIR, exist_ok=True)

with sync_playwright() as p:
    browser = p.chromium.launch_persistent_context(
        USER_DATA_DIR, headless=False, channel=None,
        args=['--no-sandbox', '--window-size=1280,800'],
        viewport={'width': 1280, 'height': 800}, locale='uk-UA',
    )
    page = browser.pages[0] if browser.pages else browser.new_page()

    from urllib.parse import urlparse

    page.goto( f'{MOODLE_URL}/login/index.php', wait_until='networkidle')
    time.sleep(1)

    oauth = page.query_selector('a[href*="oauth2"]') or page.query_selector('a[href*="google"]')
    if oauth: oauth.click()
    time.sleep(5)

    url = page.url
    host = urlparse(url).netloc
    print('After OAuth host: ' + host, flush=True)
    print('After OAuth URL: ' + url[:150], flush=True)

    if 'elearn.nubip.edu.ua' in host:
        print('1: ALREADY_ON_MOODLE!', flush=True)
    else:
        page.wait_for_selector('input[type="email"]', timeout=15000).fill(EMAIL)
        page.click('#identifierNext')
        time.sleep(3)

        page.wait_for_selector('input[type="password"]', timeout=15000).fill(PASSWORD)
        page.click('#passwordNext')
        time.sleep(5)

        if 'challenge' in page.url:
            for el in page.query_selector_all('text=/код/i'):
                if 'отримати' in (el.text_content() or '').lower():
                    el.click()
                    time.sleep(3)
                    break

            code_input = page.wait_for_selector('input[type="tel"]', timeout=10000)
            code_input.fill(CODE)
            page.keyboard.press('Enter')

            for i in range(30):
                time.sleep(1)
                current_host = urlparse(page.url).netloc
                if 'elearn.nubip.edu.ua' in current_host:
                    print('2: On Moodle after ' + str(i+1) + 's', flush=True)
                    break

    # Get session via requests
    cookies_dict = {c['name']: c['value'] for c in browser.cookies()}
    cookie_str = '; '.join(f'{k}={v}' for k, v in cookies_dict.items())

    if 'MoodleSession' in cookies_dict:
        ms = cookies_dict['MoodleSession']
        print('3: MoodleSession: ' + ms[:30] + '...', flush=True)

        r = requests.get( f'{MOODLE_URL}/user/profile.php', headers={'Cookie': cookie_str}, timeout=30)
        m = re.search(r'"sesskey":"([^"]+)"', r.text)
        if m:
            sesskey = m.group(1)
            print('4: sesskey: ' + sesskey, flush=True)

            launch_url = f'{MOODLE_URL}/admin/tool/mobile/launch.php?service=moodle_mobile_app&passport=12345&sesskey={sesskey}'
            r2 = requests.get(launch_url, headers={'Cookie': cookie_str}, allow_redirects=False, timeout=30)
            location = r2.headers.get('Location', '')
            if 'token=' in location:
                b64 = location.replace('moodlemobile://token=', '').split('&')[0]
                decoded = urllib.parse.unquote(b64)
                try:
                    raw = base64.b64decode(decoded).decode('utf-8')
                except:
                    raw = decoded
                parts = raw.split(':::')
                token = parts[1] if len(parts) > 1 else parts[0]
                print('Token: ' + token[:30] + '...', flush=True)

                r3 = requests.get( f'{MOODLE_URL}/webservice/rest/server.php', params={
                    'wstoken': token, 'wsfunction': 'core_webservice_get_site_info',
                    'moodlewsrestformat': 'json',
                }, timeout=30)
                info = r3.json()
                if 'userid' in info:
                    print('User: ' + info.get('fullname', '?'), flush=True)
                else:
                    print('API error: ' + str(info), flush=True)
            else:
                print('No token. Location: ' + location[:200], flush=True)
    else:
        print('No MoodleSession', flush=True)
        print('Cookies: ' + str(list(cookies_dict.keys())), flush=True)

    browser.close()
    print('DONE', flush=True)
