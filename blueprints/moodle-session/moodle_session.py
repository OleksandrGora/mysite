import time
import threading
from playwright.sync_api import sync_playwright
from loguru import logger

TTL = 3600  # 1 година
_cache: dict = {"value": None, "ts": 0}
_lock = threading.Lock()


def get_moodle_session(
    email: str,
    password: str,
    moodle_url: str = "https://moodle.nubip.edu.ua",
) -> str | None:
    """
    Отримує MoodleSession cookie через Google OAuth за допомогою Playwright.

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

        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True)
                context = browser.new_context(
                    user_agent=(
                        "Mozilla/5.0 (X11; Linux x86_64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/120.0.0.0 Safari/537.36"
                    )
                )
                page = context.new_page()

                # 1. Переходимо на сторінку логіну Moodle
                page.goto(f"{moodle_url}/login/index.php")
                logger.debug("Moodle session: сторінка логіну завантажена")

                # 2. Знаходимо і клікаємо кнопку Google OAuth
                page.click('a[href*="oauth2"]')
                logger.debug("Moodle session: клікнули Google OAuth")

                # 3. Логін в Google
                page.fill('input[type="email"]', email)
                page.click("#identifierNext")
                logger.debug("Moodle session: email введено")

                # Чекаємо поле пароля
                page.wait_for_selector('input[type="password"]', state="visible")
                page.fill('input[type="password"]', password)
                page.click("#passwordNext")
                logger.debug("Moodle session: пароль введено")

                # 4. Чекаємо редирект назад на Moodle
                page.wait_for_url(f"{moodle_url}/**", timeout=20000)
                logger.debug("Moodle session: редирект на Moodle отримано")

                # 5. Видобуваємо cookie
                cookies = context.cookies()
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
                    logger.warning(
                        "Moodle session: cookie MoodleSession не знайдено"
                    )

                return session

        except Exception as e:
            logger.error(f"Moodle session: помилка — {e}")
            return None
