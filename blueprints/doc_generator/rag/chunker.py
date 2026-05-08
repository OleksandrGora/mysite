"""
Chunker - Splits source text into chunks using paragraph mode.

Mode `paragraph`:
  - Split by double newlines (\\n\\n)
  - Merge small paragraphs (< min_chunk_size words) with the next
  - Split large paragraphs (> max_chunk_size words) by sentences
"""

import re
from typing import List, Optional
from dataclasses import dataclass, asdict
from loguru import logger


@dataclass
class Chunk:
    """Single chunk of text with metadata."""
    text: str
    source_file: str
    chunk_index: int
    word_count: int

    def to_dict(self) -> dict:
        return asdict(self)


def split_sentences(text: str) -> List[str]:
    """Split text into sentences, keeping the delimiter."""
    # Split on sentence endings (.!?) followed by space or newline
    parts = re.split(r'(?<=[.!?])\s+', text)
    return [p.strip() for p in parts if p.strip()]


def chunk_documents(
    sources_data: dict,
    min_chunk_size: int = 100,
    max_chunk_size: int = 800
) -> List[Chunk]:
    """
    Chunk source documents into paragraphs with smart merging/splitting.

    Args:
        sources_data: Dict of source_name -> {text, char_count, ...}
        min_chunk_size: Minimum words per chunk (smaller paragraphs merged)
        max_chunk_size: Maximum words per chunk (larger paragraphs split)

    Returns:
        List of Chunk objects with metadata
    """
    all_chunks: List[Chunk] = []
    global_index = 0

    logger.info(f"[CHUNKER] Starting chunk_documents: {len(sources_data)} sources, "
                f"min_chunk={min_chunk_size}, max_chunk={max_chunk_size}")
    
    # Log what sources_data looks like
    for source_name, data in sources_data.items():
        text = data.get('text', '')
        char_count = data.get('char_count', len(text))
        word_count = data.get('word_count', len(text.split()))
        logger.info(f"[CHUNKER] Source '{source_name}': text_len={len(text)} chars, "
                    f"{word_count} words, char_count_meta={char_count}")

    for source_name, data in sources_data.items():
        text = data.get('text', '')
        if not text:
            logger.warning(f"[CHUNKER] Empty text for source: {source_name}")
            continue

        text_len = len(text)
        text_words = len(text.split())
        
        # Step 1: Split by paragraphs (double newlines)
        raw_paragraphs = re.split(r'\n\s*\n', text)
        raw_paragraphs = [p.strip() for p in raw_paragraphs if p.strip()]
        
        logger.info(f"[CHUNKER] '{source_name}': {text_len} chars, {text_words} words, "
                    f"{len(raw_paragraphs)} raw paragraphs")

        if not raw_paragraphs:
            logger.warning(f"[CHUNKER] No paragraphs found in '{source_name}' after splitting! "
                           f"Text preview: {text[:100]!r}")
            continue

        # Step 2: Merge small paragraphs, split large ones
        merged = _merge_paragraphs(raw_paragraphs, min_chunk_size, max_chunk_size)
        
        logger.info(f"[CHUNKER] '{source_name}': {len(raw_paragraphs)} paragraphs → {len(merged)} merged chunks")

        # Step 3: Create Chunk objects
        for para_text in merged:
            word_count = len(para_text.split())
            all_chunks.append(Chunk(
                text=para_text,
                source_file=source_name,
                chunk_index=global_index,
                word_count=word_count
            ))
            global_index += 1

        logger.info(f"[CHUNKER] Done '{source_name}': {len(merged)} chunks (total so far: {len(all_chunks)})")

    logger.info(f"[CHUNKER] Total chunks created: {len(all_chunks)}")
    if all_chunks:
        word_counts = [c.word_count for c in all_chunks]
        logger.info(f"[CHUNKER] Global stats: min={min(word_counts)}, max={max(word_counts)}, "
                    f"avg={sum(word_counts)/len(word_counts):.0f} words per chunk")
    else:
        logger.warning(f"[CHUNKER] No chunks created at all!")
    
    return all_chunks


def _merge_paragraphs(
    paragraphs: List[str],
    min_chunk_size: int,
    max_chunk_size: int
) -> List[str]:
    """
    Merge small paragraphs and split large ones.

    Returns a list of paragraph strings where each has
    between min_chunk_size and max_chunk_size * 1.5 words.
    """
    result: List[str] = []
    buffer: List[str] = []
    buffer_words = 0

    for para in paragraphs:
        para_words = len(para.split())

        # Case 1: Very large paragraph - split it
        if para_words > max_chunk_size:
            # Flush buffer first
            if buffer:
                result.append('\n\n'.join(buffer))
                buffer = []
                buffer_words = 0

            # Split large paragraph by sentences
            sentences = split_sentences(para)
            sub_buffer: List[str] = []
            sub_words = 0

            for sent in sentences:
                sent_words = len(sent.split())
                if sub_words + sent_words <= max_chunk_size:
                    sub_buffer.append(sent)
                    sub_words += sent_words
                else:
                    if sub_buffer:
                        result.append(' '.join(sub_buffer))
                    sub_buffer = [sent]
                    sub_words = sent_words

            if sub_buffer:
                result.append(' '.join(sub_buffer))

        # Case 2: Small paragraph - merge with buffer
        elif para_words < min_chunk_size:
            buffer.append(para)
            buffer_words += para_words

        # Case 3: Normal paragraph
        else:
            # Check if adding to buffer would still be reasonable
            # But don't let buffer exceed max_chunk_size (for embedding model limits)
            if buffer and buffer_words + para_words <= max_chunk_size:
                buffer.append(para)
                buffer_words += para_words
            else:
                # Flush buffer, start new
                if buffer:
                    result.append('\n\n'.join(buffer))
                buffer = [para]
                buffer_words = para_words

    # Flush remaining buffer
    if buffer:
        result.append('\n\n'.join(buffer))

    return result


def format_chunks_for_log(chunks: List[Chunk]) -> str:
    """Format chunks for log output."""
    lines = []
    for c in chunks:
        preview = c.text[:80].replace('\n', ' ')
        lines.append(f"  [{c.chunk_index}] ({c.word_count} слів) {c.source_file}: {preview}...")
    return '\n'.join(lines)
