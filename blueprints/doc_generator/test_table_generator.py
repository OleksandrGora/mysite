#!/usr/bin/env python3
"""
Тестова функція для генерації таблиці з JSON запиту.
Запускається з командного рядка для тестування створення таблиць.
"""

import os
import json
import glob
import argparse
from pathlib import Path
from docx import Document
from docx.shared import Inches, Pt
from loguru import logger

# Додаємо шлях до батьківської директорії для імпортів
import sys
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from blueprints.doc_generator.builders.docx_builder import build_table_from_data


def get_next_file_number(temp_dir: Path) -> int:
    """Отримати наступний номер файлу на основі існуючих файлів."""
    pattern = str(temp_dir / "table_*.docx")
    existing_files = glob.glob(pattern)
    
    if not existing_files:
        return 1
    
    # Витягуємо номери з існуючих файлів
    numbers = []
    for file_path in existing_files:
        filename = Path(file_path).stem
        try:
            number = int(filename.replace("table_", ""))
            numbers.append(number)
        except ValueError:
            continue
    
    return max(numbers) + 1 if numbers else 1


def parse_columns_string(columns_str: str) -> list:
    """Розпарсити рядок з колонками, розділеними |."""
    return [col.strip() for col in columns_str.split("|") if col.strip()]


def generate_sample_data(headers: list, table_title: str = "", num_rows: int = 3) -> list:
    """Згенерувати тестові дані для таблиці."""
    sample_data = []
    
    # for i in range(num_rows):
    #     row = []
    #     for header in headers:
    #         if "Зразок" in header:
    #             row.append(f"Зразок {i+1}")
    #         elif "вологи" in header.lower():
    #             row.append(f"{15 + i * 2}.{5 + i}")
    #         elif "рН" in header:
    #             row.append(f"{6.5 + i * 0.2}")
    #         elif "Кислотність" in header:
    #             row.append(f"{10 + i * 2}.{5 + i}")
    #         elif "Лужність" in header:
    #             row.append(f"{20 + i * 3}.{0 + i}")
    #         elif "Розчинність" in header:
    #             row.append(f"{85 + i * 2}.{0 + i}")
    #         elif "Показник" in header:
    #             if "Органолептичні" in table_title:
    #                 indicators = ["Колір", "Запах", "Структура", "Смак"]
    #                 row.append(indicators[i] if i < len(indicators) else f"Показник {i+1}")
    #             else:
    #                 row.append(f"Показник {i+1}")
    #         elif "Характеристика" in header:
    #             if "Органолептичні" in table_title:
    #                 characteristics = ["Жовтий, без домішок", "Специфічний, без сторонніх", "Сипучий, без грудок", "Відповідний"]
    #                 row.append(characteristics[i] if i < len(characteristics) else f"Характеристика {i+1}")
    #             else:
    #                 row.append(f"Характеристика {i+1}")
    #         elif "Яєчного порошку" in header:
    #             if "Органолептичні" in table_title:
    #                 values = ["Відповідає", "Відповідає", "Відповідає", "Відповідає"]
    #                 row.append(values[i] if i < len(values) else "Норма")
    #             else:
    #                 row.append(f"Значення {i+1}")
    #         elif "Білка яєчного сухого" in header:
    #             if "Органолептичні" in table_title:
    #                 values = ["Білий, без домішок", "Нейтральний", "Дрібнозернистий", "Відповідний"]
    #                 row.append(values[i] if i < len(values) else "Норма")
    #             else:
    #                 row.append(f"Значення {i+1}")
    #         elif "Жовтка яєчного сухого" in header:
    #             if "Органолептичні" in table_title:
    #                 values = ["Помаранчевий", "Специфічний", "Сипучий", "Відповідний"]
    #                 row.append(values[i] if i < len(values) else "Норма")
    #             else:
    #                 row.append(f"Значення {i+1}")
    #         elif "Індекс розчинності" in header:
    #             row.append(f"{50 + i * 10}")
    #         else:
    #             row.append(f"Дані {i+1}")
    #     sample_data.append(row)
    
    return sample_data


