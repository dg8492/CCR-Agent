import os
import sys
import re
from pathlib import Path


def _get_docs_paths():
    """Return document search paths. In frozen (packaged) builds, only use docs/ next to the exe."""
    if getattr(sys, 'frozen', False):
        exe_dir = os.path.dirname(sys.executable)
        return [os.path.join(exe_dir, 'docs')]
    # Dev mode: also scan Derrick's OneDrive research folder
    return [
        'docs/',
        'C:/Users/derri/OneDrive/Desktop/CCR Research/',
    ]


DOCS_PATHS = _get_docs_paths()

SKIP_DIRS = {'ClaudeCode Memory', '__pycache__', '.git', 'node_modules'}
SKIP_EXTS = {'.py', '.png', '.jpg', '.jpeg', '.gif', '.csv', '.gitignore', '.gitkeep'}


def extract_pdf_text(filepath):
    try:
        import pypdf
        text = ""
        with open(filepath, 'rb') as f:
            reader = pypdf.PdfReader(f)
            for page in reader.pages:
                extracted = page.extract_text()
                if extracted:
                    text += extracted + "\n"
        return text.strip()
    except Exception as e:
        print(f"  [PDF error] {filepath}: {e}")
        return ""


def extract_docx_text(filepath):
    try:
        from docx import Document
        doc = Document(filepath)
        return "\n".join(p.text for p in doc.paragraphs if p.text.strip())
    except Exception as e:
        print(f"  [DOCX error] {filepath}: {e}")
        return ""


def extract_html_text(text):
    text = re.sub(r'<style[^>]*>.*?</style>', ' ', text, flags=re.DOTALL)
    text = re.sub(r'<script[^>]*>.*?</script>', ' ', text, flags=re.DOTALL)
    text = re.sub(r'<[^>]+>', ' ', text)
    return re.sub(r'\s+', ' ', text).strip()


def chunk_text(text, source, chunk_size=400, overlap=50):
    words = text.split()
    chunks = []
    step = max(1, chunk_size - overlap)  # guard against zero/negative step
    for i in range(0, len(words), step):
        chunk_words = words[i:i + chunk_size]
        if len(chunk_words) < 25:
            continue
        chunks.append({'text': ' '.join(chunk_words), 'source': source})
    return chunks


def load_all_documents():
    documents = []
    seen = set()

    for base_path in DOCS_PATHS:
        path = Path(base_path)
        if not path.exists():
            continue

        for filepath in path.rglob('*'):
            if not filepath.is_file():
                continue
            if any(skip in filepath.parts for skip in SKIP_DIRS):
                continue
            if filepath.suffix.lower() in SKIP_EXTS:
                continue
            if str(filepath) in seen:
                continue
            seen.add(str(filepath))

            ext = filepath.suffix.lower()
            text = ""

            if ext == '.pdf':
                text = extract_pdf_text(str(filepath))
            elif ext == '.docx':
                text = extract_docx_text(str(filepath))
            elif ext in {'.md', '.txt'}:
                try:
                    text = filepath.read_text(encoding='utf-8', errors='ignore')
                except Exception:
                    pass
            elif ext == '.html':
                try:
                    raw = filepath.read_text(encoding='utf-8', errors='ignore')
                    text = extract_html_text(raw)
                except Exception:
                    pass

            if text and len(text.strip()) > 100:
                documents.append({
                    'source': filepath.name,
                    'path': str(filepath),
                    'content': text
                })
                print(f"  Loaded: {filepath.name} ({len(text):,} chars)")

    return documents


def build_search_index(documents):
    from rank_bm25 import BM25Okapi

    all_chunks = []
    for doc in documents:
        all_chunks.extend(chunk_text(doc['content'], doc['source']))

    if not all_chunks:
        print("Warning: No document chunks indexed.")
        return None, []

    tokenized = [c['text'].lower().split() for c in all_chunks]
    bm25 = BM25Okapi(tokenized)
    print(f"Index built: {len(all_chunks)} chunks from {len(documents)} docs")
    return bm25, all_chunks


def search_documents(query, bm25, chunks, top_k=5):
    if bm25 is None or not chunks:
        return []

    scores = bm25.get_scores(query.lower().split())
    ranked = sorted(enumerate(scores), key=lambda x: x[1], reverse=True)[:top_k]

    return [
        {'text': chunks[i]['text'], 'source': chunks[i]['source'], 'score': float(s)}
        for i, s in ranked if s > 0.5  # filter noise — require meaningful relevance score
    ]
