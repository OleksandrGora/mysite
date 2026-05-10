from pptx import Presentation
from pptx.util import Inches, Pt, Emu
from pptx.dml.color import RGBColor
from pptx.enum.dml import MSO_THEME_COLOR
from pptx.enum.shapes import MSO_SHAPE
from datetime import datetime
import os
import random
import math

# 4. Створення презентації
def create_presentation(filename, slides_data, images_dir=None):
    """
    Створення PowerPoint презентації з JSON даних.
    
    Args:
        filename: шлях до вихідного файлу .pptx
        slides_data: список словників з ключами:
            - title: назва слайду
            - body: основний текст
            - image_description: опис ілюстрації (опціонально)
        images_dir: папка з згенерованими зображеннями (опціонально)
    """
    prs = Presentation()
    
    # Світлі пастельні градієнти для всієї презентації (у 2 рази світліші)
    gradient_colors = [
        (RGBColor(235, 245, 255), RGBColor(225, 240, 250)),  # Блакитний
        (RGBColor(235, 255, 240), RGBColor(225, 250, 235)),  # М'ятний
        (RGBColor(250, 240, 255), RGBColor(245, 235, 250)),  # Лавандовий
        (RGBColor(255, 248, 235), RGBColor(250, 245, 230)),  # Кремовий
        (RGBColor(255, 240, 245), RGBColor(250, 235, 240)),  # Рожевий
        (RGBColor(240, 252, 255), RGBColor(235, 250, 252)),  # Бірюзовий
        (RGBColor(245, 255, 245), RGBColor(240, 252, 240)),  # Світло-зелений
        (RGBColor(255, 250, 245), RGBColor(252, 245, 240)),  # Персиковий
        (RGBColor(255, 248, 252), RGBColor(252, 245, 250)),  # Світло-рожевий
        (RGBColor(248, 252, 255), RGBColor(245, 250, 252)),  # Аліс-блакитний
        (RGBColor(250, 255, 252), RGBColor(248, 252, 250)),  # М'ятно-білий
        (RGBColor(255, 252, 248), RGBColor(252, 250, 245)),  # Крем-брюле
        (RGBColor(245, 250, 255), RGBColor(240, 248, 252)),  # Світло-лавандовий
        (RGBColor(255, 248, 245), RGBColor(252, 245, 240)),  # Світло-кораловий
        (RGBColor(248, 255, 248), RGBColor(245, 252, 245)),  # Медовий роси
    ]
    
    # Контрастні кольори для заголовків (працюють з пастельними фонами)
    title_colors = [
        RGBColor(40, 40, 60),    # Темно-синій
        RGBColor(50, 50, 50),    # Темно-сірий
        RGBColor(60, 40, 60),    # Темно-пурпурний
        RGBColor(80, 50, 40),    # Темно-коричневий
        RGBColor(40, 60, 50),    # Темно-зелений
    ]
    
    # Випадково вибираємо ОДИН градієнт для ВСІХ слайдів
    selected_gradient = random.choice(gradient_colors)
    color1, color2 = selected_gradient
    
    # Доступні варіанти для прямокутників
    rectangle_layout_options = ['full', 'left_half', 'right_half']
    
    # Доступні варіанти для кіл (4 опції)
    circle_layout_options = ['corner_top_left', 'corner_top_right', 'corner_bottom_left', 'corner_bottom_right']
    
    # Доступні варіанти для сайдбару
    sidebar_layout_options = ['sidebar_left', 'sidebar_right']
    
    # Доступні варіанти для хедера/футера
    header_bar_layout_options = ['header_bar', 'footer_bar']
    
    # Create title slide with white background
    slide_layout = prs.slide_layouts[6]  # Blank layout
    title_slide = prs.slides.add_slide(slide_layout)
    
    # White background for title slide
    bg_shape = title_slide.shapes.add_shape(
        MSO_SHAPE.RECTANGLE,
        0, 0,
        prs.slide_width, prs.slide_height
    )
    bg_shape.z_order = 0
    # Remove border
    bg_shape.line.fill.background()
    fill = bg_shape.fill
    fill.solid()
    fill.fore_color.rgb = RGBColor(255, 255, 255)  # White
    # Lock shape
    _lock_shape(bg_shape)
    
    # Get presentation title from first slide
    presentation_title = slides_data[0].get('title', 'Презентація') if slides_data else 'Презентація'
    current_year = datetime.now().year
    
    # Випадковий колір заголовка для всієї презентації
    selected_title_color = random.choice(title_colors)
    
    # ТЕМА - centered, slightly above center, font 30
    tema_box = title_slide.shapes.add_textbox(
        Inches(0.5), Inches(2.5),
        Inches(9), Inches(0.8)
    )
    tema_frame = tema_box.text_frame
    tema_frame.text = presentation_title
    tema_frame.margin_left = 0
    tema_frame.margin_right = 0
    tema_frame.margin_top = 0
    tema_frame.margin_bottom = 0
    tema_frame.word_wrap = True
    tema_frame.auto_size = 1  # Auto resize text to fit shape
    tema_para = tema_frame.paragraphs[0]
    tema_para.font.size = Pt(30)
    tema_para.font.bold = True
    tema_para.font.color.rgb = selected_title_color
    tema_para.alignment = 1  # Center
    
    # Дисципліна - below ТЕМА, center, font 27
    disc_box = title_slide.shapes.add_textbox(
        Inches(0.5), Inches(3.5),
        Inches(9), Inches(0.7)
    )
    disc_frame = disc_box.text_frame
    disc_frame.text = "Назва дисципліни"
    disc_frame.margin_left = 0
    disc_frame.margin_right = 0
    disc_frame.margin_top = 0
    disc_frame.margin_bottom = 0
    disc_frame.word_wrap = True
    disc_frame.auto_size = 1  # Auto resize text to fit shape
    disc_para = disc_frame.paragraphs[0]
    disc_para.font.size = Pt(27)
    disc_para.font.color.rgb = selected_title_color
    disc_para.alignment = 1  # Center
    
    # Виконав - below, right aligned, font 25
    exec_box = title_slide.shapes.add_textbox(
        Inches(5), Inches(5),
        Inches(4.5), Inches(0.6)
    )
    exec_frame = exec_box.text_frame
    exec_frame.text = "Виконав студент групи ___"
    exec_frame.margin_left = 0
    exec_frame.margin_right = 0
    exec_frame.margin_top = 0
    exec_frame.margin_bottom = 0
    exec_frame.word_wrap = True
    exec_frame.auto_size = 1  # Auto resize text to fit shape
    exec_para = exec_frame.paragraphs[0]
    exec_para.font.size = Pt(25)
    exec_para.font.color.rgb = selected_title_color
    exec_para.alignment = 2  # Right
    
    # Перевірив - below, right aligned, font 25
    check_box = title_slide.shapes.add_textbox(
        Inches(5), Inches(5.7),
        Inches(4.5), Inches(0.6)
    )
    check_frame = check_box.text_frame
    check_frame.text = "Перевірив _____________"
    check_frame.margin_left = 0
    check_frame.margin_right = 0
    check_frame.margin_top = 0
    check_frame.margin_bottom = 0
    check_frame.word_wrap = True
    check_frame.auto_size = 1  # Auto resize text to fit shape
    check_para = check_frame.paragraphs[0]
    check_para.font.size = Pt(25)
    check_para.font.color.rgb = selected_title_color
    check_para.alignment = 2  # Right
    
    # Київ {year} - bottom center, font 20
    city_box = title_slide.shapes.add_textbox(
        Inches(0.5), Inches(6.8),
        Inches(9), Inches(0.5)
    )
    city_frame = city_box.text_frame
    city_frame.text = f"Київ {current_year}"
    city_frame.margin_left = 0
    city_frame.margin_right = 0
    city_frame.margin_top = 0
    city_frame.margin_bottom = 0
    city_frame.word_wrap = True
    city_frame.auto_size = 1  # Auto resize text to fit shape
    city_para = city_frame.paragraphs[0]
    city_para.font.size = Pt(20)
    city_para.font.color.rgb = selected_title_color
    city_para.alignment = 1  # Center
    
    # Choose design type once for the entire presentation (except title slide)
    design_type = random.choice(['rectangle', 'circle', 'sidebar', 'header_bar', 'diagonal_split', 'triangle_accents', 'thin_frame'])
    rectangle_layout = None
    sidebar_layout = None
    header_bar_layout = None
    
    # If rectangle design, choose layout once for consistency
    if design_type == 'rectangle':
        rectangle_layout = random.choice(rectangle_layout_options)
    elif design_type == 'sidebar':
        sidebar_layout = random.choice(sidebar_layout_options)
    elif design_type == 'header_bar':
        header_bar_layout = random.choice(header_bar_layout_options)
    
    # Content slides
    slide_count = 0
    for slide_data in slides_data:
        title_text = slide_data.get('title', '')
        body_text = slide_data.get('body', '')
        image_description = slide_data.get('image_description', '')
        image_path = slide_data.get('image_path', '')
        
        # Using empty layout (6) for full control
        slide_layout = prs.slide_layouts[6]
        slide = prs.slides.add_slide(slide_layout)
        slide_count += 1
        
        # Apply background design based on chosen type
        if design_type == 'rectangle':
            _add_background_rectangles(slide, prs.slide_width, prs.slide_height, color1, color2, rectangle_layout)
        elif design_type == 'circle':
            circle_layout = random.choice(circle_layout_options)
            _add_background_circles(slide, prs.slide_width, prs.slide_height, color1, color2, circle_layout)
        elif design_type == 'sidebar':
            _add_sidebar(slide, prs.slide_width, prs.slide_height, color1, color2, sidebar_layout)
        elif design_type == 'header_bar':
            _add_header_bar(slide, prs.slide_width, prs.slide_height, color1, color2, header_bar_layout)
        elif design_type == 'diagonal_split':
            _add_diagonal_split(slide, prs.slide_width, prs.slide_height, color1, color2)
        elif design_type == 'triangle_accents':
            _add_triangle_accents(slide, prs.slide_width, prs.slide_height, color1, color2)
        elif design_type == 'thin_frame':
            _add_thin_frame(slide, prs.slide_width, prs.slide_height, color1, color2)
        
        # Додаємо заголовок
        if title_text:
            title_box = slide.shapes.add_textbox(Inches(0.5), Inches(0.3), Inches(9), Inches(0.8))
            title_frame = title_box.text_frame
            title_frame.text = title_text
            title_frame.margin_left = 0
            title_frame.margin_right = 0
            title_frame.margin_top = 0
            title_frame.margin_bottom = 0
            title_frame.word_wrap = True
            title_frame.auto_size = 1  # Auto resize text to fit shape
            title_para = title_frame.paragraphs[0]
            title_para.font.size = Pt(32)
            title_para.font.bold = True
            title_para.font.color.rgb = selected_title_color
            title_para.alignment = 1  # Center
        
        # Перевіряємо чи є реальне зображення
        has_image = False
        actual_image_path = None
        if image_path and os.path.exists(image_path):
            has_image = True
            actual_image_path = image_path
        elif image_description and images_dir:
            potential_image = os.path.join(images_dir, f"slide_{slide_count}.png")
            if os.path.exists(potential_image):
                has_image = True
                actual_image_path = potential_image

        # Чи є image_description (потрібне місце для зображення)
        needs_image_space = bool(image_description)

        # Чергування: непарні - справа, парні - зліва
        image_on_right = (slide_count % 2 == 1)

        slide_width = prs.slide_width
        slide_height = prs.slide_height

        # Розміри зон — чітко 50/50 з відступами
        MARGIN = Inches(0.4)
        CONTENT_TOP = Inches(2.0)       # під заголовком
        CONTENT_HEIGHT = Inches(5.0)
        HALF_W = slide_width // 2       # половина слайду в EMU

        # Зона зображення: 50% ширини мінус відступи
        img_zone_w = HALF_W - int(MARGIN * 1.5)
        img_zone_h = CONTENT_HEIGHT

        # Зона тексту: та сама ширина
        text_zone_w = HALF_W - int(MARGIN * 1.5)

        if image_on_right:
            img_x = HALF_W + int(MARGIN * 0.5)
            text_x = int(MARGIN)
        else:
            img_x = int(MARGIN)
            text_x = HALF_W + int(MARGIN * 0.5)

        # Визначаємо позицію та ширину тексту
        if has_image or needs_image_space:
            # Текст займає ліву або праву половину
            final_text_x = text_x
            final_text_w = text_zone_w
        else:
            # Немає зображення — текст на всю ширину
            final_text_x = int(MARGIN)
            final_text_w = slide_width - int(MARGIN * 2)

        # Додаємо текст тіла
        if body_text:
            body_box = slide.shapes.add_textbox(
                final_text_x, CONTENT_TOP, final_text_w, CONTENT_HEIGHT
            )
            body_frame = body_box.text_frame
            body_frame.word_wrap = True
            body_frame.text = body_text
            body_para = body_frame.paragraphs[0]
            body_para.font.size = Pt(25)
            body_para.font.color.rgb = selected_title_color

        # Додавання реального зображення
        if actual_image_path:
            from PIL import Image as PILImage
            with PILImage.open(actual_image_path) as img:
                img_w, img_h = img.size
                aspect_ratio = img_h / img_w

            # Вписуємо зображення в зону img_zone_w x img_zone_h
            pic_width = img_zone_w
            pic_height = int(pic_width * aspect_ratio)
            if pic_height > img_zone_h:
                pic_height = img_zone_h
                pic_width = int(pic_height / aspect_ratio)

            # Центруємо вертикально в зоні
            img_y = CONTENT_TOP + (img_zone_h - pic_height) // 2

            slide.shapes.add_picture(
                actual_image_path, img_x, img_y,
                width=pic_width, height=pic_height
            )

        # Заглушка коли є image_description але немає реального зображення
        elif needs_image_space:
            _add_image_placeholder(
                slide, img_x, CONTENT_TOP, img_zone_w, img_zone_h, image_description
            )

    prs.save(filename)
    return filename


