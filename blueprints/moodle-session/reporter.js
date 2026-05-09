/**
 * Google Apps Script — НУБіП Moodle Assignment Reporter
 *
 * ---------------------------------------------------------------
 * Як налаштувати:
 * ---------------------------------------------------------------
 * 1. Налаштуй PROXY_BASE — сервер, який віддає MoodleSession
 * 2. Або встав MOODLE_SESSION вручну (F12 → Cookies → elearn.nubip.edu.ua)
 * 3. Встав Telegram bot токен та chat ID
 * 4. Запусти reportAssignments() вручну
 * 5. Налаштуй Time-based trigger (щодня)
 * ---------------------------------------------------------------
 */

var BASE                 = 'https://elearn.nubip.edu.ua';
var PROXY_BASE           = 'https://openclaw.tailf549b1.ts.net';
var MOODLE_SESSION       = '';
var TELEGRAM_BOT_TOKEN   = '8502058869:AAExt1Q7D5zJyHhI49k4JRo5J4Yeo5gNSpg';
var TELEGRAM_CHAT_ID     = ['1088484594'];
var TOKEN                = '';
var MAX_NAME_LEN         = 20;


// =============================================================
// MOODLE API
// =============================================================

function moodleApi(wsfunction, params) {
  if (!params) params = {};
  var url = BASE + '/webservice/rest/server.php?'
    + 'wstoken=' + encodeURIComponent(TOKEN)
    + '&wsfunction=' + encodeURIComponent(wsfunction)
    + '&moodlewsrestformat=json';
  for (var key in params)
    url += '&' + encodeURIComponent(key) + '=' + encodeURIComponent(params[key]);
  var r = UrlFetchApp.fetch(url, { method: 'get', muteHttpExceptions: true });
  if (r.getResponseCode() !== 200) throw new Error('HTTP ' + r.getResponseCode());
  var d = JSON.parse(r.getContentText());
  if (d && d.errorcode) throw new Error(d.errorcode + ': ' + (d.message || d.error));
  return d;
}


// =============================================================
// PROXY
// =============================================================

function getMoodleSession() {
  var url = PROXY_BASE + '/moodle/session';
  var res = UrlFetchApp.fetch(url);
  var data = JSON.parse(res.getContentText());
  if (!data.session) throw new Error('Не вдалось отримати сесію: ' + JSON.stringify(data));
  return data.session;
}

function fetchMoodleData(session, url) {
  var res = UrlFetchApp.fetch(url, {
    headers: {
      "Cookie": "MoodleSession=" + session
    }
  });
  return res.getContentText();
}


// =============================================================
// AUTH
// =============================================================

function ensureToken() {
  if (TOKEN) return;

  var session = MOODLE_SESSION;
  if (!session) {
    Logger.log('Отримую сесію через проксі...');
    session = getMoodleSession();
    MOODLE_SESSION = session;
    Logger.log('Сесію отримано');
  }

  Logger.log('Отримую sesskey...');
  var html = fetchMoodleData(session, BASE + '/user/profile.php');
  var m = html.match(/"sesskey":"([^"]+)"/);
  if (!m) throw new Error('sesskey не знайдено — сесія недійсна');
  var sesskey = m[1];

  Logger.log('Отримую токен через launch.php...');
  var loc = (UrlFetchApp.fetch(
    BASE + '/admin/tool/mobile/launch.php?service=moodle_mobile_app&passport=12345&sesskey=' + sesskey,
    { headers: { 'Cookie': 'MoodleSession=' + session }, followRedirects: false, muteHttpExceptions: true }
  ).getHeaders()['Location'] || '');
  if (!loc.includes('token=')) throw new Error('Токен не отримано');

  var b64 = loc.replace('moodlemobile://token=', '');
  var raw = Utilities.newBlob(Utilities.base64Decode(decodeURIComponent(b64))).getDataAsString('UTF-8');
  TOKEN = (raw.split(':::')[1] || raw.split(':::')[0]);
  Logger.log('Токен отримано');
}


// =============================================================
// SEMESTER LOGIC
// =============================================================

function getSemester() {
  var now = new Date(), m = now.getMonth() + 1, y = now.getFullYear();
  if (m >= 1 && m <= 6)
    return { start: new Date(y,0,1), end: new Date(y,5,30), label: 'Весняний семестр ' + y };
  if (m >= 9 && m <= 12)
    return { start: new Date(y,8,1), end: new Date(y,11,31), label: 'Осінній семестр ' + y };
  return null;
}

function isInSemester(ts, sem) {
  if (!ts || ts === 0) return false;
  var d = new Date(ts * 1000);
  return d >= sem.start && d <= sem.end;
}

function daysUntil(ts) {
  if (!ts || ts === 0) return Infinity;
  return Math.ceil((ts * 1000 - Date.now()) / 86400000);
}

