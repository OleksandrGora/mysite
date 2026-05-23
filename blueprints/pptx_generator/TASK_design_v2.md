# Технічне завдання: LLM-генерація нових дизайнів PPTX

## Концепція

LLM отримує вміст слайдів → аналізує тему, тональність, наявність табличних/числових даних →
генерує 3 повністю різних дизайни у вигляді JSON → користувач обирає → Python будує `.pptx`.

Жодних фіксованих шаблонів. Кожен дизайн — унікальний набір параметрів.

---

## JSON-схема дизайну (Design Config)

LLM генерує об'єкт наступної структури:

```json
{
  "id": "dark_contrast",
  "name": "Dark Contrast",
  "rationale": "Темний фон з яскравим акцентом підкреслює авторитетність теми",

  "global": {
    "background_color": "#1A1A2E",
    "invert": true,
    "font_family": "Calibri",
    "mirror_even_slides": true
  },

  "title": {
    "font_size": 34,
    "font_bold": true,
    "font_color": "#FFFFFF",
    "alignment": "left",
    "position": { "x": 0.05, "y": 0.05, "w": 0.90, "h": 0.13 }
  },

  "body": {
    "font_size": 20,
    "font_bold": false,
    "font_color": "#DDDDDD",
    "alignment": "left",
    "position": { "x": 0.05, "y": 0.22, "w": 0.50, "h": 0.70 }
  },

  "image": {
    "position": { "x": 0.58, "y": 0.22, "w": 0.38, "h": 0.70 }
  },

  "shapes": [
    {
      "type": "rectangle",
      "position": { "x": 0.0, "y": 0.0, "w": 0.04, "h": 1.0 },
      "color": "#E94560",
      "gradient": null,
      "z_order": 0
    },
    {
      "type": "rectangle",
      "position": { "x": 0.0, "y": 0.88, "w": 1.0, "h": 0.12 },
      "color": null,
      "gradient": {
        "angle": 0,
        "color_start": "#E94560",
        "color_end": "#1A1A2E"
      },
      "z_order": 1
    }
  ],

  "table_style": {
    "header_bg": "#E94560",
    "header_font_color": "#FFFFFF",
    "row_bg_odd": "#2A2A3E",
    "row_bg_even": "#1A1A2E",
    "cell_font_color": "#DDDDDD",
    "font_size": 16,
    "border_color": "#444466"
  },

  "chart_style": {
    "type": "bar",
    "colors": ["#E94560", "#0F3460", "#533483"],
    "background": "#1A1A2E",
    "font_color": "#FFFFFF"
  }
}
```

### Пояснення полів

**`global`**
- `background_color` — hex колір фону слайду (може бути темним або світлим)
- `invert` — якщо `true`, схема темна (темний фон, світлий текст); якщо `false` — світла
- `font_family` — назва шрифту (тільки стандартні Office: `Calibri`, `Arial`, `Georgia`, `Trebuchet MS`, `Verdana`, `Times New Roman`, `Tahoma`)
- `mirror_even_slides` — якщо `true`, на парних слайдах зображення і тіло міняються місцями, фігури дзеркаляться по горизонталі

**`title` / `body` / `image`**
- `position` — координати в частках від розміру слайду (0.0–1.0), де `x`, `y` — ліво-верх, `w`, `h` — ширина і висота
- Поля `title`, `body`, `image` описують зони. Python підставляє реальний текст і зображення.

**`shapes`** — список декоративних фігур
- `type`: `rectangle`, `oval`, `right_triangle`
- `position`: координати (0.0–1.0)
- `color`: суцільний колір (hex) або `null` якщо градієнт
- `gradient`: об'єкт з `angle`, `color_start`, `color_end` або `null`
- `z_order`: порядок накладання (0 = найнижче)

**`table_style`** — стиль таблиці (застосовується тільки якщо слайд містить таблицю)

**`chart_style`** — стиль діаграми (застосовується тільки якщо слайд містить діаграму)
- `type`: `bar`, `line`, `pie`

---

## Розширення JSON слайдів (вхідний формат)

Існуючий формат залишається. Додається опціональне поле `content_type`:

