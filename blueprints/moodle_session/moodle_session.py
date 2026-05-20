import os
import time
import random
from playwright.sync_api import sync_playwright
from loguru import logger


USER_DATA_DIR = '/home/openclaw/.chrome-moodle-profile'

STEALTH_SCRIPT = """
// Override the webdriver detection
Object.defineProperty(navigator, 'webdriver', {
    get: () => undefined,
});

// Override plugins
Object.defineProperty(navigator, 'plugins', {
    get: () => [1, 2, 3, 4, 5],
});

// Override languages
Object.defineProperty(navigator, 'languages', {
    get: () => ['uk-UA', 'uk', 'en-US', 'en'],
});

// Override chrome.runtime
window.chrome = {
    runtime: {},
    loadTimes: function() {},
    csi: function() {},
    app: {},
};

// Override permissions
const originalQuery = window.navigator.permissions.query;
window.navigator.permissions.query = (parameters) => (
    parameters.name === 'notifications' ?
        Promise.resolve({ state: Notification.permission }) :
        originalQuery(parameters)

// Remove webdriver trace
delete window.cdc_adoQpoasnfa76pfcZLmcfl_Array;
delete window.cdc_adoQpoasnfa76pfcZLmcfl_Promise;
delete window.cdc_adoQpoasnfa76pfcZLmcfl_Symbol;
"""


def _random_delay(min_s: float = 0.3, max_s: float = 1.5):
    """Випадкова затримка як у людини."""
    time.sleep(random.uniform(min_s, max_s))


def _human_type(page, selector: str, text: str):
    """Друкує текст з випадковою затримкою між символами."""
    page.click(selector)
    _random_delay(0.1, 0.3)
    for char in text:
        page.keyboard.type(char, delay=random.randint(30, 120))
        _random_delay(0.01, 0.05)


