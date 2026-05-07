#!/usr/bin/env python3
"""
Benchmark script for RAG pipeline speed testing.

Tests each step on real source files and reports timing.
Run: python -m blueprints.doc_generator.rag.benchmark

If speed is acceptable, we proceed with full implementation.
If too slow, the feature can be rolled back (see plans/rag-rollback-guide.md).
"""

import os
import sys
import time
import json
from pathlib import Path

# Add project root to path
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..'))
sys.path.insert(0, project_root)

from loguru import logger

# Configure logger for console output
logger.remove()
logger.add(sys.stderr, format="<level>{level:8}</level> | {message}", level="DEBUG")


def find_source_files() -> list:
    """Find source files in Tasks-data/Sources."""
    sources_dir = os.path.join(
        os.path.dirname(os.path.dirname(__file__)),
        'Tasks-data', 'Sources'
    )
    if not os.path.exists(sources_dir):
        print(f"❌ Sources directory not found: {sources_dir}")
        return []

    files = []
    for f in os.listdir(sources_dir):
        fpath = os.path.join(sources_dir, f)
        if os.path.isfile(fpath):
            files.append(fpath)

    return files


def extract_text(file_path: str) -> str:
    """Extract text from a source file."""
    ext = Path(file_path).suffix.lower()

    if ext == '.pdf':
        import fitz
        doc = fitz.open(file_path)
        text = ""
        for page in doc:
            text += page.get_text() + "\n\n"
        doc.close()
        return text.strip()
    elif ext == '.docx':
        from docx import Document
        doc = Document(file_path)
        return "\n\n".join(p.text for p in doc.paragraphs if p.text.strip())
    else:
        with open(file_path, 'r', encoding='utf-8') as f:
            return f.read()