def _add_image_placeholder(slide, ph_x, ph_y, ph_width, ph_height, label_text=''):
    """
    Додає сірий прямокутник-заглушку на місці майбутнього зображення.
    """

    # Прямокутник-заглушка
    ph_shape = slide.shapes.add_shape(
        MSO_SHAPE.RECTANGLE,
        ph_x, ph_y,
        ph_width, ph_height
    )
    ph_fill = ph_shape.fill
    ph_fill.solid()
    ph_fill.fore_color.rgb = RGBColor(220, 220, 220)  # Світло-сірий

    # М'який бордер
    ph_shape.line.color.rgb = RGBColor(180, 180, 180)
    ph_shape.line.width = Pt(1)

    # Підпис всередині заглушки
    from pptx.util import Pt as PtUtil
    tf = ph_shape.text_frame
    tf.word_wrap = True

    # Іконка + текст
    display_text = f"🖼 {label_text}" if label_text else "🖼 Зображення"
    # Обрізаємо якщо дуже довгий
    if len(display_text) > 80:
        display_text = display_text[:77] + '...'

    p = tf.paragraphs[0]
    p.text = display_text
    p.font.size = Pt(11)
    p.font.color.rgb = RGBColor(120, 120, 120)
    p.font.italic = True
    p.alignment = 1  # Center