def generate_section_content(section: dict) -> str:
    """Згенерувати контент для текстової секції."""
    title = section.get('title', '')
    section_type = section.get('type', 'text')
    level = section.get('level', 0)
    description = section.get('description', {})
    words = section.get('words', '')
    
    # Перевіряємо чи є дані в analysis.txt
    if not description or (isinstance(description, dict) and not any(description.values())):
        logger.warning(f"Section {title} has no description data in analysis.txt - will generate basic content")
        return f"{title}\n\nТекст секції буде згенеровано пізніше."
    
    # Базова логіка генерації контенту на основі типу та рівня
    # if level == 0:  # Основні заголовки
    #     if "Лабораторна робота" in title:
    #         return f"{title}\n\nДана робота є частиною навчального процесу з вивчення якості харчових продуктів."
    #     elif "Дослідження" in title:
    #         return f"{title}\n\nУ даній роботі проводиться комплексний аналіз якості сухих яєчних продуктів за різними показниками."
    
    # elif level == 1:  # Підрозділи
    #     if "Мета роботи" in title:
    #         return "Метою даної лабораторної роботи є визначення якості сухих яєчних продуктів за органолептичними та фізико-хімічними показниками. В результаті виконання роботи студент повинен:\n\n1. Оволодіти методиками визначення якісних показників яєчних продуктів.\n2. Навчитися проводити розрахунки та оформлювати результати.\n3. Зробити висновки про якість досліджуваних зразків."
    #     elif "Обладнання" in title:
    #         return "Для виконання роботи необхідне таке обладнання:\n\n• Сушильна шафа\n• Рефрактометр\n• Аналітичні ваги\n• рН-метр\n• Центрифуга\n• Лабораторний термометр\n• Мерні колби та піпетки"
    #     elif "Матеріали та реактиви" in title:
    #         return "Матеріали та реактиви, необхідні для роботи:\n\n• Сухий яєчний порошок – 50 г\n• Сухий яєчний білок – 30 г\n• Сухий яєчний жовток –20 г\n• Розчин NaOH 0,1 н\n• Розчин H2SO4 0,1 н\n• Фенолфталеїн\n• Дистильована вода\n• Фільтрувальний папір"
    #     elif "Сировина" in title:
    #         return "Об'єктом дослідження є сухі яєчні продукти промислового виробництва. Для аналізу використовуються зразки, що відповідають вимогам ДСТУ."
    #     elif "ВИМОГИ ДО ЯКОСТІ" in title:
    #         return "Сухі яєчні продукти повинні відповідати таким вимогам:\n\n• Виготовлені з курячих яєць\n• Вміст вологи не більше 8% для порошку, 9% для білка, 5% для жовтка\n• Відсутність сторонніх домішок та запаху\n• Рівномірна консистенція та колір\n• Пакування в герметичну тару\n• Термін зберігання не більше 12 місяців"
    #     elif "ВИЗНАЧЕННЯ ЯКОСТІ" in title:
    #         return "Для визначення якості сухих яєчних продуктів проводять відбір проб відповідно до ДСТУ. Проби об'єднують, квартують та підготовляють до аналізу."
    #     elif "Протокол роботи" in title:
    #         return "Результати досліджень заносять до підсумкової таблиці 3."
    
    # elif level == 2:  # Методи дослідження
    #     if "Органолептичні дослідження" in title:
    #         return "Органолептичні дослідження проводять шляхом оцінки зовнішнього вигляду, кольору, запаху, структури та смаку продукту. Для дегустації готують водний розчин зразка."
    #     elif "Физико-хімічні дослідження" in title:
    #         return "До фізико-хімічних показників якості належать: кислотність, рН, розчинність та вміст вологи. Визначення проводять відповідно до стандартних методик."
    #     elif "Висновки по роботі" in title:
    #         return "На основі проведених досліджень роблять висновки про якість досліджуваних зразків сухих яєчних продуктів та їх відповідність нормативним вимогам."
    
    # elif level == 3:  # Конкретні методики
    #     if "Визначення кислотності" in title:
    #         return "Кислотність визначають методом титрування 0,1 н розчином NaOH в присутності фенолфталеїну. Розрахунок проводять за формулою: Х = (V·К·100)/m, де V – об'єм розчину NaOH, К – поправочний коефіцієнт, m – маса наважки."
    #     elif "Визначення величини рН" in title:
    #         return "Величину рН визначають потенціометричним методом в 1%-вому розчині сухого білка. Вимірювання проводять при температурі 20±2°С."
    #     elif "Визначення розчинності" in title:
    #         return "Розчинність визначають двома методами: арбітражним та експрес-методом. Арбітражний метод базується на визначенні вмісту сухих речовин після центрифугування."
    
    # elif level == 4:  # Деталі методик
    #     if "А р б і т р а ж н и й метод" in title and "розчинності" in title:
    #         return "Арбітражний метод: 5 г продукту розчиняють у 50 мл води, перемішують 30 хв, центрифугують 10 хв при 3000 об/хв. Визначають масу сухих речовин у супернатанті. Розчинність розраховують за формулою."
    #     elif "Експрес-метод" in title and "розчинності" in title:
    #         return "Експрес-метод: визначають індекс розчинності за допомогою рефрактометра. Індекс розчинності корелюють з розчинністю за спеціальною таблицею."
    #     elif "А р б і т р а ж н и й метод" in title and "вологи" in title:
    #         return "Арбітражний метод: наважку 2-3 г висушують у сушильній шафі при 103±2°С до постійної маси. Вологість розраховують за формулою: W = ((m1-m2)/m1)·100, де m1 – початкова маса, m2 – маса після висушування."
    #     elif "Експрес-метод" in title and "вологи" in title:
    #         return "Експрес-метод: наважку висушують при температурі 180±5°С протягом 20 хвилин у спеціальній апаратурі. Метод дає приблизні результати."
    
    # Використовуємо дані з analysis.txt
    general_desc = description.get('general', '')
    specific_desc = description.get('specific', '')
    
    if general_desc or specific_desc:
        return f"{title}\n\n{general_desc}\n\n{specific_desc}"
    else:
        logger.warning(f"Section {title} has empty description in analysis.txt - using basic content")
        return f"{title}\n\nТекст секції буде згенеровано пізніше."


