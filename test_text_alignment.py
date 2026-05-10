#!/usr/bin/env python3
"""
Test script to verify text alignment and auto-sizing in PowerPoint generation
"""

import sys
import os
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from blueprints.pptx_generator.create_pptx import create_presentation

def test_text_alignment():
    """Test text alignment and auto-sizing with long text"""
    
    # Test data with long titles and text
    slides_data = [
        {
            "title": "Дуже Довга Назва Презентації Яка Має Перевірити Чи Правильно Працює Автоматичне Зменшення Розміру Шрифту та Вирівнювання По Центру",
            "body": "Це тестовий текст для перевірки роботи функціональності автоматичного розширення текстових полів та вирівнювання по центру.",
            "image_description": "Тестове зображення"
        },
        {
            "title": "Ще Один Дуже Довгий Заголовок Слайду Для Перевірки Автоматичної Підгонки Розміру Тексту",
            "body": "Тут ми перевіряємо чи правильно працює автоматичне зменшення розміру шрифту для довгих заголовків на звичайних слайдах презентації.",
            "image_description": "Ще одне тестове зображення"
        },
        {
            "title": "Короткий заголовок",
            "body": "Це звичайний текст нормальної довжини для перевірки базової функціональності.",
            "image_description": "Просте зображення"
        }
    ]
    
    output_file = "/home/openclaw/mysite/test_alignment_output.pptx"
    
    try:
        result = create_presentation(output_file, slides_data)
        print(f"✅ Test completed successfully!")
        print(f"📄 Presentation saved to: {result}")
        print("🔍 Please check the generated presentation to verify:")
        print("   - Title slide text is properly centered and auto-sized")
        print("   - Slide titles are centered and auto-sized for long text")
        print("   - All text fields maintain proper alignment")
        return True
    except Exception as e:
        print(f"❌ Test failed with error: {e}")
        return False

if __name__ == "__main__":
    test_text_alignment()