# Блокування фігур від редагування через XML, не працює
def _lock_shape(shape):
    """Заблокувати фігуру від редагування та переміщення через XML."""
    from pptx.oxml.ns import qn

    sp = shape._element

    sp_locks = sp.find(qn('a:spLocks'))
    if sp_locks is None:
        from lxml import etree
        sp_locks = etree.SubElement(sp, qn('a:spLocks'))

    sp_locks.set('noAdjustHandles', '1')
    sp_locks.set('noChangeArrowheads', '1')
    sp_locks.set('noChangeShapeType', '1')
    sp_locks.set('noEditPoints', '1')
    sp_locks.set('noGrp', '1')
    sp_locks.set('noMove', '1')
    sp_locks.set('noResize', '1')
    sp_locks.set('noRot', '1')
    sp_locks.set('noSelect', '1')


def _add_background_rectangles(slide, slide_width, slide_height, color1, color2, layout):
    """
    Add rectangle background design with gradient fill.
    
    Layout options:
    - full: fill entire slide
    - left_half: fill left half of slide
    - right_half: fill right half of slide
    """
    if layout == 'full':
        rect = slide.shapes.add_shape(
            MSO_SHAPE.RECTANGLE,
            0, 0,
            slide_width, slide_height
        )
        rect.z_order = 0
        rect.line.fill.background()
        
        fill = rect.fill
        fill.gradient()
        fill.gradient_angle = random.choice([0, 45, 90, 135, 180, 225, 270, 315])
        fill.gradient_stops[0].color.rgb = color1
        fill.gradient_stops[1].color.rgb = color2
        
        _lock_shape(rect)
        
    elif layout == 'left_half':
        rect = slide.shapes.add_shape(
            MSO_SHAPE.RECTANGLE,
            0, 0,
            slide_width / 2, slide_height
        )
        rect.z_order = 0
        rect.line.fill.background()
        
        fill = rect.fill
        fill.gradient()
        fill.gradient_angle = random.choice([0, 45, 90, 135, 180, 225, 270, 315])
        fill.gradient_stops[0].color.rgb = color1
        fill.gradient_stops[1].color.rgb = color2
        
        _lock_shape(rect)
        
    elif layout == 'right_half':
        rect = slide.shapes.add_shape(
            MSO_SHAPE.RECTANGLE,
            slide_width / 2, 0,
            slide_width / 2, slide_height
        )
        rect.z_order = 0
        rect.line.fill.background()
        
        fill = rect.fill
        fill.gradient()
        fill.gradient_angle = random.choice([0, 45, 90, 135, 180, 225, 270, 315])
        fill.gradient_stops[0].color.rgb = color1
        fill.gradient_stops[1].color.rgb = color2
        
        _lock_shape(rect)


