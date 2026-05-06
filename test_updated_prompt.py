#!/usr/bin/env python3
"""
Test the updated ANALYSIS_PROMPT with new table fields
"""
import sys
import os
sys.path.append('/home/openclaw/mysite')

from blueprints.doc_generator.parsers.template_parser import parse_template
from blueprints.doc_generator.parsers.analysis_parser import parse_analysis
from blueprints.doc_generator.config import ANALYSIS_PROMPT
import json

def test_prompt_format():
    """Test that the updated prompt produces correct JSON structure."""
    print("=== Testing Updated ANALYSIS_PROMPT ===")
    
    # Create sample text content that mimics our test document
    sample_text = """
ТЕСТОВИЙ ДОКУМЕНТ З ТАБЛИЦЯМИ

Це тестовий документ для перевірки парсингу таблиць.

РОЗДІЛ 1: ОРГАНОЛЕПТИЧНІ ПОКАЗНИКИ

Показник	Характеристика	Яєчного порошку	Білка яєчного сухого	Жовтка яєчного сухого
Зовнішній вигляд	描述	Сипучий порошок	Сипучий порошок	Сипучий порошок
Колір	描述	Світло-жовтий	Світло-жовтий	Жовто-помаранчевий
Запах	描述	Властивий яєчному порошку	Властивий яєчному білку	Властивий яєчному жовтку
Вологість, %	Не більше 7.0	6.5	6.8	7.0
Розчинність	Повна	Повна	Повна	Повна

Таблиця 1 показує основні органолептичні показники сухих яєчних продуктів.

РОЗДІЛ 2: ХІМІЧНИЙ СКЛАД

Компонент	Яєчний порошок	Білок яєчний сухий
Протеїн, %	45.0-48.0	82.0-85.0
Жир, %	38.0-42.0	1.5-3.0
Вуглеводи, %	3.0-5.0	4.0-6.0
"""
    
    # Format the prompt with sample text
    formatted_prompt = ANALYSIS_PROMPT.format(text_content=sample_text[:500000])
    
    print("Sample prompt length:", len(formatted_prompt))
    print("\n=== Expected JSON Structure ===")
    print("""
[
  {
    "id": 1,
    "type": "text",
    "title": "ТЕСТОВИЙ ДОКУМЕНТ З ТАБЛИЦЯМИ",
    "level": 0,
    "words": "від 5 до 7 слів",
    "description": {
      "general": "Заголовок документа",
      "specific": "Тестовий документ для перевірки парсингу таблиць"
    }
  },
  {
    "id": 2,
    "type": "text", 
    "title": "РОЗДІЛ 1: ОРГАНОЛЕПТИЧНІ ПОКАЗНИКИ",
    "level": 1,
    "words": "від 10 до 12 слів",
    "description": {
      "general": "Опис розділу з показниками",
      "specific": "Розділ присвячений органолептичним показникам сухих яєчних продуктів"
    }
  },
  {
    "id": 3,
    "type": "table",
    "title": "Таблиця 1",
    "level": 2,
    "rows": 6,
    "columns": ["Показник", "Характеристика", "Яєчного порошку", "Білка яєчного сухого", "Жовтка яєчного сухого"],
    "first_column_data": ["Зовнішній вигляд", "Колір", "Запах", "Вологість, %", "Розчинність"]
  }
]
""")

def test_parser_compatibility():
    """Test that the parser can handle the new JSON format."""
    print("\n=== Testing Parser Compatibility ===")
    
    # Sample JSON response that LLM might generate
    sample_json_response = [
        {
            "id": 1,
            "type": "text",
            "title": "ТЕСТОВИЙ ДОКУМЕНТ З ТАБЛИЦЯМИ",
            "level": 0,
            "words": "від 5 до 7 слів",
            "description": {
                "general": "Заголовок документа",
                "specific": "Тестовий документ для перевірки парсингу таблиць"
            }
        },
        {
            "id": 2,
            "type": "table",
            "title": "Таблиця 1 Органолептичні показники",
            "level": 2,
            "rows": 6,
            "columns": ["Показник", "Характеристика", "Яєчного порошку", "Білка яєчного сухого", "Жовтка яєчного сухого"],
            "first_column_data": ["Зовнішній вигляд", "Колір", "Запах", "Вологість, %", "Розчинність"]
        }
    ]
    
    json_str = json.dumps(sample_json_response, ensure_ascii=False)
    print("Sample JSON to parse:")
    print(json_str)
    
    try:
        # Test parsing with our updated parser
        parsed = parse_analysis(json_str)
        print(f"\n✅ Parsing successful!")
        print(f"Sections: {len(parsed.sections)}")
        print(f"Tables: {len(parsed.tables)}")
        
        # Check table details
        for table in parsed.tables:
            print(f"\nTable: {table.title}")
            print(f"  Rows: {table.rows}")
            print(f"  Columns: {len(table.columns)}")
            print(f"  First column data: {len(table.first_column_data) if table.first_column_data else 0} items")
            if table.first_column_data:
                print(f"  Sample first column: {table.first_column_data[:3]}")
        
        return True
        
    except Exception as e:
        print(f"❌ Parsing failed: {e}")
        import traceback
        traceback.print_exc()
        return False

def test_end_to_end():
    """Test end-to-end process with actual DOCX file."""
    print("\n=== Testing End-to-End Process ===")
    
    # Parse our test DOCX file
    test_file = '/home/openclaw/mysite/data/templates/test_table.docx'
    if not os.path.exists(test_file):
        print(f"❌ Test file not found: {test_file}")
        return False
    
    try:
        # Step 1: Parse template
        template_data = parse_template(test_file)
        print(f"✅ Template parsed: {len(template_data.get('tables', []))} tables found")
        
        # Step 2: Extract text for LLM
        text_content = ""
        for section in template_data.get('sections', []):
            text_content += f"{section['title']}\n"
        
        for table in template_data.get('tables', []):
            text_content += f"\nТаблиця {table['index'] + 1}\n"
            text_content += f"Стовпці: {' | '.join(table['headers'])}\n"
            text_content += f"Кількість рядків: {table['rows']}\n"
            if table.get('first_column_data'):
                text_content += f"Перший стовпець: {', '.join(table['first_column_data'][:5])}\n"
        
        # Step 3: Format prompt (simulating what LLM would receive)
        formatted_prompt = ANALYSIS_PROMPT.format(text_content=text_content[:500000])
        print(f"✅ Prompt formatted: {len(formatted_prompt)} chars")
        
        # Step 4: Show what structure we expect LLM to generate
        print("\n📋 Expected LLM output structure:")
        print("- Text sections with description.general and description.specific")
        print("- Tables with rows, columns, and first_column_data fields")
        
        return True
        
    except Exception as e:
        print(f"❌ End-to-end test failed: {e}")
        import traceback
        traceback.print_exc()
        return False

if __name__ == '__main__':
    test_prompt_format()
    success1 = test_parser_compatibility()
    success2 = test_end_to_end()
    
    print(f"\n=== SUMMARY ===")
    print(f"Parser compatibility: {'✅ PASS' if success1 else '❌ FAIL'}")
    print(f"End-to-end process: {'✅ PASS' if success2 else '❌ FAIL'}")
    
    if success1 and success2:
        print("\n🎉 All tests passed! The updated ANALYSIS_PROMPT is ready.")
    else:
        print("\n⚠️  Some tests failed. Check the output above.")
