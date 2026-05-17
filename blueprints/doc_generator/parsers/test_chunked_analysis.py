import unittest
import json
import sys
from unittest.mock import MagicMock, patch


routes_mock = MagicMock()
routes_mock.append_debug_log = MagicMock()
sys.modules['blueprints.doc_generator.routes'] = routes_mock

from blueprints.doc_generator.parsers.chunked_analysis import split_text_into_chunks, merge_analyses
from blueprints.doc_generator.parsers.analysis_parser import parse_analysis


class TestSplitTextIntoChunks(unittest.TestCase):

    def test_empty_text(self):
        result = split_text_into_chunks("")
        self.assertEqual(result, [])

    def test_single_chunk_small_text(self):
        result = split_text_into_chunks("Just a small text")
        self.assertEqual(result, ["Just a small text"])

    def test_split_by_headings(self):
        text = (
            "# Розділ 1\n\nТекст першого розділу.\n\n"
            "## Підрозділ 1.1\n\nТекст підрозділу.\n\n"
            "## Підрозділ 1.2\n\nЩе текст.\n\n"
            "# Розділ 2\n\nТекст другого розділу.\n\n"
            "# Розділ 3\n\nТекст третього розділу."
        )
        result = split_text_into_chunks(text, max_chunk_size=50)
        self.assertGreater(len(result), 1)
        for chunk in result:
            self.assertTrue(chunk.startswith('#'), f"Chunk should start with heading, got: {chunk[:50]}")
            self.assertLessEqual(len(chunk), 100)

    def test_chunks_start_with_heading(self):
        text = (
            "Преамбула без заголовка.\n\n"
            "# Розділ 1\n\nContent.\n\n"
            "# Розділ 2\n\nMore content.\n\n"
            "# Розділ 3\n\nEven more."
        )
        result = split_text_into_chunks(text, max_chunk_size=30)
        for chunk in result[1:]:
            self.assertTrue(chunk.startswith('#'), f"Chunk should start with heading: {chunk[:50]}")

    def test_no_headings_paragraph_fallback(self):
        text = "Paragraph one.\n\nParagraph two.\n\nParagraph three.\n\nParagraph four."
        result = split_text_into_chunks(text, max_chunk_size=30)
        self.assertGreater(len(result), 1)

    def test_no_headings_no_paragraphs_hard_split(self):
        text = "word " * 10000
        result = split_text_into_chunks(text, max_chunk_size=10000)
        self.assertGreater(len(result), 1)
        for chunk in result:
            self.assertLessEqual(len(chunk), 10000)

    def test_exact_chunk_size(self):
        result = split_text_into_chunks("A" * 100000, max_chunk_size=100000)
        self.assertEqual(len(result), 1)

    def test_no_truncation_below_max_size(self):
        text = "# Title\n\nSome content here."
        result = split_text_into_chunks(text, max_chunk_size=100000)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0], text)