def _add_background_circles(slide, slide_width, slide_height, color1, color2, layout):
    """
    Add two circles for slide background according to selected layout.
    """
    corner_positions = {
        'corner_top_left': (0, 0),
        'corner_top_right': (slide_width, 0),
        'corner_bottom_left': (0, slide_height),
        'corner_bottom_right': (slide_width, slide_height),
    }

    corner = corner_positions.get(layout, (0, 0))
    c1_center_x, c1_center_y = corner

    c1_radius = slide_height / 2
    c1_diameter = c1_radius * 2

    diagonal_map = {
        (0, 0): (slide_width, slide_height),
        (0, slide_height): (slide_width, 0),
        (slide_width, 0): (0, slide_height),
        (slide_width, slide_height): (0, 0),
    }

    opposite = diagonal_map.get((c1_center_x, c1_center_y), (slide_width, slide_height))
    opp_x, opp_y = opposite

    c2_center_x = opp_x - 0.25 * (opp_x - c1_center_x)
    c2_center_y = opp_y - 0.25 * (opp_y - c1_center_y)

    c2_diameter = slide_width * 0.2
    c2_radius = c2_diameter / 2

    c1_left = c1_center_x - c1_radius
    c1_top = c1_center_y - c1_radius

    circle1 = slide.shapes.add_shape(
        MSO_SHAPE.OVAL,
        c1_left, c1_top,
        c1_diameter, c1_diameter
    )
    circle1.z_order = 0
    circle1.line.fill.background()

    fill1 = circle1.fill
    fill1.gradient()
    fill1.gradient_angle = random.choice([0, 45, 90, 135, 180, 225, 270, 315])
    fill1.gradient_stops[0].color.rgb = color1
    fill1.gradient_stops[1].color.rgb = color2

    _lock_shape(circle1)

    c2_left = c2_center_x - c2_radius
    c2_top = c2_center_y - c2_radius

    circle2 = slide.shapes.add_shape(
        MSO_SHAPE.OVAL,
        c2_left, c2_top,
        c2_diameter, c2_diameter
    )
    circle2.z_order = 1
    circle2.line.fill.background()

    fill2 = circle2.fill
    fill2.gradient()
    fill2.gradient_angle = random.choice([0, 45, 90, 135, 180, 225, 270, 315])
    fill2.gradient_stops[0].color.rgb = color1
    fill2.gradient_stops[1].color.rgb = color2

    _lock_shape(circle2)