def create_full_document_from_json(full_json: list, output_path: str) -> str:
    """
    Створити повний документ з текстом та таблицями з JSON.
    
    Args:
        full_json: Повний JSON з усіма секціями
        output_path: Шлях для збереження файлу
        
    Returns:
        Шлях до створеного файлу
    """
    logger.info(f"Створення повного документу: {output_path}")
    
    # Створити новий документ
    doc = Document()
    
    # Налаштування стилів
    style = doc.styles['Normal']
    font = style.font
    font.name = 'Times New Roman'
    font.size = Pt(14)
    
    # Обробка всіх секцій
    for item in full_json:
        item_type = item.get('type', 'text')
        title = item.get('title', '')
        level = item.get('level', 0)
        
        if item_type == 'text':
            # Додати текстову секцію
            content = generate_section_content(item)
            
            # Вибір стилю заголовка на основі рівня
            if level == 0:
                heading_style = 'Title'
            elif level == 1:
                heading_style = 'Heading 1'
            elif level == 2:
                heading_style = 'Heading 2'
            elif level == 3:
                heading_style = 'Heading 3'
            elif level == 4:
                heading_style = 'Heading 4'
            else:
                heading_style = 'Heading 5'
            
            # Додати заголовок
            doc.add_paragraph(title, style=heading_style)
            
            # Додати контент
            paragraphs = content.split('\n\n')
            for para_text in paragraphs:
                if para_text.strip():
                    if para_text.startswith('•'):
                        # Маркований список
                        for bullet in para_text.split('•'):
                            if bullet.strip():
                                doc.add_paragraph(bullet.strip(), style='List Bullet')
                    elif para_text.startswith('1.'):
                        # Нумерований список
                        for num_item in para_text.split('\n'):
                            if num_item.strip():
                                doc.add_paragraph(num_item.strip(), style='List Number')
                    else:
                        # Звичайний параграф
                        doc.add_paragraph(para_text.strip())
            
            doc.add_paragraph()  # Порожній рядок між секціями
            
        elif item_type == 'table':
            # Додати таблицю
            columns = item.get('columns', [])
            if isinstance(columns, list):
                headers = columns
            else:
                headers = parse_columns_string(columns)
            
            table_title = item.get('title', '')
            rows = generate_sample_data(headers, table_title, num_rows=5)
            
            # Додати заголовок таблиці
            doc.add_paragraph(table_title, style='Heading 3')
            
            # Створити таблицю
            table = build_table_from_data(doc, item.get('id', 0), headers, rows, None)
            
            doc.add_paragraph()  # Порожній рядок після таблиці
    
    # Зберегти документ
    doc.save(output_path)
    logger.success(f"Повний документ збережено: {output_path}")
    
    return output_path