function isOpenSoon(ts) {
  if (!ts || ts === 0) return false;
  return daysUntil(ts) <= 7;
}


// =============================================================
// DATA FETCHING
// =============================================================

function getCourses() {
  var info = moodleApi('core_webservice_get_site_info');
  return moodleApi('core_enrol_get_users_courses', { userid: info.userid });
}

function getAssignments(cid) {
  var d = moodleApi('mod_assign_get_assignments', { 'courseids[0]': cid });
  return (d.courses && d.courses[0] && d.courses[0].assignments) || [];
}

function getQuizzes(cid) {
  var d = moodleApi('mod_quiz_get_quizzes_by_courses', { 'courseids[0]': cid });
  return d.quizzes || [];
}

function isSubmitted(assignId) {
  try {
    var r = moodleApi('mod_assign_get_submission_status', { assignid: assignId });
    return r.lastattempt && r.lastattempt.submission && r.lastattempt.submission.status === 'submitted';
  } catch (e) {
    return false;
  }
}


// =============================================================
// CLASSIFICATION
// =============================================================

function classify(assignment, semester, isTest) {
  var tOpen = isTest ? assignment.timeopen : assignment.allowsubmissionsfromdate;
  var tClose = isTest ? assignment.timeclose : assignment.duedate;
  var hasDL = tClose && tClose > 0 && isInSemester(tClose, semester);
  var dlDays = hasDL ? daysUntil(tClose) : Infinity;

  return {
    name: assignment.name,
    timeOpen: tOpen || null,
    timeClose: hasDL ? tClose : null,
    deadlineDays: dlDays,
    hasDeadline: hasDL,
    isTest: !!isTest,
    isOpenSoon: isOpenSoon(tOpen)
  };
}

function classifyItem(item) {
  if (item.isTest) {
    var hasTiming = item.timeOpen || item.timeClose;
    if (!hasTiming) return 'hide';
    if (item.timeClose && item.timeClose * 1000 < Date.now()) return 'hide';
    var isOpen = !item.timeOpen || item.timeOpen * 1000 < Date.now();
    if (isOpen) return 'show';
    if (item.isOpenSoon) return 'show';
    if (item.hasDeadline && item.deadlineDays <= 7) return 'show';
    return 'hide';
  }
  if (item.hasDeadline && item.deadlineDays <= 7) return 'show';
  if (!item.hasDeadline) return 'nodeadline';
  return 'hide';
}


function abbreviateName(name) {
  var s = name;
  s = s.replace(/лабораторн[^\s]*(?:\s+робот[^\s]*|\s+робіт)?\s*№?\s*(\d+)/gi, 'ЛР$1');
  s = s.replace(/самостійн[^\s]*(?:\s+робот[^\s]*|\s+робіт)?\s*№?\s*(\d+)/gi, 'СР$1');
  if (s !== name) s = s.replace(/\s+/g, ' ').trim();
  return s;
}

function trimName(name) {
  var s = abbreviateName(name);
  if (s.length <= MAX_NAME_LEN) return s;
  return s.substring(0, MAX_NAME_LEN - 3) + '...';
}

// =============================================================
// SORTING
// =============================================================

function numFromName(n) {
  var m = n.match(/\d+/);
  return m ? parseInt(m[0], 10) : 999999;
}

function byNumber(a, b) {
  var na = numFromName(a.name), nb = numFromName(b.name);
  if (na !== nb) return na - nb;
  return a.name.localeCompare(b.name);
}

function byUrgency(a, b) {
  return a.deadlineDays - b.deadlineDays;
}

function detectType(name) {
  var n = name.toLowerCase();
  if (n.indexOf('лаборатор') !== -1) return 'lab';
  if (n.indexOf('самостійн') !== -1) return 'self';
  if (n.indexOf('тест') !== -1) return 'test';
  if (n.indexOf('контрольн') !== -1) return 'control';
  if (n.indexOf('модуль') !== -1) return 'module';
  if (n.indexOf('курсов') !== -1) return 'coursework';
  if (n.indexOf('реферат') !== -1) return 'essay';
  if (n.indexOf('практичн') !== -1) return 'practical';
  return 'other';
}

var TYPE_ORDER = ['lab','self','test','control','practical','coursework','essay','module','other'];


// =============================================================
// FORMATTING
// =============================================================

function pad2(n) { return ('0' + n).slice(-2); }

function fmtDate(d) {
  return pad2(d.getDate()) + '.' + pad2(d.getMonth()+1) + ' ' + pad2(d.getHours()) + ':' + pad2(d.getMinutes());
}

