#!/usr/bin/env python3
"""
Test script for table parsing functionality
"""
import sys
import os
sys.path.append('/home/openclaw/mysite')

from blueprints.doc_generator.parsers.template_parser import parse_template, parse_template_to_json
import json

def test_docx_table_parsing():
    """Test parsing a DOCX file with tables."""
    print("=== Testing DOCX Table Parsing ===")
    
    # Look for a test DOCX file
    templates_dir = '/home/openclaw/mysite/data/templates'
    if not os.path.exists(templates_dir):
        print(f"Templates directory not found: {templates_dir}")
        return
    
    docx_files = [f for f in os.listdir(templates_dir) if f.endswith('.docx')]
    if not docx_files:
        print("No DOCX files found in templates directory")
        return
    
    # Test first DOCX file
    test_file = os.path.join(templates_dir, docx_files[0])
    print(f"Testing file: {test_file}")
    
    try:
        result = parse_template(test_file)
        print(f"Parsed successfully!")
        print(f"Title: {result.get('title')}")
        print(f"Sections: {len(result.get('sections', []))}")
        print(f"Tables: {len(result.get('tables', []))}")
        
        # Show table details
        for i, table in enumerate(result.get('tables', [])):
            print(f"\n--- Table {i+1} ---")
            print(f"Index: {table.get('index')}")
            print(f"Rows: {table.get('rows')}")
            print(f"Columns: {table.get('columns')}")
            print(f"Headers: {table.get('headers')}")
            print(f"Has header row: {table.get('has_header_row')}")
            print(f"First column data: {table.get('first_column_data', [])[:5]}")  # Show first 5 items
        
        # Save to JSON for inspection
        output_file = test_file.replace('.docx', '_parsed.json')
        with open(output_file, 'w', encoding='utf-8') as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
        print(f"\nFull result saved to: {output_file}")
        
    except Exception as e:
        print(f"Error parsing {test_file}: {e}")
        import traceback
        traceback.print_exc()

def test_pdf_table_parsing():
    """Test parsing a PDF file with tables."""
    print("\n=== Testing PDF Table Parsing ===")
    
    templates_dir = '/home/openclaw/mysite/data/templates'
    if not os.path.exists(templates_dir):
        print(f"Templates directory not found: {templates_dir}")
        return
    
    pdf_files = [f for f in os.listdir(templates_dir) if f.endswith('.pdf')]
    if not pdf_files:
        print("No PDF files found in templates directory")
        return
    
    # Test first PDF file
    test_file = os.path.join(templates_dir, pdf_files[0])
    print(f"Testing file: {test_file}")
    
    try:
        result = parse_template(test_file)
        print(f"Parsed successfully!")
        print(f"Title: {result.get('title')}")
        print(f"Pages: {result.get('pages')}")
        print(f"Sections: {len(result.get('sections', []))}")
        print(f"Tables: {len(result.get('tables', []))}")
        
        # Show table details
        for i, table in enumerate(result.get('tables', [])):
            print(f"\n--- Table {i+1} ---")
            print(f"Index: {table.get('index')}")
            print(f"Page: {table.get('page')}")
            print(f"Rows: {table.get('rows')}")
            print(f"Columns: {table.get('columns')}")
            print(f"Headers: {table.get('headers')}")
            print(f"First column data: {table.get('first_column_data', [])[:5]}")  # Show first 5 items
        
        # Save to JSON for inspection
        output_file = test_file.replace('.pdf', '_parsed.json')
        with open(output_file, 'w', encoding='utf-8') as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
        print(f"\nFull result saved to: {output_file}")
        
    except Exception as e:
        print(f"Error parsing {test_file}: {e}")
        import traceback
        traceback.print_exc()

if __name__ == '__main__':
    test_docx_table_parsing()
    test_pdf_table_parsing()