def create_table_from_json_request(table_request: dict, output_path: str) -> str:
    """
    Створити таблицю з JSON запиту.
    
    Args:
        table_request: JSON запит для створення таблиці
        output_path: Шлях для збереження файлу
        
    Returns:
        Шлях до створеного файлу
    """
    logger.info(f"Створення таблиці: {table_request.get('title', 'Без назви')}")
    
    # Розпарсити колонки
    headers = parse_columns_string(table_request.get('columns', ''))
    logger.info(f"Колонки: {headers}")
    
    # Згенерувати тестові дані
    table_title = table_request.get('title', '')
    rows = generate_sample_data(headers, table_title, num_rows=5)
    logger.info(f"Згенеровано {len(rows)} рядків даних")
    
    # Створити новий документ
    doc = Document()
    
    # Додати заголовок
    title = table_request.get('title', 'Таблиця')
    doc.add_paragraph(title, style='Heading 1')
    doc.add_paragraph()
    
    # Створити таблицю
    table = build_table_from_data(
        doc=doc,
        table_index=table_request.get('id', 0),
        headers=headers,
        rows=rows,
        template_table=None
    )
    
    # Зберегти документ
    doc.save(output_path)
    logger.success(f"Таблицю збережено: {output_path}")
    
    return output_path


def validate_json_data(full_json):
    """Валідація JSON даних перед обробкою."""
    errors = []
    
    for i, item in enumerate(full_json, 1):
        # Перевірка обов'язкових полів
        if 'id' not in item:
            errors.append(f"Item {i}: missing 'id'")
        if 'type' not in item:
            errors.append(f"Item {i}: missing 'type'")
        if 'title' not in item:
            errors.append(f"Item {i}: missing 'title'")
        if 'level' not in item:
            errors.append(f"Item {i}: missing 'level'")
        
        # Перевірка типу
        item_type = item.get('type')
        if item_type == 'text':
            if 'description' not in item:
                errors.append(f"Item {i}: text section missing 'description'")
            elif item['description'] is not None and not isinstance(item['description'], dict):
                errors.append(f"Item {i}: description must be dict or null")
        elif item_type == 'table':
            if 'columns' not in item:
                errors.append(f"Item {i}: table missing 'columns'")
            elif item['columns'] is not None and not isinstance(item['columns'], list):
                errors.append(f"Item {i}: columns must be list or null")
    
    return errors


def main():
    """Головна функція для запуску з командного рядка."""
    
    # Хардкод шлях до analysis.txt
    analysis_path = Path("/home/openclaw/mysite/blueprints/doc_generator/Tasks-data/Лабораторна робота 8/analysis.txt")
    
    logger.info(f"📂 Завантаження analysis.txt: {analysis_path}")
    
    if not analysis_path.exists():
        logger.error(f"❌ Файл не знайдено: {analysis_path}")
        return 1
    
    try:
        with open(analysis_path, 'r', encoding='utf-8') as f:
            content = f.read()
        
        # Очистити markdown code block якщо є
        content = content.strip()
        if content.startswith('```json'):
            content = content[7:]
        elif content.startswith('```'):
            content = content[3:]
        if content.endswith('```'):
            content = content[:-3]
        content = content.strip()
        
        full_json = json.loads(content)
        logger.info(f"✅ JSON завантажено: {len(full_json)} елементів")
    except json.JSONDecodeError as e:
        logger.error(f"❌ Помилка парсингу JSON: {e}")
        return 1
    except Exception as e:
        logger.error(f"❌ Помилка читання файлу: {e}")
        return 1
    
    # Валідація JSON даних
    validation_errors = validate_json_data(full_json)
    if validation_errors:
        logger.error("JSON validation errors:")
        for error in validation_errors:
            logger.error(f"  - {error}")
        print(f"❌ Помилки валідації JSON: {len(validation_errors)}")
        for error in validation_errors[:5]:  # Показуємо перші 5 помилок
            print(f"  - {error}")
        return 1
    
    # Налаштування шляхів
    temp_dir = Path("/home/openclaw/mysite/blueprints/doc_generator/temp")
    temp_dir.mkdir(exist_ok=True)
    
    # Отримати наступний номер файлу
    file_number = get_next_file_number(temp_dir)
    output_filename = f"full_document_{file_number}.docx"
    output_path = temp_dir / output_filename
    
    logger.info(f"Створення повного документу: {output_path}")
    
    try:
        # Створити повний документ
        result_path = create_full_document_from_json(full_json, str(output_path))
        
        # Підрахувати статистику
        text_sections = len([item for item in full_json if item["type"] == "text"])
        table_sections = len([item for item in full_json if item["type"] == "table"])
        
        print(f"✅ Повний документ успішно створено!")
        print(f"📁 Файл збережено: {result_path}")
        print(f"📝 Текстових секцій: {text_sections}")
        print(f"📊 Таблиць: {table_sections}")
        print(f"📄 Всього секцій: {len(full_json)}")
        
    except Exception as e:
        logger.error(f"Помилка при створенні документу: {e}")
        print(f"❌ Помилка: {e}")
        return 1
    
    return 0


if __name__ == "__main__":
    exit(main())