function fmtShow(item) {
  var label = trimName(item.name);
  if (item.isTest) {
    var parts = [];
    if (item.timeOpen && item.timeOpen * 1000 > Date.now())
      parts.push('🔓 ' + fmtDate(new Date(item.timeOpen * 1000)));
    else
      parts.push('відкрито');
    if (item.timeClose && item.timeClose * 1000 > Date.now())
      parts.push('до ' + fmtDate(new Date(item.timeClose * 1000)));
    return label + ' | ' + parts.join(' ');
  }
  if (item.timeClose) {
    var s = label + ' | ' + fmtDate(new Date(item.timeClose * 1000));
    if (item.deadlineDays < 0) s += ' ⚠️';
    else s += ' (' + item.deadlineDays + 'д)';
    return s;
  }
  return label + ' | (без д/н)';
}

function fmtNodeadline(item) {
  return trimName(item.name) + ' | (без д/н)';
}


// =============================================================
// REPORT
// =============================================================

function buildReport(courses) {
  var lines = [], total = 0;
  var d = new Date();
  lines.push('📚 НУБіП — звіт по завданнях');
  lines.push(pad2(d.getDate()) + '.' + pad2(d.getMonth()+1) + '.' + d.getFullYear());
  lines.push('');

  for (var ci = 0; ci < courses.length; ci++) {
    var course = courses[ci];
    var items = course.items;
    var show = [], nodead = [];

      for (var i = 0; i < items.length; i++) {
      var cls = classifyItem(items[i]);
      if (cls === 'show') show.push(items[i]);
      else if (cls === 'nodeadline') nodead.push(items[i]);
    }

    if (show.length === 0 && nodead.length === 0) continue;

    lines.push('▸ ' + course.name);
    show.sort(byUrgency);
    for (var i = 0; i < show.length; i++) { lines.push(fmtShow(show[i])); total++; }

    if (nodead.length > 0) {
      nodead.sort(byNumber);
      var groups = {};
      for (var i = 0; i < nodead.length; i++) {
        var t = detectType(nodead[i].name);
        if (!groups[t]) groups[t] = [];
        groups[t].push(nodead[i]);
      }
      for (var ti = 0; ti < TYPE_ORDER.length; ti++) {
        var g = groups[TYPE_ORDER[ti]];
        if (!g) continue;
        var n = Math.min(g.length, 2);
        for (var si = 0; si < n; si++) { lines.push(fmtNodeadline(g[si])); total++; }
        if (g.length > 2) lines.push('⋯');
      }
    }
    lines.push('');
  }

  lines.push('✉️ ' + total + ' завдань показано');
  return lines.join('\n');
}


// =============================================================
// TELEGRAM
// =============================================================

function sendTelegram(text) {
  if (!TELEGRAM_BOT_TOKEN || TELEGRAM_CHAT_ID.length === 0) {
    Logger.log('Telegram не налаштовано. Звіт тільки в лог:');
    Logger.log(text);
    return;
  }
  for (var ci = 0; ci < TELEGRAM_CHAT_ID.length; ci++) {
    var r = UrlFetchApp.fetch('https://api.telegram.org/bot' + TELEGRAM_BOT_TOKEN + '/sendMessage', {
      method: 'post',
      payload: { chat_id: TELEGRAM_CHAT_ID[ci], text: text },
      muteHttpExceptions: true
    });
    var j = JSON.parse(r.getContentText());
    if (!j.ok) Logger.log('Telegram помилка для ' + TELEGRAM_CHAT_ID[ci] + ': ' + JSON.stringify(j));
  }
}


// =============================================================
// MAIN
// =============================================================

function reportAssignments() {
  var semester = getSemester();
  if (!semester) {
    sendTelegram('📚 НУБіП — канікули, звіт не потрібен.');
    return;
  }
  Logger.log(semester.label);

  try {
    ensureToken();
    var courses = getCourses();
    if (!courses || courses.length === 0) { sendTelegram('Курсів не знайдено'); return; }

    var courseData = [];
    for (var ci = 0; ci < courses.length; ci++) {
      var c = courses[ci];
      if (c.hidden) continue;
      var items = [];
      var as = getAssignments(c.id);
      for (var ai = 0; ai < as.length; ai++) {
        var item = classify(as[ai], semester, false);
        if (classifyItem(item) === 'hide') continue;
        if (isSubmitted(as[ai].id)) continue;
        items.push(item);
      }
      try {
        var qs = getQuizzes(c.id);
        for (var qi = 0; qi < qs.length; qi++)
          items.push(classify(qs[qi], semester, true));
      } catch (e) {}

      if (items.length > 0) courseData.push({ name: trimName(c.fullname), items: items });
    }

    var report = buildReport(courseData);
    Logger.log(report);
    sendTelegram(report);

  } catch (e) {
    Logger.log('Помилка: ' + e.message);
    sendTelegram('❌ НУБіП — помилка: ' + e.message);
  }
}
