"""Test: moodle token + api call."""
import sys, json
sys.path.insert(0, '.')
import requests
from blueprints.moodle_session.config_manager import load_config, save_config

cfg = load_config()
email = cfg['moodle_email']
password = cfg['moodle_password']
moodle_url = cfg['moodle_url'].rstrip('/')

print(f"Email: {email}")
print(f"Password set: {bool(password)}")
print(f"Password length: {len(password)}")
print(f"Password raw: {repr(password)}")

# 1. Try login/token.php
print("\n1. login/token.php...")
r = requests.post(f'{moodle_url}/login/token.php', data={
    'username': email, 'password': password, 'service': 'moodle_mobile_app',
}, timeout=30)
res = r.json()
print(json.dumps(res, indent=2))
if res.get('token'):
    token = res['token']
    print(f"✅ Token: {token[:20]}...")
else:
    print(f"❌ Помилка входу")
    sys.exit(1)

# 2. Test token
print("\n2. Перевіряємо через API...")
info = requests.get(f'{moodle_url}/webservice/rest/server.php', params={
    'wstoken': token, 'wsfunction': 'core_webservice_get_site_info', 'moodlewsrestformat': 'json',
}, timeout=30)
info_json = info.json()
if info_json.get('errorcode'):
    print(f"❌ {info_json['errorcode']}: {info_json.get('message','')}")
    sys.exit(1)
print(f"✅ Користувач: {info_json.get('fullname')}")

# 3. Save token
save_config({'moodle_token': token, 'moodle_password': password})
print("✅ Token + пароль збережено")

# 4. Get courses
print("\n3. Отримуємо курси...")
courses = requests.get(f'{moodle_url}/webservice/rest/server.php', params={
    'wstoken': token, 'wsfunction': 'core_enrol_get_users_courses', 'moodlewsrestformat': 'json',
    'userid': info_json['userid'],
}, timeout=30)
courses_json = courses.json()
if isinstance(courses_json, list):
    print(f"✅ {len(courses_json)} курсів")
    for c in courses_json[:5]:
        print(f"   - {c.get('fullname','?')}")
else:
    print(f"❌ {courses_json}")
    sys.exit(1)

print("\n✅ Успішно!")