def _add_sidebar(slide, slide_width, slide_height, color1, color2, layout):
    """
    Add sidebar design - vertical colored bar on left or right side.
    
    Layout options:
    - sidebar_left: vertical bar on left side (18% width)
    - sidebar_right: vertical bar on right side (18% width)
    """
    sidebar_width = int(slide_width * 0.18)
    
    if layout == 'sidebar_left':
        x = 0
        width = sidebar_width
    else:  # sidebar_right
        x = slide_width - sidebar_width
        width = sidebar_width
    
    sidebar = slide.shapes.add_shape(
        MSO_SHAPE.RECTANGLE,
        x, 0,
        width, slide_height
    )
    sidebar.z_order = 0
    sidebar.line.fill.background()
    
    fill = sidebar.fill
    fill.gradient()
    fill.gradient_angle = random.choice([0, 45, 90, 135, 180, 225, 270, 315])
    fill.gradient_stops[0].color.rgb = color1
    fill.gradient_stops[1].color.rgb = color2
    
    _lock_shape(sidebar)


def _add_header_bar(slide, slide_width, slide_height, color1, color2, layout):
    """
    Add header or footer bar design - horizontal colored band.
    
    Layout options:
    - header_bar: horizontal bar at top (15% height)
    - footer_bar: horizontal bar at bottom (15% height)
    """
    bar_height = int(slide_height * 0.15)
    
    if layout == 'header_bar':
        x = 0
        y = 0
        height = bar_height
    else:  # footer_bar
        x = 0
        y = slide_height - bar_height
        height = bar_height
    
    bar = slide.shapes.add_shape(
        MSO_SHAPE.RECTANGLE,
        x, y,
        slide_width, height
    )
    bar.z_order = 0
    bar.line.fill.background()
    
    fill = bar.fill
    fill.gradient()
    fill.gradient_angle = random.choice([0, 45, 90, 135, 180, 225, 270, 315])
    fill.gradient_stops[0].color.rgb = color1
    fill.gradient_stops[1].color.rgb = color2
    
    _lock_shape(bar)


