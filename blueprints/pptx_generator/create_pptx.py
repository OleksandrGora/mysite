from pptx import Presentation
from pptx.util import Inches, Pt, Emu
from pptx.dml.color import RGBColor
from pptx.enum.dml import MSO_THEME_COLOR
from pptx.enum.shapes import MSO_SHAPE
from datetime import datetime
import os
import random
import math

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
    city_para = city_frame.paragraphs[0]
    city_para.font.size = Pt(20)
    city_para.font.color.rgb = selected_title_color
    city_para.alignment = 1  # Center
    
    # Choose design type once for the entire presentation (except title slide)
    design_type = random.choice(['rectangle', 'circle'])
    rectangle_layout = None
    
    # If rectangle design, choose layout once for consistency
    if design_type == 'rectangle':
        rectangle_layout = random.choice(rectangle_layout_options)
    
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
            # Rectangles: same layout for all slides
            _add_background_rectangles(slide, prs.slide_width, prs.slide_height, color1, color2, rectangle_layout)
        else:
            # Circles: random layout for each slide
            circle_layout = random.choice(circle_layout_options)
            _add_background_circles(slide, prs.slide_width, prs.slide_height, color1, color2, circle_layout)
        
        # Додаємо заголовок - завжди жирний, контрастний колір
        # Заголовок займає верхню частину до 1.3 дюймів
        if title_text:
            title_box = slide.shapes.add_textbox(Inches(0.5), Inches(0.3), Inches(9), Inches(0.8))
            title_frame = title_box.text_frame
            title_frame.text = title_text
            title_para = title_frame.paragraphs[0]
            title_para.font.size = Pt(32)
            title_para.font.bold = True  # Завжди жирний
            title_para.font.color.rgb = selected_title_color
            title_para.alignment = 1  # Center
        
        # Перевіряємо чи є зображення для чергування ліво/право
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
        
        # Чергування зображень: непарні слайди - право, парні - ліво
        # slide_count починається з 1
        image_on_right = (slide_count % 2 == 1)

        # Розраховуємо розмір зображення: 30% від ширини слайду
        slide_width = prs.slide_width
        slide_height = prs.slide_height
        img_width = slide_width * 0.5  # 30% ширини слайду
        # Або 30% від висоти - використовуємо менший розмір для оптимального вміщення
        img_height_limit = slide_height * 0.5  # 35% висоти для комфорту

        # Визначаємо позицію зображення та тексту
        # Заголовок займає до 1.3 дюймів (~1.18"), зображення починається після нього
        img_y = Inches(1.3)  # Під заголовком
        text_y = Inches(1.3)  # Текст на тому ж рівні, що й зображення

        # Додаємо текст тіла з урахуванням чергування
        if body_text:
            if has_image:
                text_width = Inches(6.3)  # Ширина тексту коли є зображення
                if image_on_right:
                    # Зображення справа, текст зліва
                    body_box = slide.shapes.add_textbox(Inches(0.5), text_y, text_width, Inches(4.5))
                else:
                    # Зображення зліва, текст справа
                    body_box = slide.shapes.add_textbox(Inches(3.2), text_y, text_width, Inches(4.5))
            else:
                # Якщо немає зображення, текст на всю ширину
                body_box = slide.shapes.add_textbox(Inches(0.5), Inches(1.8), Inches(9), Inches(4.5))

            body_frame = body_box.text_frame
            body_frame.word_wrap = True
            body_frame.text = body_text
            body_para = body_frame.paragraphs[0]
            body_para.font.size = Pt(18)
            body_para.font.color.rgb = selected_title_color

        # Додавання зображення з чергуванням ліво/право та розміром 30% ширини
        if actual_image_path:
            from PIL import Image as PILImage
            # Визначаємо пропорції зображення
            with PILImage.open(actual_image_path) as img:
                img_w, img_h = img.size
                aspect_ratio = img_h / img_w

            # Ширина 30% від ширини слайду
            pic_width = int(slide_width * 0.3)
            pic_height = int(pic_width * aspect_ratio)

            # Якщо висота занадто велика, обмежуємо 35% висоти слайду
            max_height = int(slide_height * 0.5)
            if pic_height > max_height:
                pic_height = max_height
                pic_width = int(pic_height / aspect_ratio)

            if image_on_right:
                # Непарні слайди - зображення справа
                img_x = slide_width - pic_width - Inches(0.5)
                slide.shapes.add_picture(actual_image_path, img_x, img_y, width=pic_width, height=pic_height)
            else:
                # Парні слайди - зображення зліва
                slide.shapes.add_picture(actual_image_path, Inches(0.5), img_y, width=pic_width, height=pic_height)
        
    prs.save(filename)
    return filename