```json
{
  "slide_number": 1,
  "title": "Назва слайду",
  "body": "Основний текст",
  "image_description": "...",
  "content_type": "text"
}
```

`content_type` може бути:
- `"text"` — звичайний текст (за замовчуванням, якщо поле відсутнє)
- `"table"` — `body` містить табличні дані у вигляді markdown-таблиці або CSV-рядків
- `"chart"` — `body` містить числові дані для діаграми у форматі:
  ```
  Категорія1: 45
  Категорія2: 30
  Категорія3: 25
  ```

> LLM, яка генерує вміст слайдів (перший етап), вже проставляє `content_type`. Якщо поле відсутнє — вважати `"text"`.

---

## Зміни у `routes.py`

### Новий endpoint: `POST /api/suggest-design`

**Вхід:**
```json
{
  "slides": [...],
  "provider": "google"
}
```

**Логіка:**
1. Витягнути з кожного слайду: `title`, перші 200 символів `body`, `content_type`
2. Сформувати `slides_summary` (рядок)
3. Викликати LLM з промптом (див. нижче)
4. Розпарсити JSON-масив з 3 дизайнами
5. Повернути відповідь

**Вихід:**
```json
{
  "designs": [ <3 об'єкти Design Config> ],
  "tokens": { "input_tokens": 0, "output_tokens": 0, "total_tokens": 0 }
}
```

**Обробка помилок:**
- Якщо LLM повернула невалідний JSON — повторити запит один раз
- Якщо повторний запит провалився — повернути `{"error": "...", "designs": []}`
- Фронт при `designs: []` показує toast і продовжує генерацію з random дизайном

### Зміни в `POST /api/generate`

Прийняти додатковий параметр у тілі запиту або як окремий JSON-поле разом зі слайдами:

```json
{
  "slides": [...],
  "design_config": { <Design Config об'єкт або null> }
}
```

Якщо `design_config` присутній — передати у `create_presentation(..., design_config=design_config)`.

> Увага: зараз `/api/generate` приймає raw body як JSON масив слайдів. Потрібно або обгорнути у об'єкт `{"slides": [...], "design_config": {...}}`, або передавати `design_config` як окремий query-параметр (JSON-encoded). Обрати варіант і узгодити з фронтом.

---

## Промпт для LLM (`/api/suggest-design`)

### System prompt:
```
Ти — креативний дизайнер PowerPoint презентацій. Генеруєш параметри дизайну у форматі JSON.
Повертай ТІЛЬКИ валідний JSON масив з 3 об'єктами. Без пояснень, без markdown, без ```json.
```

### User prompt:
```
Проаналізуй тему та зміст слайдів і запропонуй 3 різних дизайни.

СЛАЙДИ:
{slides_summary}

ВИМОГИ ДО 3 ВАРІАНТІВ:
- Варіант 1: світла схема (invert: false), стримана
- Варіант 2: темна схема (invert: true), контрастна
- Варіант 3: твій вибір — підходить до теми, можеш бути сміливим

Кожен варіант має СУТТЄВО відрізнятися від інших:
різний фон, різні позиції фігур, різна типографіка, різне розміщення тексту і зображення.

ЗАОХОЧУЄТЬСЯ (використовуй як натхнення, не копіюй):
- заголовок знизу слайду, великий шрифт — тіло і зображення займають верхню частину
- вузька вертикальна смуга тексту зліва (w: 0.30), велике зображення справа
- темний трикутник або овал у куті займає третину слайду, текст накладається поверх
- зображення зверху (y: 0.05), заголовок і текст знизу (y > 0.55)
- два тонких прямокутники як рамка (зверху і знизу) замість суцільного фону
- заголовок по центру (alignment: center), великий font_size (38–44), тіло дрібніше (16–18)
- фігура-акцент займає всю ліву половину (w: 0.45), текст і зображення справа

ОБМЕЖЕННЯ:
- Координати position: x, y, w, h — від 0.0 до 1.0 (частка від розміру слайду)
- Слайд 16:9, тому h=1.0 при w=0.5 — це висока вертикальна смуга, враховуй пропорції
- Зони title, body, image НЕ перекриваються між собою — перевір координати
- Фігури (shapes) мають z_order=0..1, текстові зони завжди поверх фігур
- font_family — тільки: Calibri, Arial, Georgia, Trebuchet MS, Verdana, Times New Roman, Tahoma
- font_size для title: 24–44, для body: 14–26
- shapes: від 1 до 4 фігур (rectangle, oval, right_triangle)
- Якщо є слайди з content_type "table" або "chart" — додай table_style / chart_style