def get_moodle_session(
    email: str,
    password: str,
    moodle_url: str = "https://elearn.nubip.edu.ua",
    twofa_code: str | None = None,
    interactive: bool = False,
) -> str | None:
    """
    Отримує MoodleSession cookie через Google OAuth за допомогою Playwright.

    Завжди виконує повну авторизацію з наданими credentials.
    Кешування не використовується.
    """
    logger.info("Moodle session: запускаємо Playwright для отримання нової сесії")

    os.makedirs(USER_DATA_DIR, exist_ok=True)
    os.environ.setdefault('DISPLAY', ':10')

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch_persistent_context(
                USER_DATA_DIR,
                headless=False,
                channel="chrome",
                args=[
                    '--disable-blink-features=AutomationControlled',
                    '--no-sandbox',
                    '--disable-setuid-sandbox',
                    '--disable-infobars',
                    '--disable-dev-shm-usage',
                    '--disable-accelerated-2d-canvas',
                    '--no-first-run',
                    '--no-zygote',
                    '--disable-gpu',
                    '--ignore-certificate-errors',
                    '--window-size=1920,1080',
                ],
                user_agent=(
                    "Mozilla/5.0 (X11; Linux x86_64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/125.0.0.0 Safari/537.36"
                ),
                viewport={'width': 1920, 'height': 1080},
                locale='uk-UA',
                timezone_id='Europe/Kiev',
                geolocation={'latitude': 50.4501, 'longitude': 30.5234},
                permissions=['geolocation'],
                device_scale_factor=1,
                has_touch=False,
                is_mobile=False,
            )

            page = browser.pages[0] if browser.pages else browser.new_page()

            page.add_init_script(STEALTH_SCRIPT)

            logger.debug(f"Moodle session: перехід на {moodle_url}/login/index.php")
            page.goto(f"{moodle_url}/login/index.php", wait_until='networkidle')
            _random_delay()
            logger.debug(f"Moodle session: сторінка логіну завантажена, URL: {page.url[:200]}")

            # Перевіряємо чи вже залогінені
            logout_link = page.query_selector('a[href*="logout"]')
            if logout_link:
                logger.info("Moodle session: вже залогінені, читаємо cookies")
                cookies = browser.cookies()
                session = next(
                    (c["value"] for c in cookies if c["name"] == "MoodleSession"),
                    None,
                )
                browser.close()
                return session

            # 2. Знаходимо і клікаємо кнопку Google OAuth
            oauth_link = page.query_selector('a[href*="oauth2"]')
            if not oauth_link:
                oauth_link = page.query_selector('a[href*="google"]')
            if oauth_link:
                oauth_link.click()
            else:
                logger.warning("Moodle session: кнопка Google OAuth не знайдена")
                browser.close()
                return None

            _random_delay(1.0, 2.0)
            logger.debug("Moodle session: клікнули Google OAuth")

            moodle_redirected = False
            _random_delay(1.0, 2.0)

            import re as _re
            try:
                page.wait_for_url(
                    _re.compile(r"^https?://elearn\.nubip\.edu\.ua"),
                    timeout=30000,
                )
                moodle_redirected = True
                logger.debug(f"Moodle session: редирект на Moodle, URL: {page.url[:200]}")
            except Exception:
                logger.warning(f"Moodle session: редирект на Moodle не отримано, URL: {page.url[:200]}")

            if not moodle_redirected:
                logger.debug("Moodle session: потрібен логін в Google")
                google_logged_in = False

                try:
                    account_selector = page.wait_for_selector(
                        f'div[data-email="{email}"], div[data-identifier], [data-identifier]',
                        state='visible', timeout=3000
                    )
                    if account_selector:
                        logger.debug("Moodle session: знайдено account picker")
                        account_selector.click()
                        _random_delay(1.0, 2.0)
                        google_logged_in = True
                except Exception:
                    logger.debug("Moodle session: account picker не знайдено")

                if not google_logged_in:
                    try:
                        page.wait_for_selector('input[type="email"]', state='visible', timeout=5000)
                        _random_delay(0.5, 1.0)
                        _human_type(page, 'input[type="email"]', email)
                        _random_delay(0.5, 1.0)
                        page.click("#identifierNext")
                        logger.debug("Moodle session: email введено")
                        google_logged_in = True
                    except Exception:
                        logger.warning("Moodle session: поле email не знайдено")
                        logger.debug(f"Moodle session: невідома сторінка Google, URL: {page.url[:200]}")
                        browser.close()
                        return None

                if google_logged_in:
                    try:
                        page.wait_for_url(
                            _re.compile(r"^https?://elearn\.nubip\.edu\.ua"),
                            timeout=10000,
                        )
                        moodle_redirected = True
                        logger.debug(f"Moodle session: редирект після email, URL: {page.url[:200]}")
                    except Exception:
                        pass

            if not moodle_redirected:
                try:
                    page.wait_for_selector('input[type="password"]', state="visible", timeout=15000)
                    _random_delay(0.5, 1.5)
                    _human_type(page, 'input[type="password"]', password)
                    _random_delay(0.5, 1.0)
                    page.click("#passwordNext")
                    logger.debug("Moodle session: пароль введено")

                    try:
                        page.wait_for_url(
                            _re.compile(r"^https?://elearn\.nubip\.edu\.ua"),
                            timeout=30000,
                        )
                        moodle_redirected = True
                        logger.debug(f"Moodle session: редирект після пароля, URL: {page.url[:200]}")
                    except Exception:
                        pass
                except Exception:
                    logger.warning(f"Moodle session: поле пароля не знайдено, URL: {page.url[:200]}")

            if not moodle_redirected:
                current_url = page.url
                if 'challenge' in current_url or 'google.com/v3/signin' in current_url:
                    logger.warning("Moodle session: Google challenge сторінка — очікуємо код 2FA")
                    _random_delay(1.0, 2.0)
                    try:
                        page.screenshot(path='/tmp/google_challenge.png', full_page=True)
                        html_content = page.content()
                        with open('/tmp/google_challenge.html', 'w', encoding='utf-8') as _hf:
                            _hf.write(html_content)
                        logger.info("Moodle session: скріншот та HTML збережено в /tmp/")
                    except Exception as _se:
                        logger.warning(f"Moodle session: не вдалось зберегти скріншот: {_se}")
                    _random_delay(0.5, 1.0)
                    try:
                        email_option = page.get_by_text(_re.compile(
                            r'Отримати код підтвердження|отримати код|Get a verification code|код.*на пошту|код.*на адресу|verify.*email|code.*email',
                            _re.IGNORECASE
                        )).first
                        if email_option.is_visible(timeout=3000):
                            logger.info("Moodle session: вибираємо опцію отримання коду на пошту")
                            email_option.click()
                            _random_delay(2.0, 3.0)
                    except Exception:
                        logger.info("Moodle session: опцію отримання коду не знайдено, можливо вже на сторінці вводу коду")
                    _random_delay(1.0, 2.0)
                    phone_prompted = False
                    if twofa_code:
                        code = twofa_code
                    elif interactive:
                        code_file = '/tmp/playwright_2fa_code.txt'
                        logger.info(f"➡️  Очікую код 2FA. Напишіть його і я запишу в {code_file}")
                        logger.info("➡️  АБО через 15с спробую SMS/Google Prompt на телефон")
                        import os as _os
                        start = time.time()
                        code = None
                        while code is None and not moodle_redirected and (time.time() - start) < 300:
                            _random_delay(1.0, 2.0)
                            if _os.path.exists(code_file):
                                with open(code_file, 'r') as _f:
                                    code = _f.read().strip()
                            if code is None and not phone_prompted and (time.time() - start) > 15:
                                phone_prompted = True
                                logger.info("Moodle session: пробуємо SMS через Інший спосіб...")
                                try:
                                    other_btn = page.get_by_text(_re.compile(
                                        r'Інший спосіб|Other method|Інший|Other|Більше способів',
                                        _re.IGNORECASE
                                    )).first
                                    if other_btn.is_visible(timeout=3000):
                                        other_btn.click()
                                        _random_delay(2.0, 3.0)
                                        page.screenshot(path='/tmp/other_methods.png', full_page=True)
                                        with open('/tmp/other_methods.html', 'w', encoding='utf-8') as _hf:
                                            _hf.write(page.content())
                                        logger.info("Moodle session: збережено скріншот інших способів")
                                        sms_btn = page.get_by_text(_re.compile(
                                            r'SMS|текстове повідомлення|Text message|повідомлення.*телефон|код.*SMS|код.*текст|SMS-повідом|через SMS|надіслати.*SMS|через телефон|номер телефон|на номер|отримати.*код.*телефон|телефон.*код|голосове|дзвін',
                                            _re.IGNORECASE
                                        )).first
                                        if sms_btn.is_visible(timeout=3000):
                                            sms_btn.click()
                                            logger.info("Moodle session: ✅ SMS надіслано на телефон. Чекаю код...")
                                        else:
                                            logger.info("Moodle session: опцію SMS не знайдено, пробуємо інші...")
                                            promt_btn = page.get_by_text(_re.compile(
                                                r'сповіщення|телефон|телефона|комп\'ютера|phone|notification|Google Prompt',
                                                _re.IGNORECASE
                                            )).first
                                            if promt_btn.is_visible(timeout=3000):
                                                promt_btn.click()
                                                logger.info("Moodle session: ✅ Google Prompt надіслано на телефон!")
                                except Exception as pe:
                                    logger.warning(f"Moodle session: помилка SMS/Prompt: {pe}")
                            if phone_prompted and (time.time() - start) > 20:
                                try:
                                    page.wait_for_url(
                                        _re.compile(r"^https?://elearn\.nubip\.edu\.ua"),
                                        timeout=3000,
                                    )
                                    logger.info("Moodle session: ✅ редирект на Moodle!")
                                    moodle_redirected = True
                                except Exception:
                                    pass
                        if moodle_redirected:
                            pass
                        elif not code:
                            logger.error("Moodle session: таймаут — код SMS не отримано")
                            browser.close()
                            return None
                        else:
                            code = code.strip()
                            _os.remove(code_file)
                    else:
                        logger.error("Moodle session: код 2FA не надано")
                        browser.close()
                        return None
                    if not moodle_redirected:
                        _random_delay(1.0, 2.0)
                        input_field = page.locator(
                            '#idvPin, input[type="tel"], input[name="code"], input[aria-label*="код"], input[aria-label*="code"], input[aria-label*="digit"], input[name="idv"]'
                        ).first
                        try:
                            input_field.wait_for(state='visible', timeout=30000)
                        except Exception:
                            logger.warning("Moodle session: шукаємо будь-яке видиме поле вводу")
                            all_inputs = page.locator('input:visible').all()
                            input_field = None
                            for inp in all_inputs:
                                try:
                                    input_type = inp.get_attribute('type')
                                    if input_type and input_type in ('text', 'tel', 'number', None, ''):
                                        input_field = inp
                                        break
                                except Exception:
                                    continue
                            if not input_field:
                                logger.error("Moodle session: не знайдено поле для вводу коду")
                                browser.close()
                                return None
                        try:
                            input_field.click(timeout=2000)
                        except Exception:
                            page.keyboard.press('Tab')
                            _random_delay(0.2, 0.3)
                        _random_delay(0.1, 0.2)
                        for char in code:
                            page.keyboard.type(char, delay=random.randint(30, 80))
                            _random_delay(0.005, 0.02)
                        _random_delay(0.5, 1.0)
                        submit_btn = page.query_selector('#confirm, button[type="submit"], div[role="button"]:has-text("Далі"), div[role="button"]:has-text("Next")')
                        if submit_btn:
                            submit_btn.click()
                        else:
                            page.keyboard.press('Enter')
                    _random_delay(1.0, 2.0)
                    try:
                        page.wait_for_url(
                            _re.compile(r"^https?://elearn\.nubip\.edu\.ua"),
                            timeout=30000,
                        )
                        moodle_redirected = True
                        logger.debug(f"Moodle session: редирект після 2FA, URL: {page.url[:200]}")
                    except Exception:
                        logger.warning(f"Moodle session: редирект після 2FA не отримано, URL: {page.url[:200]}")
                else:
                    logger.warning(f"Moodle session: таймаут, URL: {current_url[:200]}")
                if not moodle_redirected:
                    browser.close()
                    return None

            logger.debug("Moodle session: редирект на Moodle отримано")

            _random_delay(0.5, 1.0)
            cookies = browser.cookies()
            session = next(
                (c["value"] for c in cookies if c["name"] == "MoodleSession"),
                None,
            )

            browser.close()

            if session:
                logger.info("Moodle session: успішно отримано нову сесію")
            else:
                logger.warning("Moodle session: cookie MoodleSession не знайдено")

            return session

    except Exception as e:
        logger.error(f"Moodle session: помилка — {e}")
        return None


if __name__ == '__main__':
    import sys
    email = sys.argv[1] if len(sys.argv) > 1 else input("Email: ").strip()
    password = sys.argv[2] if len(sys.argv) > 2 else input("Password: ").strip()
    moodle_url = sys.argv[3] if len(sys.argv) > 3 else "https://elearn.nubip.edu.ua"

    logger.info(f"=== 2FA Test: {email} @ {moodle_url} ===")
    session = get_moodle_session(email, password, moodle_url, interactive=True)
    if session:
        logger.info(f"✅ MoodleSession отримано: {session[:20]}...")
    else:
        logger.error("❌ Не вдалося отримати MoodleSession")
