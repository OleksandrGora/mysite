"""
QueryBuilder - Constructs search queries from analysis structure.

Each section/table in the document analysis generates one or more
search queries to find relevant chunks in ChromaDB.
"""

from typing import List, Dict
from loguru import logger


class QueryBuilder:
    """
    Builds search queries from parsed analysis sections and tables.
    """

    @staticmethod
    def build_queries(parsed_analysis) -> List[Dict[str, str]]:
        """
        Generate search queries for each section and table.

        Args:
            parsed_analysis: ParsedAnalysis object from analysis_parser.py
                Contains .sections and .tables lists

        Returns:
            List of dicts: [{section_title, query_text}, ...]
        """
        queries = []

        # Queries from text sections
        for section in parsed_analysis.sections:
            query_parts = [section.title]

            if section.description_general:
                query_parts.append(section.description_general)
            if section.description_specific:
                # Remove "## Наприклад:" prefix if present
                specific = section.description_specific
                if specific.startswith('## Наприклад:'):
                    specific = specific[13:].strip()
                if specific:
                    query_parts.append(specific)

            query_text = ' | '.join(query_parts)

            queries.append({
                'section_title': section.title,
                'query_text': query_text
            })

        # Queries from tables
        for table in parsed_analysis.tables:
            query_parts = [table.title]
            if table.columns:
                query_parts.append('колонки: ' + ', '.join(table.columns))
            if table.first_column_data:
                # Use first few data items for better matching
                sample = table.first_column_data[:3]
                query_parts.append('дані: ' + ', '.join(sample))

            query_text = ' | '.join(query_parts)

            queries.append({
                'section_title': table.title,
                'query_text': query_text
            })

        logger.info(f"Built {len(queries)} search queries from analysis "
                     f"({len(parsed_analysis.sections)} sections, "
                     f"{len(parsed_analysis.tables)} tables)")

        return queries

    @staticmethod
    def build_per_section_queries(
        parsed_analysis,
        parent_context: dict = None
    ) -> List[Dict[str, str]]:
        """
        Build more precise per-section search queries with hierarchical context.

        Unlike build_queries(), this method:
        - Includes parent section titles for hierarchical context
        - Adjusts query detail based on word_count_range (smaller sections
          get more specific queries, larger sections get broader queries)
        - For tables, includes all available metadata (columns, sample data)

        Args:
            parsed_analysis: ParsedAnalysis object
            parent_context: Optional dict from prompt_parser.build_parent_context()
                Maps section_title -> [parent_titles...]

        Returns:
            List of dicts: [{section_title, query_text}, ...]
        """
        parent_context = parent_context or {}
        queries = []

        # Queries from text sections
        for section in parsed_analysis.sections:
            query_parts = [section.title]

            # Add parent context (hierarchical)
            parents = parent_context.get(section.title, [])
            if parents:
                query_parts.append('розділ: ' + ' > '.join(parents))

            # Add general description
            if section.description_general:
                # For small sections, use more of the general description
                # to compensate for lack of data
                query_parts.append(section.description_general)

            # Add specific description with details
            if section.description_specific:
                specific = section.description_specific
                if specific.startswith('## Наприклад:'):
                    specific = specific[13:].strip()
                if specific:
                    query_parts.append(specific)

            # Adjust based on expected word count
            wc = section.word_count_range
            if wc:
                min_w, max_w = wc
                avg_words = (min_w + max_w) / 2
                if avg_words < 30:
                    # Very small section: boost search precision
                    # by adding "деталі" marker
                    query_parts.append('ключові показники')
                elif avg_words > 150:
                    # Large section: need broader search
                    query_parts.append('повний опис методологія')

            query_text = ' | '.join(query_parts)
            queries.append({
                'section_title': section.title,
                'query_text': query_text,
                'word_count_range': wc
            })

        # Queries from tables
        for table in parsed_analysis.tables:
            query_parts = [table.title]

            # Add parent context
            parents = parent_context.get(table.title, [])
            if parents:
                query_parts.append('розділ: ' + ' > '.join(parents))

            if table.columns:
                query_parts.append('колонки: ' + ', '.join(table.columns))
            if table.first_column_data:
                # Include more sample data for better matching
                sample = table.first_column_data[:5]
                query_parts.append('дані: ' + ', '.join(sample))

            query_text = ' | '.join(query_parts)
            queries.append({
                'section_title': table.title,
                'query_text': query_text,
                'word_count_range': None  # tables don't have word ranges
            })

        logger.info(f"Built {len(queries)} per-section queries "
                     f"({len(parsed_analysis.sections)} sections, "
                     f"{len(parsed_analysis.tables)} tables)")

        return queries

    @staticmethod
    def format_queries_for_log(queries: List[Dict[str, str]]) -> str:
        """Format queries for log output."""
        lines = []
        for i, q in enumerate(queries):
            lines.append(f"  [{i+1}] '{q['section_title'][:50]}' → '{q['query_text'][:80]}...'")
        return '\n'.join(lines)
