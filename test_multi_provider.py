#!/usr/bin/env python3
"""
Test multi-provider support for Gemini and OpenAI models
"""
import sys
import os
sys.path.append('/home/openclaw/mysite')

from blueprints.doc_generator.config import (
    AVAILABLE_MODELS, get_model_provider, 
    is_gemini_model, is_openai_model,
    GEMINI_API_KEY, OPENAI_API_KEY,
    REASONING_MODEL, TEMPERATURE
)

def test_model_detection():
    """Test model provider detection functions."""
    print("=== Testing Model Provider Detection ===")
    
    test_models = [
        'gemini-2.5-pro',
        'gemini-2.5-flash',
        'gpt-5-nano',
        'gpt-4o',
        'gpt-4o-mini',
        'unknown-model'
    ]
    
    for model in test_models:
        provider = get_model_provider(model)
        is_gemini = is_gemini_model(model)
        is_openai = is_openai_model(model)
        
        print(f"{model:20} -> provider: {provider:8} | gemini: {is_gemini:5} | openai: {is_openai:5}")

def test_available_models():
    """Test available models list."""
    print(f"\n=== Available Models ({len(AVAILABLE_MODELS)}) ===")
    for i, model in enumerate(AVAILABLE_MODELS, 1):
        provider = get_model_provider(model)
        print(f"{i:2}. {model:20} ({provider})")

def test_api_keys():
    """Test API key configuration."""
    print(f"\n=== API Keys Configuration ===")
    print(f"GEMINI_API_KEY: {'✅ Configured' if GEMINI_API_KEY else '❌ Missing'}")
    print(f"OPENAI_API_KEY: {'✅ Configured' if OPENAI_API_KEY else '❌ Missing'}")
    print(f"REASONING_MODEL: {REASONING_MODEL}")

def test_client_initialization():
    """Test client initialization for different providers."""
    print(f"\n=== Testing Client Initialization ===")
    
    provider = get_model_provider(REASONING_MODEL)
    print(f"Current model: {REASONING_MODEL}")
    print(f"Detected provider: {provider}")
    
    if provider == 'gemini':
        try:
            import google.genai as genai
            if GEMINI_API_KEY:
                client = genai.Client(api_key=GEMINI_API_KEY)
                print("✅ Gemini client initialized successfully")
            else:
                print("❌ Gemini API key not configured")
        except Exception as e:
            print(f"❌ Failed to initialize Gemini client: {e}")
            
    elif provider == 'openai':
        try:
            from openai import OpenAI
            if OPENAI_API_KEY:
                client = OpenAI(api_key=OPENAI_API_KEY)
                print("✅ OpenAI client initialized successfully")
            else:
                print("❌ OpenAI API key not configured")
        except Exception as e:
            print(f"❌ Failed to initialize OpenAI client: {e}")
    else:
        print(f"❌ Unsupported provider: {provider}")

def test_prompt_formatting():
    """Test prompt formatting for different providers."""
    print(f"\n=== Testing Prompt Formatting ===")
    
    sample_prompt = "Проаналізуй структуру документа"
    
    # Gemini format
    gemini_format = sample_prompt
    
    # OpenAI format (chat messages)
    openai_format = [
        {"role": "system", "content": "Ти — експерт з аналізу структури документів. Відповідай українською мовою."},
        {"role": "user", "content": sample_prompt}
    ]
    
    print("Gemini format (direct prompt):")
    print(f"  {gemini_format}")
    print("\nOpenAI format (chat messages):")
    print(f"  System: {openai_format[0]['content']}")
    print(f"  User: {openai_format[1]['content']}")

def test_model_switching():
    """Test switching between different models."""
    print(f"\n=== Testing Model Switching ===")
    
    test_cases = [
        ('gemini-2.5-pro', 'gemini'),
        ('gpt-5-nano', 'openai'),
        ('gpt-4o', 'openai'),
        ('gemini-2.5-flash', 'gemini')
    ]
    
    for model, expected_provider in test_cases:
        detected = get_model_provider(model)
        status = "✅" if detected == expected_provider else "❌"
        print(f"{status} {model:20} -> {detected:8} (expected: {expected_provider})")

if __name__ == '__main__':
    print("🔧 Multi-Provider Support Test")
    print("=" * 50)
    
    test_model_detection()
    test_available_models()
    test_api_keys()
    test_client_initialization()
    test_prompt_formatting()
    test_model_switching()
    
    print(f"\n📋 Summary:")
    print(f"- Available models: {len(AVAILABLE_MODELS)}")
    print(f"- Gemini models: {len([m for m in AVAILABLE_MODELS if is_gemini_model(m)])}")
    print(f"- OpenAI models: {len([m for m in AVAILABLE_MODELS if is_openai_model(m)])}")
    print(f"- Current model: {REASONING_MODEL}")
    print(f"- Current provider: {get_model_provider(REASONING_MODEL)}")
    
    print(f"\n🎯 To test with specific model:")
    print(f"export REASONING_MODEL=gpt-5-nano")
    print(f"export REASONING_MODEL=gemini-2.5-pro")
    print(f"export OPENAI_API_KEY=your_openai_key")
    print(f"export GEMINI_API_KEY=your_gemini_key")