class TestMergeAnalyses(unittest.TestCase):

    def test_merge_two_simple_arrays(self):
        chunk1 = json.dumps([
            {"id": 1, "type": "text", "title": "Розділ 1", "level": 1, "words": "від 100 до 200 слів",
             "description": {"general": "Опис", "specific": "Наприклад: тест"}},
            {"id": 2, "type": "text", "title": "Розділ 2", "level": 2, "words": "від 50 до 100 слів",
             "description": {"general": "Опис 2", "specific": "Наприклад: тест 2"}},
        ], ensure_ascii=False)
        chunk2 = json.dumps([
            {"id": 1, "type": "text", "title": "Розділ 3", "level": 1, "words": "від 200 до 300 слів",
             "description": {"general": "Опис 3", "specific": "Наприклад: тест 3"}},
        ], ensure_ascii=False)

        result = merge_analyses([chunk1, chunk2])
        parsed = json.loads(result)
        self.assertEqual(len(parsed), 3)
        self.assertEqual(parsed[0]["id"], 1)
        self.assertEqual(parsed[0]["title"], "Розділ 1")
        self.assertEqual(parsed[1]["id"], 2)
        self.assertEqual(parsed[1]["title"], "Розділ 2")
        self.assertEqual(parsed[2]["id"], 3)
        self.assertEqual(parsed[2]["title"], "Розділ 3")

    def test_merge_with_tables(self):
        chunk1 = json.dumps([
            {"id": 1, "type": "text", "title": "Розділ 1", "level": 1, "words": "",
             "description": {"general": "Опис", "specific": "Приклад"}},
        ], ensure_ascii=False)
        chunk2 = json.dumps([
            {"id": 1, "type": "table", "title": "Таблиця 1", "level": 2, "rows": 5,
             "columns": ["A", "B"], "first_column_data": ["x", "y"]},
        ], ensure_ascii=False)

        result = merge_analyses([chunk1, chunk2])
        parsed = json.loads(result)
        self.assertEqual(len(parsed), 2)
        self.assertEqual(parsed[0]["type"], "text")
        self.assertEqual(parsed[0]["id"], 1)
        self.assertEqual(parsed[1]["type"], "table")
        self.assertEqual(parsed[1]["id"], 2)

    def test_merge_empty_input(self):
        result = merge_analyses([])
        self.assertEqual(json.loads(result), [])

    def test_merge_empty_chunks(self):
        result = merge_analyses(["", "   "])
        self.assertEqual(json.loads(result), [])

    def test_merge_partial_failure(self):
        chunk_valid = json.dumps([
            {"id": 1, "type": "text", "title": "Валідний", "level": 1, "words": "",
             "description": {"general": "Опис", "specific": "Приклад"}},
        ], ensure_ascii=False)
        result = merge_analyses([chunk_valid, "not valid json at all", chunk_valid])
        parsed = json.loads(result)
        self.assertEqual(len(parsed), 2)

    def test_merge_json_with_code_fences(self):
        chunk = "```json\n[{\"id\": 1, \"type\": \"text\", \"title\": \"Test\", \"level\": 1, \"words\": \"\", \"description\": {\"general\": \"G\", \"specific\": \"S\"}}]\n```"
        result = merge_analyses([chunk])
        parsed = json.loads(result)
        self.assertEqual(len(parsed), 1)
        self.assertEqual(parsed[0]["id"], 1)

    def test_merge_large_number_of_items(self):
        chunks = []
        for i in range(3):
            items = []
            for j in range(10):
                items.append({
                    "id": j + 1,
                    "type": "text",
                    "title": f"Section {i}-{j}",
                    "level": 1,
                    "words": "",
                    "description": {"general": "G", "specific": "S"}
                })
            chunks.append(json.dumps(items, ensure_ascii=False))

        result = merge_analyses(chunks)
        parsed = json.loads(result)
        self.assertEqual(len(parsed), 30)
        for k, item in enumerate(parsed):
            self.assertEqual(item["id"], k + 1)

    def test_merge_id_renumbering(self):
        chunk = json.dumps([
            {"id": 999, "type": "text", "title": "A", "level": 1, "words": "",
             "description": {"general": "G", "specific": "S"}},
            {"id": 1000, "type": "text", "title": "B", "level": 1, "words": "",
             "description": {"general": "G", "specific": "S"}},
        ], ensure_ascii=False)
        result = merge_analyses([chunk])
        parsed = json.loads(result)
        self.assertEqual(parsed[0]["id"], 1)
        self.assertEqual(parsed[1]["id"], 2)

    def test_merge_output_is_valid_json_for_parse_analysis(self):
        chunk1 = json.dumps([
            {"id": 1, "type": "text", "title": "Section 1", "level": 1, "words": "від 10 до 20 слів",
             "description": {"general": "General desc", "specific": "Specific ex"}},
        ], ensure_ascii=False)
        chunk2 = json.dumps([
            {"id": 1, "type": "table", "title": "Table 1", "level": 2, "rows": 3,
             "columns": ["Col1", "Col2"], "first_column_data": ["a", "b"]},
        ], ensure_ascii=False)

        result = merge_analyses([chunk1, chunk2])
        parsed = json.loads(result)

        pa = parse_analysis(json.dumps(parsed, ensure_ascii=False))
        self.assertEqual(len(pa.sections), 1)
        self.assertEqual(len(pa.tables), 1)
        self.assertEqual(pa.sections[0].index, 1)
        self.assertEqual(pa.tables[0].index, 2)


if __name__ == '__main__':
    unittest.main()