def _add_diagonal_split(slide, slide_width, slide_height, color1, color2):
    """
    Add diagonal split design - rectangle with diagonal gradient.
    """
    rect = slide.shapes.add_shape(
        MSO_SHAPE.RECTANGLE,
        0, 0,
        slide_width, slide_height
    )
    rect.z_order = 0
    rect.line.fill.background()
    
    fill = rect.fill
    fill.gradient()
    fill.gradient_angle = 45  # Diagonal angle
    fill.gradient_stops[0].color.rgb = color1
    fill.gradient_stops[1].color.rgb = color2
    
    _lock_shape(rect)


def _add_triangle_accents(slide, slide_width, slide_height, color1, color2):
    """
    Add triangle accents in corners - small triangles in top-left and bottom-right.
    """
    triangle_size = int(min(slide_width, slide_height) * 0.15)
    
    # Add triangles in top-left and bottom-right corners
    corner_configs = [
        (0, 0, triangle_size, triangle_size),  # top-left
        (slide_width - triangle_size, slide_height - triangle_size, triangle_size, triangle_size),  # bottom-right
    ]
    
    for x, y, width, height in corner_configs:
        triangle = slide.shapes.add_shape(
            MSO_SHAPE.RIGHT_TRIANGLE,
            x, y,
            width, height
        )
        triangle.z_order = 0
        triangle.line.fill.background()
        
        fill = triangle.fill
        fill.gradient()
        fill.gradient_angle = random.choice([0, 45, 90, 135, 180, 225, 270, 315])
        fill.gradient_stops[0].color.rgb = color1
        fill.gradient_stops[1].color.rgb = color2
        
        _lock_shape(triangle)


def _add_thin_frame(slide, slide_width, slide_height, color1, color2):
    """
    Add thin frame design - colored border around the entire slide.
    Frame width is 0.2 inches.
    """
    frame_width = int(Inches(0.2))  # 0.2 inches frame
    
    # Top frame
    top = slide.shapes.add_shape(
        MSO_SHAPE.RECTANGLE,
        0, 0,
        slide_width, frame_width
    )
    top.z_order = 0
    top.line.fill.background()
    fill = top.fill
    fill.solid()
    fill.fore_color.rgb = color1
    _lock_shape(top)
    
    # Bottom frame
    bottom = slide.shapes.add_shape(
        MSO_SHAPE.RECTANGLE,
        0, slide_height - frame_width,
        slide_width, frame_width
    )
    bottom.z_order = 0
    bottom.line.fill.background()
    fill = bottom.fill
    fill.solid()
    fill.fore_color.rgb = color1
    _lock_shape(bottom)
    
    # Left frame
    left = slide.shapes.add_shape(
        MSO_SHAPE.RECTANGLE,
        0, 0,
        frame_width, slide_height
    )
    left.z_order = 0
    left.line.fill.background()
    fill = left.fill
    fill.solid()
    fill.fore_color.rgb = color1
    _lock_shape(left)
    
    # Right frame
    right = slide.shapes.add_shape(
        MSO_SHAPE.RECTANGLE,
        slide_width - frame_width, 0,
        frame_width, slide_height
    )
    right.z_order = 0
    right.line.fill.background()
    fill = right.fill
    fill.solid()
    fill.fore_color.rgb = color1
    _lock_shape(right)