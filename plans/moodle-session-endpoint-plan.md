# План: Moodle Session Endpoint

## Мета

Додати Flask-ендпоінт `/moodle/session`, який за допомогою Playwright автоматично логіниться на Moodle через Google OAuth (використовуючи серверні облікові дані) і повертає значення cookie `MoodleSession`.

## Архітектура

```mermaid
flowchart TD
    A[Client] -->|GET /moodle/session| B[Flask: app.py]
    B --> C[moodle_session.get_moodle_session]
    C --> D{Cache valid?<br/>TTL 3600s}
    D -->|Yes| E[Return cached session]
    D -->|No| F[Launch Playwright Chromium headless]
    F --> G[Navigate to moodle.nubip.edu.ua/login]
    G --> H[Click Google OAuth button]
    H --> I[Fill Google email]
    I --> J[Fill Google password]
    J --> K[Wait for Moodle redirect]
    K --> L[Extract MoodleSession cookie]
    L --> M[Update cache]
    M --> E
    E --> N[Return JSON: {"session": "...]"}]
```

## Файли, які будуть змінені/створені

| Файл | Дія | Опис |
|------|------|------|
| `moodle_session.py` | **Створити** | Модуль із функцією `get_moodle_session()` + кешування |
| `app.py` | **Змінити** | Додати import та route `/moodle/session` |
| `requirements.txt` | **Змінити** | Додати `playwright` (якщо відсутній) |
| `.env` | **Змінити** (вручну) | Додати `MOODLE_EMAIL`, `MOODLE_PASSWORD` |

## Детальний план кроків

### Крок 1: Створити `moodle_session.py`

Модуль у корені проєкту з єдиною функцією `get_moodle_session(email, password, moodle_url)`.

**Ключові вимоги до реалізації:**

1. **Кешування з TTL** — in-memory словник `_cache` із часом життя 3600 с (1 год). Якщо кеш валідний, повертаємо збережене значення без запуску браузера.

2. **Thread-safety lock** — додати `threading.Lock()` навколо критичної секції (запуск браузера), щоб уникнути одночасних запусків Playwright при багатьох воркерах gunicorn.

3. **Обробка помилок** — try/except навколо всього Playwright-блоку:
   - `page.wait_for_url()` може кинути `TimeoutError` (невдалий логін, зміна сторінки Google)
   - `page.click()` може кинути `Error` якщо елемент не знайдено
   - Всі помилки логувати через `logger` (вже налаштований loguru в `app.py`)
   - У разі помилки повертати `None`, а ендпоінт віддасть 502

4. **Закриття браузера** — навіть при помилці браузер має закриватися, використати `finally` або контекстний менеджер.

5. **Селектори Google OAuth:**
   - Кнопка Google на Moodle: `a[href*="oauth2"]`
   - Поле email: `input[type="email"]`
   - Кнопка "Далі": `#identifierNext`
   - Поле пароля: `input[type="password"]`
   - Кнопка "Далі" (після пароля): `#passwordNext`

6. **Очікування редиректу** — `page.wait_for_url(f"{moodle_url}/**", timeout=20000)` — 20 сек таймаут.

7. **Видобування cookie** — `context.cookies()`, шукати `c['name'] == 'MoodleSession'`.

**Псевдокод:**

```python
import time
import threading
from playwright.sync_api import sync_playwright
from loguru import logger

TTL = 3600
_cache = {"value": None, "ts": 0}
_lock = threading.Lock()

def get_moodle_session(email: str, password: str, moodle_url: str = "https://moodle.nubip.edu.ua") -> str | None:
    # Перевірка кешу (без локу — read-only)
    if time.time() - _cache["ts"] < TTL and _cache["value"]:
        return _cache["value"]

    with _lock:
        # Double-check після отримання локу
        if time.time() - _cache["ts"] < TTL and _cache["value"]:
            return _cache["value"]

        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True)
                context = browser.new_context()
                page = context.new_page()

                page.goto(f"{moodle_url}/login/index.php")
                page.click('a[href*="oauth2"]')

                # Google login
                page.fill('input[type="email"]', email)
                page.click('#identifierNext')
                page.wait_for_selector('input[type="password"]', state='visible')
                page.fill('input[type="password"]', password)
                page.click('#passwordNext')

                # Чекати редірект назад на Moodle
                page.wait_for_url(f"{moodle_url}/**", timeout=20000)

                cookies = context.cookies()
                session = next(
                    (c['value'] for c in cookies if c['name'] == 'MoodleSession'),
                    None
                )
                browser.close()

                if session:
                    _cache["value"] = session
                    _cache["ts"] = time.time()
                return session

        except Exception as e:
            logger.error(f"Moodle session error: {e}")
            return None
```

### Крок 2: Додати route в `app.py`

Додати в кінець `app.py` (перед `if __name__ == '__main__'`):

```python
from moodle_session import get_moodle_session

@app.route('/moodle/session')
def moodle_session():
    session = get_moodle_session(
        os.environ["MOODLE_EMAIL"],
        os.environ["MOODLE_PASSWORD"],
        "https://moodle.nubip.edu.ua"
    )
    if session is None:
        return jsonify({"error": "Failed to obtain Moodle session"}), 502
    return jsonify({"session": session})
```

**Важливо:** import має бути зверху файла, але оскільки `moodle_session.py` не залежить від `app`, можна додати звичайний import на початку `app.py`.

### Крок 3: requirements.txt

Додати рядок:
```
playwright>=1.40.0
```

Перевірити, чи вже є (наразі немає).

### Крок 4: .env (вручну)

Додати до `/home/openclaw/mysite/.env`:
```
MOODLE_EMAIL=your_email@gmail.com
MOODLE_PASSWORD=your_password
```

### Крок 5: Перезапуск сервісу

Після внесення змін:
```bash
sudo systemctl restart mysite.service
```

## Потенційні ризики та зауваження

1. **Google MFA/2FA** — Якщо на Google-акаунті увімкнено 2FA, логін не спрацює. Рішення: використовувати акаунт без 2FA або App Password.

2. **Google CAPTCHA** — При автоматизованому логіні Google може показати CAPTCHA. Рішення: періодичний перелогін (кеш на 1 год зменшує частоту), можна додати `page.solve_captcha()` через сервіси-розпізнавачі (але це ускладнення).

3. **Зміна селекторів Google** — Google іноді змінює HTML-структуру сторінки логіну. Якщо ендпоінт перестане працювати — треба оновлювати селектори.

4. **Кеш не shared між воркерами** — Кожен воркер gunicorn має свій in-memory кеш. Це ок, просто кожен воркер може незалежно запустити Playwright при першому запиті після деплою. Lock мінімізує проблеми всередині одного воркера.

5. **Декілька одночасних запитів** — Lock гарантує, що тільки один Playwright-інстанс запускається одночасно. Інші запити чекають на лок і отримують закешоване значення.

## Перевірка

Після деплою:
```bash
curl https://openclaw.tailf549b1.ts.net/moodle/session
# -> {"session":"abc123..."}
```
