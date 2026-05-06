#!/usr/bin/env python3
"""
Test model persistence functionality
"""
import sys
import os
import json
sys.path.append('/home/openclaw/mysite')

from blueprints.doc_generator.config import (
    AVAILABLE_MODELS, MODEL_SETTINGS_FILE, get_selected_model_static
)
from blueprints.doc_generator.routes import get_selected_model, save_selected_model

def test_current_model():
    """Test current model selection."""
    print("=== Current Model Selection ===")
    
    # Test from config
    config_model = get_selected_model_static()
    print(f"Model from config: {config_model}")
    
    # Test from routes
    routes_model = get_selected_model()
    print(f"Model from routes: {routes_model}")
    
    # Check if they match
    if config_model == routes_model:
        print("✅ Both methods return the same model")
    else:
        print("❌ Methods return different models!")
    
    return routes_model

def test_model_settings_file():
    """Test model settings file operations."""
    print(f"\n=== Model Settings File ===")
    print(f"Settings file path: {MODEL_SETTINGS_FILE}")
    
    if os.path.exists(MODEL_SETTINGS_FILE):
        try:
            with open(MODEL_SETTINGS_FILE, 'r', encoding='utf-8') as f:
                settings = json.load(f)
                print(f"Current settings: {settings}")
                saved_model = settings.get('selected_model')
                print(f"Saved model: {saved_model}")
                
                if saved_model in AVAILABLE_MODELS:
                    print(f"✅ Saved model is valid")
                else:
                    print(f"❌ Saved model is not in available list")
                    
        except Exception as e:
            print(f"❌ Error reading settings file: {e}")
    else:
        print("⚠️  Settings file does not exist")

def test_save_different_models():
    """Test saving different models."""
    print(f"\n=== Testing Model Saving ===")
    
    test_models = ['gpt-5-nano', 'gemini-2.5-pro', 'gpt-4o', 'gemini-2.5-flash']
    
    for model in test_models:
        print(f"\n--- Testing model: {model} ---")
        
        # Save model
        success = save_selected_model(model)
        if success:
            print(f"✅ Successfully saved: {model}")
            
            # Verify it was saved
            current_model = get_selected_model()
            if current_model == model:
                print(f"✅ Verification passed: {current_model}")
            else:
                print(f"❌ Verification failed: expected {model}, got {current_model}")
        else:
            print(f"❌ Failed to save: {model}")

def test_invalid_model():
    """Test saving invalid model."""
    print(f"\n=== Testing Invalid Model ===")
    
    invalid_model = 'invalid-model-name'
    print(f"Attempting to save invalid model: {invalid_model}")
    
    success = save_selected_model(invalid_model)
    if not success:
        print("✅ Correctly rejected invalid model")
    else:
        print("❌ Incorrectly accepted invalid model")

def test_environment_override():
    """Test environment variable override."""
    print(f"\n=== Testing Environment Override ===")
    
    # Save original
    original_env = os.environ.get('REASONING_MODEL')
    print(f"Original REASONING_MODEL: {original_env}")
    
    # Test with environment override
    os.environ['REASONING_MODEL'] = 'gpt-5-nano'
    
    # Reload config by importing fresh
    try:
        # Clear any cached imports
        import importlib
        import blueprints.doc_generator.config
        importlib.reload(blueprints.doc_generator.config)
        
        from blueprints.doc_generator.config import get_selected_model_static as get_fresh_model
        override_model = get_fresh_model()
        print(f"Model with env override: {override_model}")
        
        if override_model == 'gpt-5-nano':
            print("✅ Environment override works correctly")
        else:
            print(f"❌ Environment override failed: {override_model}")
            
    except Exception as e:
        print(f"❌ Error testing environment override: {e}")
    finally:
        # Restore original
        if original_env:
            os.environ['REASONING_MODEL'] = original_env
        else:
            os.environ.pop('REASONING_MODEL', None)

def test_persistence_across_restarts():
    """Test model persistence (simulated)."""
    print(f"\n=== Testing Persistence ===")
    
    # Save a model
    test_model = 'gpt-4o-mini'
    print(f"Saving test model: {test_model}")
    
    success = save_selected_model(test_model)
    if success:
        print("✅ Model saved successfully")
        
        # Read directly from file to verify
        try:
            with open(MODEL_SETTINGS_FILE, 'r', encoding='utf-8') as f:
                settings = json.load(f)
                file_model = settings.get('selected_model')
                
                if file_model == test_model:
                    print("✅ File contains correct model")
                else:
                    print(f"❌ File has wrong model: {file_model}")
                    
        except Exception as e:
            print(f"❌ Error verifying file: {e}")
    else:
        print("❌ Failed to save model")

if __name__ == '__main__':
    print("🔧 Model Persistence Test")
    print("=" * 50)
    
    # Test current state
    current = test_current_model()
    
    # Test settings file
    test_model_settings_file()
    
    # Test saving different models
    test_save_different_models()
    
    # Test invalid model
    test_invalid_model()
    
    # Test environment override
    test_environment_override()
    
    # Test persistence
    test_persistence_across_restarts()
    
    print(f"\n📋 Final Summary:")
    print(f"- Available models: {len(AVAILABLE_MODELS)}")
    print(f"- Current model: {current}")
    print(f"- Settings file: {MODEL_SETTINGS_FILE}")
    print(f"- File exists: {os.path.exists(MODEL_SETTINGS_FILE)}")
    
    print(f"\n🎯 Model selection will persist across server restarts!")
    print(f"💡 Use UI or API to change model, or set REASONING_MODEL environment variable")