ФОРМАТ ВІДПОВІДІ (JSON масив з 3 об'єктів):
[
  {
    "id": "...",
    "name": "...",
    "rationale": "...",
    "global": { "background_color": "...", "invert": true/false, "font_family": "...", "mirror_even_slides": true/false },
    "title": { "font_size": N, "font_bold": true/false, "font_color": "...", "alignment": "left/center/right", "position": {"x":N,"y":N,"w":N,"h":N} },
    "body": { "font_size": N, "font_bold": false, "font_color": "...", "alignment": "left/center/right", "position": {"x":N,"y":N,"w":N,"h":N} },
    "image": { "position": {"x":N,"y":N,"w":N,"h":N} },
    "shapes": [ { "type": "...", "position": {"x":N,"y":N,"w":N,"h":N}, "color": "..." or null, "gradient": {"angle":N,"color_start":"...","color_end":"..."} or null, "z_order": N } ],
    "table_style": { ... } or null,
    "chart_style": { ... } or null
  },
  ...
]
```

---

## Валідація і автокорекція зон (`routes.py`)

Після отримання JSON від LLM і до повернення відповіді клієнту — запустити валідатор на кожному з 3 дизайнів.

### `_validate_and_fix_zones(design)`

```python
def _validate_and_fix_zones(design):
    """
    Перевіряє що title, body, image не перекриваються.
    При конфлікті — автоматично коригує координати.
    Повертає (design, was_fixed, conflicts_log).
    """
    zones = ['title', 'body', 'image']
    conflicts_log = []
    was_fixed = False

    for i in range(len(zones)):
        for j in range(i + 1, len(zones)):
            a_key, b_key = zones[i], zones[j]
            a = design[a_key]['position']
            b = design[b_key]['position']

            if _rects_overlap(a, b):
                conflicts_log.append(f"{a_key} ↔ {b_key}")
                design[b_key]['position'] = _resolve_overlap(a, b)
                was_fixed = True

    return design, was_fixed, conflicts_log


def _rects_overlap(a, b):
    return not (
        a['x'] + a['w'] <= b['x'] or
        b['x'] + b['w'] <= a['x'] or
        a['y'] + a['h'] <= b['y'] or
        b['y'] + b['h'] <= a['y']
    )


def _resolve_overlap(fixed, moving):
    """
    Зсуває 'moving' вниз або вправо щоб прибрати перекриття з 'fixed'.
    Обирає напрямок з меншим зміщенням.
    Клампує результат в межі 0.0–1.0.
    """
    # Варіант 1: зсунути вниз
    gap_y = (fixed['y'] + fixed['h']) - moving['y']
    # Варіант 2: зсунути вправо
    gap_x = (fixed['x'] + fixed['w']) - moving['x']

    new = dict(moving)
    if abs(gap_y) <= abs(gap_x):
        new['y'] = min(fixed['y'] + fixed['h'] + 0.01, 1.0 - moving['h'])
    else:
        new['x'] = min(fixed['x'] + fixed['w'] + 0.01, 1.0 - moving['w'])

    # Клампування
    new['x'] = max(0.0, min(new['x'], 1.0 - new['w']))
    new['y'] = max(0.0, min(new['y'], 1.0 - new['h']))
    return new
```

**Інтеграція в `/api/suggest-design`:**

```python
fixed_designs = []
for design in designs:
    design, was_fixed, conflicts = _validate_and_fix_zones(design)
    if was_fixed:
        logger.warning(f"Design '{design['id']}': auto-fixed overlaps: {conflicts}")
    fixed_designs.append(design)
```

Логувати конфлікти, але не повертати помилку — автокорекція прозора для користувача.

---

## Зміни у `create_pptx.py`

### Сигнатура `create_presentation`

```python
def create_presentation(filename, slides_data, images_dir=None, design_config=None):
```

Якщо `design_config is None` — поведінка як зараз (random).

### Допоміжна функція `_apply_design_config`

```python
def _apply_design_config(prs, slide, slide_data, design_config, slide_index):
```

Викликається замість існуючих `_add_background_*` функцій якщо `design_config` переданий.

**Що робить:**

1. **Фон слайду** — зафарбувати прямокутник на весь слайд кольором `global.background_color`

2. **Фігури** — пройти по `design_config["shapes"]`, для кожної:
   - Визначити реальні координати з часток (помножити на `slide_width`/`slide_height`)
   - Якщо `mirror_even_slides` і слайд парний — дзеркалити `x` → `1.0 - x - w`
   - Додати фігуру через `slide.shapes.add_shape(...)`
   - Застосувати `color` або `gradient`

3. **Заголовок** — розмістити textbox за `title.position`, застосувати шрифт, колір, вирівнювання

4. **Тіло або таблиця або діаграма** — залежно від `slide_data.get("content_type", "text")`:
   - `"text"` → textbox за `body.position`
   - `"table"` → `_render_table(slide, slide_data["body"], design_config["table_style"], body_pos)`
   - `"chart"` → `_render_chart(slide, slide_data["body"], design_config["chart_style"], body_pos)`

5. **Зображення** — якщо є `image_path`, розмістити за `image.position`; якщо є `image_description` — заглушка

### Функція `_render_table`

```python
def _render_table(slide, body_text, table_style, position):
```

- Парсить `body_text` як markdown-таблицю (`| col1 | col2 |`) або CSV-рядки
- Додає таблицю через `slide.shapes.add_table(rows, cols, left, top, width, height)`
- Застосовує `table_style`: колір хедера, чергування рядків, шрифт, бордер

### Функція `_render_chart`

```python
def _render_chart(slide, body_text, chart_style, position):
```

- Парсить `body_text` формату `Категорія: значення` (по одному на рядок)
- Будує діаграму через `pptx.chart` API:
  - `chart_style["type"]` → `XL_CHART_TYPE.BAR_CLUSTERED` / `LINE` / `PIE`
  - `chart_style["colors"]` → серії
- Розміщує за `position`

---

## Зміни у `index-pptx-generator.html`

### 1. Галочка

Додати в `.provider-row` поруч з існуючою галочкою «Генерувати зображення»:

```html
<label class="checkbox-label">
  <input type="checkbox" id="suggest-design">
  ✨ Запропонувати дизайн
</label>
```

### 2. Модальне вікно

Прихований `<div id="design-modal">` з overlay. З'являється після отримання варіантів.

Структура:
```
[Overlay]
  [Modal]
    Заголовок: "Оберіть дизайн"
    [3 картки в ряд]
      [Картка]
        SVG-прев'ю (220×140px)
        Назва
        Rationale (курсив, сірий)
        [Кнопка "Обрати"]
    [Кнопка "Скасувати"]
```

На мобільних (< 700px) картки йдуть вертикально.

### 3. SVG-прев'ю `renderDesignSVG(design)`

Функція повертає SVG 220×140px що схематично відображає дизайн:

```javascript
function renderDesignSVG(design) {
  const W = 220, H = 140;
  const bg = design.global.background_color;
  const titlePos = design.title.position;
  const bodyPos = design.body.position;
  const imgPos = design.image.position;

  let shapesSVG = '';
  for (const shape of design.shapes) {
    const x = shape.position.x * W;
    const y = shape.position.y * H;
    const w = shape.position.w * W;
    const h = shape.position.h * H;
    const fill = shape.color || shape.gradient?.color_start || '#888';

    if (shape.type === 'rectangle') {
      shapesVG += `<rect x="${x}" y="${y}" width="${w}" height="${h}" fill="${fill}" opacity="0.85"/>`;
    } else if (shape.type === 'oval') {
      shapesVG += `<ellipse cx="${x+w/2}" cy="${y+h/2}" rx="${w/2}" ry="${h/2}" fill="${fill}" opacity="0.85"/>`;
    } else if (shape.type === 'right_triangle') {
      shapesVG += `<polygon points="${x},${y} ${x+w},${y+h} ${x},${y+h}" fill="${fill}" opacity="0.85"/>`;
    }
  }

  const titleColor = design.title.font_color;
  const bodyColor = design.body.font_color;

  return `<svg width="${W}" height="${H}" viewBox="0 0 ${W} ${H}" xmlns="http://www.w3.org/2000/svg">
    <rect width="${W}" height="${H}" fill="${bg}"/>
    ${shapesVG}
    <!-- title placeholder -->
    <rect x="${titlePos.x*W}" y="${titlePos.y*H}" width="${titlePos.w*W}" height="${titlePos.h*H*0.4}"
          fill="${titleColor}" opacity="0.8" rx="2"/>
    <!-- body lines -->
    ${[0,1,2].map(i => `<rect x="${bodyPos.x*W}" y="${bodyPos.y*H + i*10}" width="${bodyPos.w*W*(0.9-i*0.1)}" height="5"
          fill="${bodyColor}" opacity="0.4" rx="1"/>`).join('')}
    <!-- image placeholder -->
    <rect x="${imgPos.x*W}" y="${imgPos.y*H}" width="${imgPos.w*W}" height="${imgPos.h*H}"
          fill="#888" opacity="0.2" rx="3"/>
    <text x="${imgPos.x*W + imgPos.w*W/2}" y="${imgPos.y*H + imgPos.h*H/2}"
          text-anchor="middle" dominant-baseline="middle" font-size="10" fill="${bodyColor}" opacity="0.4">🖼</text>
  </svg>`;
}
```

### 4. JS-флоу

```javascript
async function generatePPTX() {
  const suggestDesign = document.getElementById('suggest-design').checked;
  let designConfig = null;

  if (suggestDesign) {
    let slides;
    try { slides = JSON.parse(jsonInput); }
    catch { /* показати помилку */ return; }

    logLine('Аналізую тему, пропоную дизайни...');
    const result = await fetchDesignSuggestions(slides, provider);

    if (!result || !result.designs?.length) {
      showToast('Не вдалось отримати варіанти дизайну. Продовжую з випадковим.', 'warning');
    } else {
      addTokensToCounter(result.tokens);
      designConfig = await showDesignModal(result.designs); // Promise<design|null>
      if (designConfig === null) {
        // Користувач скасував
        resetButton();
        return;
      }
    }
  }

  // Продовжити генерацію з designConfig (або null = random)
  startGeneration(designConfig);
}

async function fetchDesignSuggestions(slides, provider) {
  const resp = await fetch(`${API_BASE}/api/suggest-design`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ slides, provider })
  });
  return resp.ok ? resp.json() : null;
}

