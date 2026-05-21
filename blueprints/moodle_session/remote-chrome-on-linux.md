# Інструкція: відкрити Chrome на сервері через VNC

## 0. На сервері (SSH сесія) — очистити старі процеси
```bash
pkill -f "google-chrome"
rm -f /home/openclaw/.chrome-moodle-profile/SingletonLock
```

## 1. На сервері (SSH сесія)
```bash
pkill x11vnc
x11vnc -display :10 -passwd 1234 -listen localhost -xkb -forever &
```

## 2. На MacBook (Terminal.app)
```bash
ssh -L 5900:localhost:5900 openclaw -N &
```

## 3. На MacBook — відкрити VNC Viewer
```bash
open -a "VNC Viewer"
```
Підключитись до `localhost:5900`, пароль `1234`.

## 4. На сервері (SSH сесія)
```bash
DISPLAY=:10 google-chrome --user-data-dir=/home/openclaw/.chrome-moodle-profile --no-sandbox https://elearn.nubip.edu.ua
```
Chrome відкриється у вікні VNC — логінишся вручну через Google OAuth, проходиш 2FA, ставиш "довіряти цьому пристрою".

## 5. Після успішного логіну
Профіль збережено в `/home/openclaw/.chrome-moodle-profile` — Playwright використовуватиме його для автоматичних запитів без повторної авторизації.