def main():
    print("=" * 60)
    print("🔬 RAG Pipeline Benchmark")
    print("=" * 60)
    print()

    # Find source files
    source_files = find_source_files()
    if not source_files:
        print("❌ No source files found in Tasks-data/Sources/")
        sys.exit(1)

    print(f"📁 Found {len(source_files)} source file(s):")
    for f in source_files:
        size_kb = os.path.getsize(f) / 1024
        print(f"   • {os.path.basename(f)} ({size_kb:.0f} KB)")
    print()

    # Use the first file (or a specific one) for testing
    test_file = source_files[0]
    print(f"📄 Using: {os.path.basename(test_file)}")
    print()

    results = {}

    # ================================================================
    # Step 1: Extract text
    # ================================================================
    print("─" * 50)
    print("📖 Step 1: Text Extraction")
    print("─" * 50)

    t_start = time.time()
    text = extract_text(test_file)
    t_extract = time.time() - t_start

    char_count = len(text)
    word_count = len(text.split())
    results['extract'] = {
        'time_seconds': round(t_extract, 2),
        'chars': char_count,
        'words': word_count
    }
    print(f"   Time:     {t_extract:.2f}s")
    print(f"   Chars:    {char_count:,}")
    print(f"   Words:    {word_count:,}")
    print()

    # ================================================================
    # Step 2: Chunk
    # ================================================================
    print("─" * 50)
    print("✂️  Step 2: Chunking (paragraph mode)")
    print("─" * 50)

    from blueprints.doc_generator.rag.chunker import chunk_documents

    # Build sources_data format
    test_sources = {os.path.basename(test_file): {'text': text, 'char_count': char_count}}

    t_start = time.time()
    chunks = chunk_documents(
        sources_data=test_sources,
        min_chunk_size=100,
        max_chunk_size=800
    )
    t_chunk = time.time() - t_start

    results['chunk'] = {
        'time_seconds': round(t_chunk, 2),
        'total_chunks': len(chunks),
        'avg_words': round(sum(c.word_count for c in chunks) / len(chunks), 1) if chunks else 0
    }
    print(f"   Time:      {t_chunk:.2f}s")
    print(f"   Chunks:    {len(chunks)}")
    if chunks:
        print(f"   Avg words: {results['chunk']['avg_words']}")
        print(f"   Samples:")
        for c in chunks[:3]:
            preview = c.text[:60].replace('\n', ' ')
            print(f"     [{c.chunk_index}] ({c.word_count} слів) {preview}...")
    print()

    # ================================================================
    # Step 3: Embed
    # ================================================================
    print("─" * 50)
    print("🧠 Step 3: Embeddings (Ollama nomic-embed-text)")
    print("─" * 50)

    from blueprints.doc_generator.rag.embedder import Embedder, compute_sources_hash

    embedder = Embedder()
    test_hash = compute_sources_hash([test_file])

    t_start = time.time()
    if not chunks:
        print("   ⚠ No chunks to embed")
    else:
        # Test with smaller chunks first if original chunks are large
        # nomic-embed-text has 2048 token limit; Cyrillic text uses more tokens
        test_texts = []
        for c in chunks:
            # Take first 150 words of each chunk to stay within token limits
            words = c.text.split()
            if len(words) > 150:
                test_texts.append(' '.join(words[:150]))
            else:
                test_texts.append(c.text)

        import requests
        from blueprints.doc_generator.rag.embedder import BATCH_SIZE, OLLAMA_BASE_URL, EMBEDDING_MODEL

        total_batches = (len(test_texts) + BATCH_SIZE - 1) // BATCH_SIZE
        all_embeddings = []

        print(f"   Batches:   {total_batches} (batch size={BATCH_SIZE})")
        print(f"   Embedding: {EMBEDDING_MODEL}")

        for i in range(0, len(test_texts), BATCH_SIZE):
            batch = test_texts[i:i + BATCH_SIZE]
            t_batch = time.time()

            try:
                response = requests.post(
                    f"{OLLAMA_BASE_URL}/api/embed",
                    json={'model': EMBEDDING_MODEL, 'input': batch},
                    timeout=120
                )
                response.raise_for_status()
                data = response.json()
                batch_embeddings = data.get('embeddings', [])
                all_embeddings.extend(batch_embeddings)
                tb = time.time() - t_batch
                print(f"     Batch {i // BATCH_SIZE + 1}/{total_batches}: "
                      f"{len(batch)} texts → {len(batch_embeddings)} vectors ({tb:.2f}s)")

                # Small delay between batches
                if i + BATCH_SIZE < len(test_texts):
                    time.sleep(0.3)

            except Exception as e:
                print(f"     ❌ Batch {i // BATCH_SIZE + 1} failed: {e}")
                # Try one at a time to isolate problematic texts
                for j, single_text in enumerate(batch):
                    try:
                        resp = requests.post(
                            f"{OLLAMA_BASE_URL}/api/embed",
                            json={'model': EMBEDDING_MODEL, 'input': [single_text]},
                            timeout=120
                        )
                        resp.raise_for_status()
                        data = resp.json()
                        emb = data.get('embeddings', [])
                        if emb:
                            all_embeddings.append(emb[0])
                            print(f"       Single {j} OK ({len(single_text.split())} words)")
                        else:
                            print(f"       Single {j} FAILED (empty response, {len(single_text.split())} words)")
                    except Exception as e2:
                        print(f"       Single {j} FAILED: {e2}")

        t_embed = time.time() - t_start
        emb_dim = len(all_embeddings[0]) if all_embeddings else 0
        results['embed'] = {
            'time_seconds': round(t_embed, 2),
            'vectors': len(all_embeddings),
            'dimension': emb_dim
        }
        print(f"   Total time: {t_embed:.2f}s")
        print(f"   Vectors:    {len(all_embeddings)} x {emb_dim}d")
    print()

    # ================================================================
    # Step 4: ChromaDB store + search
    # ================================================================
    print("─" * 50)
    print("🗄️  Step 4: ChromaDB (store + search)")
    print("─" * 50)

    t_start = time.time()
    try:
        collection_name = embedder.build(
            chunks=chunks,
            sources_hash=test_hash,
            source_files=[os.path.basename(test_file)]
        )
        t_store = time.time() - t_start
        results['chromadb_store'] = {
            'time_seconds': round(t_store, 2),
            'collection': collection_name
        }
        print(f"   Store:     {t_store:.2f}s")
        print(f"   Collection: {collection_name}")

        # Test search
        t_start = time.time()
        test_queries = [
            "Мета роботи лабораторної",
            "Результати досліджень",
            "Висновки"
        ]
        for q in test_queries:
            tb = time.time()
            results_search = embedder.search(
                query=q,
                sources_hash=test_hash,
                top_k=2,
                threshold=0.0
            )
            ts = time.time() - tb
            print(f"   Search '{q[:30]}...': {len(results_search)} results ({ts:.2f}s)")
            for r in results_search[:2]:
                preview = r['text'][:50].replace('\n', ' ')
                print(f"     → [{r['source']}:{r['chunk_index']}] "
                      f"sim={r['similarity']:.3f} '{preview}...'")

        t_search_total = time.time() - t_start
        results['chromadb_search'] = {
            'time_seconds': round(t_search_total, 2),
            'queries': len(test_queries)
        }

    except Exception as e:
        print(f"   ❌ ChromaDB failed: {e}")
        import traceback
        traceback.print_exc()
    print()

    # ================================================================
    # Summary
    # ================================================================
    print("=" * 60)
    print("📊 BENCHMARK SUMMARY")
    print("=" * 60)

    total = 0
    for step, data in results.items():
        t = data.get('time_seconds', 0)
        total += t
        step_name = {
            'extract': '1. Text Extraction',
            'chunk': '2. Chunking',
            'embed': '3. Embeddings (Ollama)',
            'chromadb_store': '4a. ChromaDB Store',
            'chromadb_search': '4b. ChromaDB Search',
        }.get(step, step)
        extra = ""
        if step == 'extract':
            extra = f" ({data.get('words', 0)} слів)"
        elif step == 'chunk':
            extra = f" ({data.get('total_chunks', 0)} чанків)"
        elif step == 'embed':
            extra = f" ({data.get('vectors', 0)} векторів)"
        print(f"   {step_name:25s}  {t:7.2f}s{extra}")

    print(f"   {'─' * 35}")
    print(f"   {'TOTAL':25s}  {total:7.2f}s")

    # File size info
    file_size_kb = os.path.getsize(test_file) / 1024
    print(f"\n   File size: {file_size_kb:.0f} KB")

    print()
    if total < 10:
        print("✅  VERY FAST — RAG is viable")
    elif total < 30:
        print("✅  ACCEPTABLE — RAG can work with caching")
    elif total < 60:
        print("⚠️  SLOW — consider larger chunks, smaller overlap")
    else:
        print("❌  TOO SLOW — RAG may not be practical on this hardware")

    print()
    print("Results JSON:")
    print(json.dumps(results, indent=2, ensure_ascii=False))


if __name__ == '__main__':
    main()
