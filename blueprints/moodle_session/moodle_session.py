import os
import time
import random
import threading
from playwright.sync_api import sync_playwright
from loguru import logger

TTL = 3600  # 1 година
_cache: dict = {"value": None, "ts": 0}
_lock = threading.Lock()

# Постійний профіль браузера, щоб Google пам'ятав пристрій
USER_DATA_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
    'data', 'playwright_profile'
)

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
);

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
    moodle_url: str = "https://moodle.nubip.edu.ua",
) -> str | None:
    """
    Отримує MoodleSession cookie через Google OAuth за допомогою Playwright.

    Використовує:
    - Постійний профіль (Google не питає 2FA при повторному вході)
    - Stealth-скрипти проти детекції автоматизації
    - Chromium з кастомними аргументами

    Результат кешується на TTL секунд (за замовчуванням 3600 = 1 година).
    Використовує threading.Lock() для потокобезпеки.
    """
    # Перевірка кешу (без локу — read-only, швидкий шлях)
    now = time.time()
    if now - _cache["ts"] < TTL and _cache["value"]:
        logger.debug("Moodle session: повертаємо закешоване значення")
        return _cache["value"]

    with _lock:
        # Double-check після отримання локу
        now = time.time()
        if now - _cache["ts"] < TTL and _cache["value"]:
            logger.debug("Moodle session: повертаємо закешоване (double-check)")
            return _cache["value"]

        logger.info("Moodle session: запускаємо Playwright для отримання нової сесії")

        # Створюємо директорію для профілю
        os.makedirs(USER_DATA_DIR, exist_ok=True)

        try:
            with sync_playwright() as p:
                # Стеле-налаштування для Chromium
                browser = p.chromium.launch_persistent_context(
                    USER_DATA_DIR,
                    headless=True,
                    channel=None,  # Використовуємо вбудований Chromium Playwright
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

                # Впроваджуємо стелс-скрипт
                page.add_init_script(STEALTH_SCRIPT)

                # 1. Переходимо на сторінку логіну Moodle
                logger.debug(f"Moodle session: перехід на {moodle_url}/login/index.php")
                page.goto(f"{moodle_url}/login/index.php", wait_until='networkidle')
                _random_delay()
                logger.debug("Moodle session: сторінка логіну завантажена")

                # 2. Знаходимо і клікаємо кнопку Google OAuth
                oauth_link = page.query_selector('a[href*="oauth2"]')
                if not oauth_link:
                    # Спробуй інші селектори
                    oauth_link = page.query_selector('a[href*="google"]')
                if oauth_link:
                    oauth_link.click()
                else:
                    logger.warning("Moodle session: кнопка Google OAuth не знайдена")
                    browser.close()
                    return None

                _random_delay(1.0, 2.0)
                logger.debug("Moodle session: клікнули Google OAuth")

                # 3. Логін в Google
                # Чекаємо поле email
                page.wait_for_selector('input[type="email"]', state='visible', timeout=15000)
                _random_delay(0.5, 1.0)

                # Вводимо email як людина
                _human_type(page, 'input[type="email"]', email)
                _random_delay(0.5, 1.0)
                page.click("#identifierNext")
                logger.debug("Moodle session: email введено")

                # Чекаємо поле пароля
                page.wait_for_selector('input[type="password"]', state="visible", timeout=15000)
                _random_delay(0.5, 1.5)

                # Вводимо пароль як людина
                _human_type(page, 'input[type="password"]', password)
                _random_delay(0.5, 1.0)
                page.click("#passwordNext")
                logger.debug("Moodle session: пароль введено")

                # 4. Чекаємо редирект назад на Moodle або challenge
                # Спочатку пробуємо дочекатись редиректу на Moodle
                _random_delay(1.0, 2.0)

                # Перевіряємо чи ми на сторінці challenge
                current_url = page.url
                if 'challenge' in current_url or 'google.com/v3/signin' in current_url:
                    logger.warning(f"Moodle session: Google challenge сторінка виявлена: {current_url[:100]}")
                    # Можливо, профіль вже збережено і при повторному запуску все буде ок
                    # Або спробуємо почекати і перезавантажити
                    logger.info("Moodle session: пробуємо дочекатись вирішення challenge...")
                    try:
                        page.wait_for_url(f"{moodle_url}/**", timeout=30000)
                    except Exception:
                        logger.error("Moodle session: challenge не вирішено, пробуємо ще раз...")
                        # Якщо challenge - зберігаємо сторінку для діагностики
                        try:
                            page.screenshot(path=os.path.join(USER_DATA_DIR, 'challenge.png'))
                            logger.info(f"Moodle session: скріншот challenge збережено в {USER_DATA_DIR}/challenge.png")
                        except Exception:
                            pass
                        browser.close()
                        return None
                else:
                    # Чекаємо редирект на Moodle
                    try:
                        page.wait_for_url(f"{moodle_url}/**", timeout=30000)
                    except Exception:
                        logger.warning("Moodle session: таймаут очікування редиректу на Moodle")
                        browser.close()
                        return None

                logger.debug("Moodle session: редирект на Moodle отримано")

                # 5. Видобуваємо cookie
                _random_delay(0.5, 1.0)
                cookies = browser.cookies()
                session = next(
                    (c["value"] for c in cookies if c["name"] == "MoodleSession"),
                    None,
                )

                browser.close()

                if session:
                    _cache["value"] = session
                    _cache["ts"] = time.time()
                    logger.info("Moodle session: успішно отримано нову сесію")
                else:
                    logger.warning("Moodle session: cookie MoodleSession не знайдено")

                return session

        except Exception as e:
            logger.error(f"Moodle session: помилка — {e}")
            return None
