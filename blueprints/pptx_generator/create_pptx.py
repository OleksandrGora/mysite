from pptx import Presentation
from pptx.util import Inches, Pt
from pptx.dml.color import RGBColor
from pptx.enum.dml import MSO_THEME_COLOR
from datetime import datetime
import os
import random

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
    
    # Менш насичені (пастельні) градієнти для всієї презентації
    gradient_colors = [
        (RGBColor(200, 230, 255), RGBColor(180, 210, 240)),  # Блакитний
        (RGBColor(200, 255, 220), RGBColor(180, 240, 200)),  # М'ятний
        (RGBColor(240, 220, 255), RGBColor(225, 200, 240)),  # Лавандовий
        (RGBColor(255, 235, 200), RGBColor(240, 220, 180)),  # Кремовий
        (RGBColor(255, 220, 230), RGBColor(240, 200, 220)),  # Рожевий
        (RGBColor(220, 250, 255), RGBColor(200, 235, 240)),  # Бірюзовий
        (RGBColor(230, 255, 230), RGBColor(210, 240, 210)),  # Світло-зелений
        (RGBColor(255, 245, 230), RGBColor(240, 230, 210)),  # Персиковий
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
    
    # Випадково вибираємо розмір/положення фону для ВСІХ слайдів
    # 0 = повний, 1 = ліва половина, 2 = права половина
    bg_layout = random.choice(['full', 'left', 'right'])
    
    # Create title slide with white background
    slide_layout = prs.slide_layouts[6]  # Blank layout
    title_slide = prs.slides.add_slide(slide_layout)
    
    # White background for title slide
    background_shape = title_slide.shapes.add_shape(
        1,  # Rectangle
        0, 0,
        prs.slide_width, prs.slide_height
    )
    background_shape.z_order = 0
    # Remove border
    background_shape.line.fill.background()
    fill = background_shape.fill
    fill.solid()
    fill.fore_color.rgb = RGBColor(255, 255, 255)  # White
    
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
    
    # Content slides
    slide_count = 0
    for slide_data in slides_data:
        title_text = slide_data.get('title', '')
        body_text = slide_data.get('body', '')
        image_description = slide_data.get('image_description', '')
        image_path = slide_data.get('image_path', '')
        
        # Використовуємо порожній макет (6) для повного контролю
        slide_layout = prs.slide_layouts[6]
        slide = prs.slides.add_slide(slide_layout)
        slide_count += 1
        
        # Визначаємо розміри фону залежно від вибраного layout
        if bg_layout == 'full':
            bg_x, bg_y = 0, 0
            bg_width = prs.slide_width
            bg_height = prs.slide_height
        elif bg_layout == 'left':
            bg_x, bg_y = 0, 0
            bg_width = prs.slide_width / 2
            bg_height = prs.slide_height
        else:  # right
            bg_x = prs.slide_width / 2
            bg_y = 0
            bg_width = prs.slide_width / 2
            bg_height = prs.slide_height
        
        # Створюємо прямокутник для градієнтного фону
        background_shape = slide.shapes.add_shape(
            1,  # Rectangle
            bg_x, bg_y,
            bg_width, bg_height
        )
        background_shape.z_order = 0  # На задній план
        # Remove border
        background_shape.line.fill.background()
        
        # Додаємо градієнтну заливку (той самий градієнт для всіх слайдів)
        fill = background_shape.fill
        fill.gradient()
        
        # Випадковий кут градієнта для кожного слайда
        fill.gradient_angle = random.choice([0, 45, 90, 135, 180, 225, 270, 315])
        
        # Використовуємо той самий градієнт для всіх слайдів
        fill.gradient_stops[0].color.rgb = color1
        fill.gradient_stops[1].color.rgb = color2
        
        # Додаємо заголовок - завжди жирний, контрастний колір
        if title_text:
            title_box = slide.shapes.add_textbox(Inches(0.5), Inches(0.5), Inches(9), Inches(1))
            title_frame = title_box.text_frame
            title_frame.text = title_text
            title_para = title_frame.paragraphs[0]
            title_para.font.size = Pt(36)
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
        
        # Додаємо текст тіла з урахуванням чергування
        if body_text:
            if has_image:
                if image_on_right:
                    # Зображення справа, текст зліва
                    body_box = slide.shapes.add_textbox(Inches(0.5), Inches(2), Inches(5), Inches(4))
                else:
                    # Зображення зліва, текст справа
                    body_box = slide.shapes.add_textbox(Inches(4), Inches(2), Inches(5.5), Inches(4))
            else:
                # Якщо немає зображення, текст на всю ширину
                body_box = slide.shapes.add_textbox(Inches(0.5), Inches(2), Inches(9), Inches(4))
            
            body_frame = body_box.text_frame
            body_frame.word_wrap = True
            body_frame.text = body_text
            body_para = body_frame.paragraphs[0]
            body_para.font.size = Pt(18)
            body_para.font.color.rgb = selected_title_color
        
        # Додавання зображення з чергуванням ліво/право
        if actual_image_path:
            if image_on_right:
                # Непарні слайди - зображення справа
                slide.shapes.add_picture(actual_image_path, Inches(6), Inches(2), width=Inches(3))
            else:
                # Парні слайди - зображення зліва
                slide.shapes.add_picture(actual_image_path, Inches(0.5), Inches(2), width=Inches(3))
        
    prs.save(filename)
    return filename
