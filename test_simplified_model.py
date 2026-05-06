#!/usr/bin/env python3
"""
Test simplified model system (environment variable only)
"""
import sys
import os
sys.path.append('/home/openclaw/mysite')

from blueprints.doc_generator.config import (
    AVAILABLE_MODELS, REASONING_MODEL, get_model_provider,
    GEMINI_API_KEY, OPENAI_API_KEY
)

def test_environment_only():
    """Test that model is loaded only from environment."""
    print("=== Testing Environment-Only Model System ===")
    
    print(f"Current REASONING_MODEL: {REASONING_MODEL}")
    print(f"Available models: {len(AVAILABLE_MODELS)}")
    
    # Check if current model is valid
    if REASONING_MODEL in AVAILABLE_MODELS:
        print(f"✅ Current model is valid: {REASONING_MODEL}")
        provider = get_model_provider(REASONING_MODEL)
        print(f"🔧 Provider: {provider}")
        
        # Check API keys
        if provider == 'gemini':
            print(f"Gemini API Key: {'✅ Configured' if GEMINI_API_KEY else '❌ Missing'}")
        elif provider == 'openai':
            print(f"OpenAI API Key: {'✅ Configured' if OPENAI_API_KEY else '❌ Missing'}")
        else:
            print(f"❌ Unknown provider: {provider}")
    else:
        print(f"❌ Invalid model: {REASONING_MODEL}")

def test_environment_override():
    """Test environment variable override."""
    print(f"\n=== Testing Environment Override ===")
    
    # Save original
    original = os.environ.get('REASONING_MODEL')
    print(f"Original model: {original}")
    
    # Test override
    test_models = ['gpt-5-nano', 'gemini-2.5-pro', 'gpt-4o']
    
    for model in test_models:
        os.environ['REASONING_MODEL'] = model
        
        # Reload config to test
        try:
            import importlib
            import blueprints.doc_generator.config
            importlib.reload(blueprints.doc_generator.config)
            
            from blueprints.doc_generator.config import REASONING_MODEL as TEST_MODEL
            print(f"Set {model:15} -> Loaded {TEST_MODEL:15} {'✅' if TEST_MODEL == model else '❌'}")
            
        except Exception as e:
            print(f"❌ Error testing {model}: {e}")
    
    # Restore original
    if original:
        os.environ['REASONING_MODEL'] = original
    else:
        os.environ.pop('REASONING_MODEL', None)
    
    print(f"Restored original: {original}")

def test_api_keys():
    """Test API key configuration."""
    print(f"\n=== Testing API Keys ===")
    
    print(f"GEMINI_API_KEY: {'✅ Set' if GEMINI_API_KEY else '❌ Not set'}")
    print(f"OPENAI_API_KEY: {'✅ Set' if OPENAI_API_KEY else '❌ Not set'}")
    
    # Test provider detection
    models_to_test = ['gemini-2.5-pro', 'gpt-5-nano', 'gpt-4o-mini']
    
    for model in models_to_test:
        provider = get_model_provider(model)
        print(f"{model:20} -> {provider}")
        
        # Check if corresponding API key exists
        if provider == 'gemini' and not GEMINI_API_KEY:
            print(f"  ⚠️  Missing Gemini API key")
        elif provider == 'openai' and not OPENAI_API_KEY:
            print(f"  ⚠️  Missing OpenAI API key")

def test_model_list():
    """Test available models list."""
    print(f"\n=== Available Models ===")
    
    gemini_models = [m for m in AVAILABLE_MODELS if m.startswith('gemini')]
    openai_models = [m for m in AVAILABLE_MODELS if m.startswith('gpt')]
    
    print(f"Gemini models ({len(gemini_models)}):")
    for model in gemini_models:
        print(f"  - {model}")
    
    print(f"\nOpenAI models ({len(openai_models)}):")
    for model in openai_models:
        print(f"  - {model}")

def test_no_file_persistence():
    """Verify that no file persistence is used."""
    print(f"\n=== Testing No File Persistence ===")
    
    config_dir = os.path.dirname('/home/openclaw/mysite/blueprints/doc_generator/config.py')
    settings_file = os.path.join(config_dir, 'model_settings.json')
    
    if os.path.exists(settings_file):
        print(f"⚠️  Settings file still exists: {settings_file}")
        print("  This should be removed for environment-only system")
    else:
        print("✅ No settings file found (correct for environment-only system)")

if __name__ == '__main__':
    print("🔧 Simplified Model System Test")
    print("=" * 50)
    
    test_environment_only()
    test_model_list()
    test_api_keys()
    test_environment_override()
    test_no_file_persistence()
    
    print(f"\n📋 Summary:")
    print(f"- System type: Environment variable only")
    print(f"- Current model: {REASONING_MODEL}")
    print(f"- Total available models: {len(AVAILABLE_MODELS)}")
    print(f"- Gemini models: {len([m for m in AVAILABLE_MODELS if m.startswith('gemini')])}")
    print(f"- OpenAI models: {len([m for m in AVAILABLE_MODELS if m.startswith('gpt')])}")
    
    print(f"\n🎯 To change model:")
    print(f"export REASONING_MODEL=gpt-5-nano")
    print(f"export REASONING_MODEL=gemini-2.5-pro")
    print(f"export REASONING_MODEL=gpt-4o")
    
    print(f"\n💡 Model is loaded from environment variable only!")
    print(f"💡 No file persistence, no database, just simple env var!")
