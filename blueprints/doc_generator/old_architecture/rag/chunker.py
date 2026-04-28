"""
Chunker - Splits large text into overlapping chunks for RAG.
"""
from typing import List
import re


def chunk_text(
    text: str,
    chunk_size: int = 800,
    overlap: int = 150
) -> List[str]:
    """
    Split text into overlapping chunks by tokens (approximated by words).
    
    Args:
        text: Input text to split
        chunk_size: Target chunk size in tokens (words)
        overlap: Overlap between chunks in tokens
        
    Returns:
        List of text chunks
    """
    if not text:
        return []
    
    # Simple word-based tokenization (approximation)
    words = text.split()
    
    if len(words) <= chunk_size:
        return [text]
    
    chunks = []
    start = 0
    
    while start < len(words):
        # Get chunk
        end = min(start + chunk_size, len(words))
        chunk_words = words[start:end]
        chunk_text = ' '.join(chunk_words)
        
        # Try to break at sentence boundary
        if end < len(words):
            # Look for sentence end in overlap zone
            overlap_zone = chunk_text[-overlap*5:]  # Approx chars
            sentence_ends = [m.end() for m in re.finditer(r'[.!?]\s+', overlap_zone)]
            if sentence_ends:
                # Cut at last sentence end in overlap zone
                cut_point = len(chunk_text) - len(overlap_zone) + sentence_ends[-1]
                chunk_text = chunk_text[:cut_point]
        
        chunks.append(chunk_text.strip())
        
        # Move start with overlap
        start += chunk_size - overlap
        
        # Prevent infinite loop
        if start >= end:
            start = end
    
    return chunks


def chunk_text_by_paragraphs(
    text: str,
    max_chunk_size: int = 800,
    min_chunk_size: int = 100
) -> List[str]:
    """
    Split text by paragraphs, respecting max size.
    Merges small paragraphs into larger chunks.
    """
    paragraphs = text.split('\n\n')
    paragraphs = [p.strip() for p in paragraphs if p.strip()]
    
    chunks = []
    current_chunk = []
    current_size = 0
    
    for para in paragraphs:
        para_words = len(para.split())
        
        if current_size + para_words <= max_chunk_size:
            current_chunk.append(para)
            current_size += para_words
        else:
            # Save current chunk
            if current_chunk:
                chunks.append('\n\n'.join(current_chunk))
            
            # Start new chunk
            if para_words > max_chunk_size:
                # Paragraph too big, split it
                words = para.split()
                for i in range(0, len(words), max_chunk_size):
                    chunk_words = words[i:i+max_chunk_size]
                    chunks.append(' '.join(chunk_words))
                current_chunk = []
                current_size = 0
            else:
                current_chunk = [para]
                current_size = para_words
    
    # Save last chunk
    if current_chunk:
        text = '\n\n'.join(current_chunk)
        if len(text.split()) >= min_chunk_size:
            chunks.append(text)
    
    return chunks


def chunk_source_document(
    doc_name: str,
    text: str,
    chunk_size: int = 800,
    overlap: int = 150
) -> List[dict]:
    """
    Chunk a source document and return chunks with metadata.
    
    Returns list of dicts with:
    - text: chunk text
    - source: document name
    - chunk_index: chunk number
    - totalChunks: total chunks in doc
    """
    chunks = chunk_text(text, chunk_size, overlap)
    
    return [
        {
            'text': chunk,
            'source': doc_name,
            'chunkIndex': i,
            'totalChunks': len(chunks),
            'charCount': len(chunk)
        }
        for i, chunk in enumerate(chunks)
    ]


if __name__ == '__main__':
    # Test
    sample = "This is sentence one. " * 100 + "\n\n" + "This is paragraph two. " * 100
    chunks = chunk_text(sample, chunk_size=50, overlap=10)
    print(f"Split {len(sample.split())} words into {len(chunks)} chunks")
    for i, c in enumerate(chunks):
        print(f"Chunk {i}: {len(c.split())} words")