def _lock_shape(shape):
    """Заблокувати фігуру від редагування та переміщення через XML."""
    from pptx.oxml.ns import qn

    # Отримуємо XML елемент фігури
    sp = shape._element

    # Створюємо або отримуємо spLocks елемент
    # Додаємо блокування через XML атрибути
    sp_locks = sp.find(qn('a:spLocks'))
    if sp_locks is None:
        from lxml import etree
        sp_locks = etree.SubElement(sp, qn('a:spLocks'))

    # Встановлюємо атрибути блокування
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
        # Full slide rectangle
        rect = slide.shapes.add_shape(
            MSO_SHAPE.RECTANGLE,
            0, 0,
            slide_width, slide_height
        )
        rect.z_order = 0
        rect.line.fill.background()
        
        # Add gradient fill
        fill = rect.fill
        fill.gradient()
        fill.gradient_angle = random.choice([0, 45, 90, 135, 180, 225, 270, 315])
        fill.gradient_stops[0].color.rgb = color1
        fill.gradient_stops[1].color.rgb = color2
        
        # Lock shape
        _lock_shape(rect)
        
    elif layout == 'left_half':
        # Left half rectangle
        rect = slide.shapes.add_shape(
            MSO_SHAPE.RECTANGLE,
            0, 0,
            slide_width / 2, slide_height
        )
        rect.z_order = 0
        rect.line.fill.background()
        
        # Add gradient fill
        fill = rect.fill
        fill.gradient()
        fill.gradient_angle = random.choice([0, 45, 90, 135, 180, 225, 270, 315])
        fill.gradient_stops[0].color.rgb = color1
        fill.gradient_stops[1].color.rgb = color2
        
        # Lock shape
        _lock_shape(rect)
        
    elif layout == 'right_half':
        # Right half rectangle
        rect = slide.shapes.add_shape(
            MSO_SHAPE.RECTANGLE,
            slide_width / 2, 0,
            slide_width / 2, slide_height
        )
        rect.z_order = 0
        rect.line.fill.background()
        
        # Add gradient fill
        fill = rect.fill
        fill.gradient()
        fill.gradient_angle = random.choice([0, 45, 90, 135, 180, 225, 270, 315])
        fill.gradient_stops[0].color.rgb = color1
        fill.gradient_stops[1].color.rgb = color2
        
        # Lock shape
        _lock_shape(rect)


def _add_background_circles(slide, slide_width, slide_height, color1, color2, layout):
    """
    Add two circles for slide background according to selected layout.
    Circles are clipped to slide boundaries (appear as sectors).

    Layout options (4 corners):
    - corner_top_left: large circle in top-left corner
    - corner_top_right: large circle in top-right corner  
    - corner_bottom_left: large circle in bottom-left corner
    - corner_bottom_right: large circle in bottom-right corner

    First circle:
    - Center at selected corner
    - Radius = half of slide height
    - Clipped to visible part (sector) inside slide

    Second circle:
    - Center on diagonal from opposite corner
    - At 1/4 distance from opposite corner
    - Diameter = 20% of slide width
    """
    # Corner positions for the first circle
    corner_positions = {
        'corner_top_left': (0, 0),
        'corner_top_right': (slide_width, 0),
        'corner_bottom_left': (0, slide_height),
        'corner_bottom_right': (slide_width, slide_height),
    }

    # Get corner coordinates for first circle
    corner = corner_positions.get(layout, (0, 0))
    c1_center_x, c1_center_y = corner

    # First circle parameters
    c1_radius = slide_height / 2
    c1_diameter = c1_radius * 2

    # Diagonal coefficients for calculating opposite point
    diagonal_map = {
        (0, 0): (slide_width, slide_height),  # top-left -> bottom-right
        (0, slide_height): (slide_width, 0),  # bottom-left -> top-right
        (slide_width, 0): (0, slide_height),  # top-right -> bottom-left
        (slide_width, slide_height): (0, 0),  # bottom-right -> top-left
    }

    # Opposite corner
    opposite = diagonal_map.get((c1_center_x, c1_center_y), (slide_width, slide_height))
    opp_x, opp_y = opposite

    # Diagonal from opposite corner through first circle center
    c2_center_x = opp_x - 0.25 * (opp_x - c1_center_x)
    c2_center_y = opp_y - 0.25 * (opp_y - c1_center_y)

    # Second circle diameter = 20% of slide width
    c2_diameter = slide_width * 0.2
    c2_radius = c2_diameter / 2

    # Add first circle (large circle at corner)
    # Circle center at corner, radius = half slide height
    c1_left = c1_center_x - c1_radius
    c1_top = c1_center_y - c1_radius

    # Create full circle (it may extend beyond slide boundaries)
    # PowerPoint automatically shows only visible part (sector)
    circle1 = slide.shapes.add_shape(
        MSO_SHAPE.OVAL,
        c1_left, c1_top,
        c1_diameter, c1_diameter
    )
    circle1.z_order = 0
    circle1.line.fill.background()

    # Add gradient fill
    fill1 = circle1.fill
    fill1.gradient()
    fill1.gradient_angle = random.choice([0, 45, 90, 135, 180, 225, 270, 315])
    fill1.gradient_stops[0].color.rgb = color1
    fill1.gradient_stops[1].color.rgb = color2

    # Lock first circle
    _lock_shape(circle1)

    # Add second circle (smaller, on diagonal)
    c2_left = c2_center_x - c2_radius
    c2_top = c2_center_y - c2_radius

    circle2 = slide.shapes.add_shape(
        MSO_SHAPE.OVAL,
        c2_left, c2_top,
        c2_diameter, c2_diameter
    )
    circle2.z_order = 1  # On top of first circle
    circle2.line.fill.background()

    # Add gradient fill
    fill2 = circle2.fill
    fill2.gradient()
    fill2.gradient_angle = random.choice([0, 45, 90, 135, 180, 225, 270, 315])
    fill2.gradient_stops[0].color.rgb = color1
    fill2.gradient_stops[1].color.rgb = color2

    # Lock second circle
    _lock_shape(circle2)