function showDesignModal(designs) {
  return new Promise((resolve) => {
    // Рендер 3 карток з SVG
    // Кожна кнопка "Обрати" → resolve(design)
    // Кнопка "Скасувати" → resolve(null)
    // Показати модаль
  });
}
```

`startGeneration(designConfig)` — існуюча логіка `generatePPTX`, куди додається передача `design_config` у запит до `/api/generate`.

---

## Порядок реалізації

1. **`create_pptx.py`**
   - Додати `design_config=None` у `create_presentation`
   - Реалізувати `_apply_design_config` (фон + фігури + текст + зображення)
   - Реалізувати `_render_table`
   - Реалізувати `_render_chart`

2. **`routes.py`**
   - Новий endpoint `POST /api/suggest-design` з промптом і парсингом JSON
   - Оновити `POST /api/generate` — прийняти `design_config`, передати у `create_presentation`

3. **`index-pptx-generator.html`**
   - Галочка → модаль → SVG-прев'ю → вибір → генерація з `design_config`

---

## Важливо

- Існуючий флоу (без галочки) — не чіпати, random як зараз
- Координати у JSON завжди 0.0–1.0, Python перераховує в EMU
- Дзеркалювання парних слайдів: `x_mirrored = 1.0 - x - w` для фігур і зон
- Якщо `content_type` відсутній або `"text"` — рендерити текст (не таблицю/діаграму)
- Таблицю і діаграму рендерити замість textbox тіла, у тих же координатах `body.position`
- Валідатор зон запускати на сервері до відповіді клієнту — автокорекція прозора для користувача
- Конфлікти зон логувати через `logger.warning` для діагностики якості промпту
