"""
Configuration for document generator service.
"""
import os
from dotenv import load_dotenv

load_dotenv()

# Paths
DATA_DIR = os.getenv('DATA_DIR', '/home/openclaw/mysite/data')

# LLM Configuration
GEMINI_API_KEY = os.getenv('GEMINI_API_KEY')
REASONING_MODEL = os.getenv('REASONING_MODEL', 'gemini-2.5-flash-lite')
EMBEDDING_MODEL = os.getenv('EMBEDDING_MODEL', 'models/text-embedding-004')

# Generation Settings
MAX_TOKENS_PER_SECTION = 4000
MAX_TOKENS_FOR_TABLE = 2000
TEMPERATURE = 0.3  # Lower for more consistent output

# Feature Flags
USE_RAG = os.getenv('USE_RAG', 'false').lower() == 'true'

# Validate API key
def validate_config():
    if not GEMINI_API_KEY:
        raise ValueError("GEMINI_API_KEY not set in .env file")
