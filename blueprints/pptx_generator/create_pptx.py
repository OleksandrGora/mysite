from pptx import Presentation
from pptx.util import Inches, Pt
from pptx.dml.color import RGBColor
from pptx.enum.dml import MSO_THEME_COLOR
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
    
    # Контрастні градієнти (більша різниця між кольорами)
    gradient_colors = [
        (RGBColor(173, 216, 230), RGBColor(70, 130, 180)),  # Світло-синій -> насичений синій
        (RGBColor(144, 238, 144), RGBColor(34, 139, 34)),    # Світло-зелений -> насичений зелений
        (RGBColor(221, 160, 221), RGBColor(128, 0, 128)),   # Світло-фіолетовий -> насичений фіолетовий
        (RGBColor(255, 218, 185), RGBColor(255, 140, 0)),   # Світло-оранжевий -> насичений оранжевий
        (RGBColor(255, 182, 193), RGBColor(220, 20, 60)),    # Світло-рожевий -> насичений червоний
        (RGBColor(176, 224, 230), RGBColor(0, 139, 139)),     # Світло-бірюзовий -> насичений темно-бірюзовий
        (RGBColor(255, 228, 225), RGBColor(147, 112, 219)),  # Світло-лавандовий -> насичений пурпурний
        (RGBColor(230, 230, 250), RGBColor(75, 0, 130)),    # Світло-пурпурний -> насичений індиго
    ]
    
    for slide_data in slides_data:
        title_text = slide_data.get('title', '')
        body_text = slide_data.get('body', '')
        image_description = slide_data.get('image_description', '')
        image_path = slide_data.get('image_path', '')
        
        # Використовуємо порожній макет (6) для повного контролю
        slide_layout = prs.slide_layouts[6]
        slide = prs.slides.add_slide(slide_layout)
        
        # Створюємо прямокутник на весь слайд для градієнтного фону
        background_shape = slide.shapes.add_shape(
            1,  # Rectangle
            0, 0,
            prs.slide_width, prs.slide_height
        )
        background_shape.z_order = 0  # На задній план
        
        # Додаємо градієнтну заливку
        fill = background_shape.fill
        fill.gradient()
        
        # Випадковий кут градієнта
        fill.gradient_angle = random.choice([0, 45, 90, 135, 180, 225, 270, 315])
        
        # Випадкові кольори градієнта
        color1, color2 = random.choice(gradient_colors)
        fill.gradient_stops[0].color.rgb = color1
        fill.gradient_stops[1].color.rgb = color2
        
        # Додаємо заголовок вручну через text box
        if title_text:
            title_box = slide.shapes.add_textbox(Inches(0.5), Inches(0.5), Inches(9), Inches(1))
            title_frame = title_box.text_frame
            title_frame.text = title_text
            title_para = title_frame.paragraphs[0]
            title_para.font.size = Pt(36)
            title_para.font.bold = True
            title_para.font.color.rgb = RGBColor(50, 50, 50)
            title_para.alignment = 1  # Center
        
        # Додаємо текст тіла вручну через text box
        if body_text:
            body_box = slide.shapes.add_textbox(Inches(0.5), Inches(2), Inches(9), Inches(4))
            body_frame = body_box.text_frame
            body_frame.word_wrap = True
            body_frame.text = body_text
            body_para = body_frame.paragraphs[0]
            body_para.font.size = Pt(18)
            body_para.font.color.rgb = RGBColor(50, 50, 50)
        
        # Додавання зображення якщо є
        if image_path and os.path.exists(image_path):
            slide.shapes.add_picture(image_path, Inches(6), Inches(1.5), width=Inches(3))
        elif image_description and images_dir:
            slide_index = len(prs.slides) - 1
            potential_image = os.path.join(images_dir, f"slide_{slide_index}.png")
            if os.path.exists(potential_image):
                slide.shapes.add_picture(potential_image, Inches(6), Inches(1.5), width=Inches(3))
        
    prs.save(filename)
    return filename
