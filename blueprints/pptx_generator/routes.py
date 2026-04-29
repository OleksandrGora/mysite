import os
import json
import uuid
import tempfile
import shutil
from datetime import datetime
from flask import request, jsonify, render_template, current_app, send_file, session, redirect, url_for
from loguru import logger
from functools import wraps
from . import pptx_generator_bp
from .create_pptx import create_presentation

# --- Authentication Decorator ---

def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'email' not in session:
            if request.is_json or '/api/' in request.path:
                return jsonify({'error': 'Unauthorized'}), 401
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function


# --- Routes ---

def extract_text_from_docx(file_path):
    """
    Extract text from DOCX file.
    
    Args:
        file_path: path to DOCX file
    
    Returns:
        extracted text as string
    """
    try:
        from docx import Document
        doc = Document(file_path)
        
        text_parts = []
        for para in doc.paragraphs:
            if para.text.strip():
                text_parts.append(para.text.strip())
        
        return '\n\n'.join(text_parts)
    except Exception as e:
        logger.error(f"Failed to extract text from DOCX: {e}")
        return None


@pptx_generator_bp.route('/')
@login_required
def index():
    """Main page with JSON input form."""
    return render_template('pptx_generator/index.html')


@pptx_generator_bp.route('/api/generate', methods=['POST'])
@login_required
def generate_pptx():
    """
    Generate PowerPoint presentation from JSON.
    
    Expected JSON format:
    [
        {
            "title": "Назва слайду",
            "body": "Основний текст слайду",
            "image_description": "Опис ілюстрації для генерації (опціонально)"
        }
    ]
    
    Query parameters:
        provider: 'google' (default) or 'openai' - image generation provider
    
    If JSON parsing fails, LLM will be used to fix the structure.
    """
    try:
        raw_data = request.get_data(as_text=True)
        
        if not raw_data:
            return jsonify({'error': 'No data provided'}), 400
        
        # Get image provider from query params (default: google)
        image_provider = request.args.get('provider', 'google').lower()
        if image_provider not in ['google', 'openai']:
            image_provider = 'google'
        
        # Get generate_images flag (default: true for backward compatibility)
        generate_images_param = request.args.get('generate_images', 'true').lower()
        generate_images = generate_images_param in ['true', '1', 'yes']
        
        logger.info(f"Using image provider: {image_provider}, generate_images: {generate_images}")
        
        # Track token usage across all LLM calls
        total_tokens = {'input_tokens': 0, 'output_tokens': 0, 'total_tokens': 0}
        
        # Try to parse as JSON first
        try:
            data = json.loads(raw_data)
        except json.JSONDecodeError:
            # If JSON parsing fails, use LLM to fix it
            logger.info("JSON parsing failed, attempting LLM fix")
            data, tokens = fix_json_with_llm(raw_data)
            
            # Add tokens from JSON fixing
            if tokens:
                total_tokens['input_tokens'] += tokens.get('input_tokens', 0)
                total_tokens['output_tokens'] += tokens.get('output_tokens', 0)
                total_tokens['total_tokens'] += tokens.get('total_tokens', 0)
            
            if data is None:
                return jsonify({'error': 'Invalid JSON and LLM fix failed. Please provide valid JSON or clear text.'}), 400
        
        if not isinstance(data, list):
            return jsonify({'error': 'Data must be an array of slide objects'}), 400
        
        # Validate slides structure
        for i, slide in enumerate(data):
            if not isinstance(slide, dict):
                return jsonify({'error': f'Slide {i} must be an object'}), 400
            if 'title' not in slide:
                return jsonify({'error': f'Slide {i} missing required field: title'}), 400
            if 'body' not in slide:
                return jsonify({'error': f'Slide {i} missing required field: body'}), 400
        
        # Create temporary directory for this generation
        temp_dir = tempfile.mkdtemp(prefix='pptx_gen_')
        images_dir = os.path.join(temp_dir, 'images')
        os.makedirs(images_dir, exist_ok=True)
        
        # Generate images if descriptions provided and generate_images is enabled
        slides_with_images = []
        for i, slide in enumerate(data):
            slide_copy = slide.copy()
            image_desc = slide.get('image_description', '')
            
            if image_desc and generate_images:
                # Try to generate image using selected provider
                try:
                    if image_provider == 'openai':
                        image_path, image_tokens = generate_image_with_openai(image_desc, images_dir, i)
                    else:
                        image_path, image_tokens = generate_image_with_llm(image_desc, images_dir, i)
                    
                    if image_path:
                        slide_copy['image_path'] = image_path
                        logger.info(f"Generated image for slide {i} using {image_provider}")
                        
                        # Add tokens from image generation
                        if image_tokens:
                            total_tokens['input_tokens'] += image_tokens.get('input_tokens', 0)
                            total_tokens['output_tokens'] += image_tokens.get('output_tokens', 0)
                            total_tokens['total_tokens'] += image_tokens.get('total_tokens', 0)
                            logger.info(f"Added image generation tokens for slide {i}: {image_tokens}")
                        
                except Exception as e:
                    logger.warning(f"Failed to generate image for slide {i} using {image_provider}: {e}")
            
            slides_with_images.append(slide_copy)
        
        # Generate PPTX
        output_filename = f"presentation_{uuid.uuid4().hex[:8]}.pptx"
        output_path = os.path.join(temp_dir, output_filename)
        
        create_presentation(output_path, slides_with_images, images_dir)
        
        logger.info(f"Generated presentation: {output_path}")
        
        # Create response with file and token usage header
        response = send_file(
            output_path,
            as_attachment=True,
            download_name=output_filename,
            mimetype='application/vnd.openxmlformats-officedocument.presentationml.presentation'
        )
        
        # Add token usage to response headers
        response.headers['X-Tokens-Used'] = json.dumps(total_tokens)
        
        return response
        
    except Exception as e:
        logger.error(f"Error generating PPTX: {e}", exc_info=True)
        return jsonify({'error': f'Generation failed: {str(e)}'}), 500


def fix_json_with_llm(raw_text):
    """
    Use LLM to fix and format JSON structure for slides.
    
    Args:
        raw_text: raw text input that may not be valid JSON
    
    Returns:
        tuple: (parsed JSON list or None, tokens_used dict or None)
    """
    try:
        from dotenv import load_dotenv
        load_dotenv()
        
        api_key = os.getenv('GEMINI_API_KEY')
        if not api_key:
            logger.warning("GEMINI_API_KEY not set, cannot fix JSON")
            return None
        
        from google import genai
        client = genai.Client(api_key=api_key)
        
        prompt = f"""Ти — експерт з форматування даних для PowerPoint презентацій.

Перетвори наступний текст на валідний JSON масив об'єктів для слайдів.

СТРУКТУРА КОЖНОГО СЛАЙДА:
- title: назва слайду (обов'язково)
- body: основний текст слайду (обов'язково)
- image_description: опис ілюстрації (опціонально)

ВИМОГИ:
- Поверни ТІЛЬКИ JSON масив, без додаткових пояснень
- Якщо текст не має чіткої структури слайдів — розділи його логічно на 3-5 слайдів
- Якщо є заголовки/підрозділи — використай їх як title
- Якщо текст короткий — створи 1 слайд
- Українська мова

ВХІДНИЙ ТЕКСТ:
{raw_text}

Поверни JSON:"""
        
        response = client.models.generate_content(
            model='gemini-2.5-pro',
            contents=prompt,
            config={"temperature": 0.3}
        )
        
        result_text = response.text.strip()
        
        # Extract token usage from response
        tokens_used = None
        try:
            if hasattr(response, 'usage_metadata') and response.usage_metadata:
                tokens_used = {
                    'input_tokens': getattr(response.usage_metadata, 'prompt_token_count', 0),
                    'output_tokens': getattr(response.usage_metadata, 'candidates_token_count', 0),
                    'total_tokens': getattr(response.usage_metadata, 'total_token_count', 0)
                }
        except Exception as token_err:
            logger.warning(f"Could not extract token usage: {token_err}")
        
        # Remove markdown code blocks if present
        if result_text.startswith('```json'):
            result_text = result_text[7:]
        if result_text.startswith('```'):
            result_text = result_text[3:]
        if result_text.endswith('```'):
            result_text = result_text[:-3]
        result_text = result_text.strip()
        
        # Parse the JSON
        data = json.loads(result_text)
        
        if isinstance(data, list):
            logger.info(f"Successfully fixed JSON with LLM: {len(data)} slides")
            return data, tokens_used
        else:
            logger.warning("LLM returned non-array JSON")
            return None, tokens_used
            
    except Exception as e:
        logger.error(f"Failed to fix JSON with LLM: {e}")
        return None, None


def generate_image_with_llm(description, output_dir, slide_index):
    """
    Generate image using Gemini AI based on description.
    
    Args:
        description: text description of the image
        output_dir: directory to save the image
        slide_index: index of the slide for filename
    
    Returns:
        tuple: (path to generated image or None, tokens_used dict or None)
    """
    try:
        from dotenv import load_dotenv
        load_dotenv()
        
        api_key = os.getenv('GEMINI_API_KEY')
        if not api_key:
            logger.warning("GEMINI_API_KEY not set, skipping image generation")
            return None, None
        
        from google import genai
        from google.genai import types
        import base64
        
        client = genai.Client(api_key=api_key)
        
        # Enhance the prompt for better image generation
        enhanced_prompt = f"""Create a clean, professional illustration for a presentation slide.
        
Description: {description}

Requirements:
- Simple, minimalist design
- Suitable for educational/business presentation
- Clear visual elements
- No text in the image
- Professional color palette"""
        
        logger.info(f"Generating image for slide {slide_index}: {description[:50]}...")
        
        # Generate image using Gemini
        response = client.models.generate_content(
            model='gemini-3.1-flash-image-preview',
            contents=enhanced_prompt,
            config=types.GenerateContentConfig(
                temperature=0.7,
                response_modalities=['Text', 'Image']
            )
        )
        
        # Extract image data from response
        image_data = None
        logger.info(f"Response candidates count: {len(response.candidates)}")
        
        if response.candidates and len(response.candidates) > 0:
            candidate = response.candidates[0]
            logger.info(f"Content parts count: {len(candidate.content.parts)}")
            
            for i, part in enumerate(candidate.content.parts):
                logger.info(f"Part {i}: type={type(part)}, text={getattr(part, 'text', None)[:50] if hasattr(part, 'text') and part.text else 'None'}")
                
                if hasattr(part, 'inline_data') and part.inline_data is not None:
                    image_data = part.inline_data.data
                    logger.info(f"Found inline_data for slide {slide_index}")
                    break
                elif hasattr(part, 'inlineData') and part.inlineData is not None:
                    image_data = part.inlineData.data
                    logger.info(f"Found inlineData for slide {slide_index}")
                    break
        
        if image_data is None:
            logger.warning(f"No image data returned for slide {slide_index}")
            return None
        
        # Save the generated image
        image_path = os.path.join(output_dir, f"slide_{slide_index}.png")
        with open(image_path, 'wb') as f:
            # Data is already base64 encoded string
            f.write(base64.b64decode(image_data) if isinstance(image_data, str) else image_data)
        
        # Extract token usage from response
        tokens_used = None
        try:
            if hasattr(response, 'usage_metadata') and response.usage_metadata:
                tokens_used = {
                    'input_tokens': getattr(response.usage_metadata, 'prompt_token_count', 0),
                    'output_tokens': getattr(response.usage_metadata, 'candidates_token_count', 0),
                    'total_tokens': getattr(response.usage_metadata, 'total_token_count', 0)
                }
                logger.info(f"Image generation tokens: {tokens_used}")
        except Exception as token_err:
            logger.warning(f"Could not extract image generation token usage: {token_err}")
        
        logger.info(f"Image saved to {image_path}")
        return image_path, tokens_used
        
    except Exception as e:
        logger.error(f"Image generation failed: {e}")
        return None, None


def generate_image_with_openai(description, output_dir, slide_index):
    """
    Generate image using OpenAI GPT Image 1 Mini based on description.
    
    Args:
        description: text description of the image
        output_dir: directory to save the image
        slide_index: index of the slide for filename
    
    Returns:
        tuple: (path to generated image or None, tokens_used dict or None)
    """
    try:
        from dotenv import load_dotenv
        load_dotenv()
        
        api_key = os.getenv('OPENAI_API_KEY')
        if not api_key:
            logger.warning("OPENAI_API_KEY not set, skipping image generation")
            return None, None
        
        from openai import OpenAI
        import base64
        
        client = OpenAI(api_key=api_key)
        
        # Enhance the prompt for better image generation
        enhanced_prompt = f"""Create a clean, professional illustration for a presentation slide.
        
Description: {description}

Requirements:
- Simple, minimalist design
- Suitable for educational/business presentation
- Clear visual elements
- No text in the image
- Professional color palette"""
        
        logger.info(f"Generating image with OpenAI for slide {slide_index}: {description[:50]}...")
        
        # Generate image using OpenAI GPT Image 1 Mini
        response = client.images.generate(
            model="gpt-image-1-mini",
            prompt=enhanced_prompt,
            n=1,
            size="1024x1024"
        )
        
        # Extract image data from response
        if response.data and len(response.data) > 0:
            image_data = response.data[0].b64_json
            if image_data:
                # Save the generated image
                image_path = os.path.join(output_dir, f"slide_{slide_index}.png")
                with open(image_path, 'wb') as f:
                    f.write(base64.b64decode(image_data))
                
                # Extract token usage from response
                tokens_used = None
                try:
                    if hasattr(response, 'usage') and response.usage:
                        tokens_used = {
                            'input_tokens': getattr(response.usage, 'prompt_tokens', 0),
                            'output_tokens': getattr(response.usage, 'completion_tokens', 0),
                            'total_tokens': getattr(response.usage, 'total_tokens', 0)
                        }
                        logger.info(f"OpenAI image generation tokens: {tokens_used}")
                except Exception as token_err:
                    logger.warning(f"Could not extract OpenAI image generation token usage: {token_err}")
                
                logger.info(f"OpenAI image saved to {image_path}")
                return image_path, tokens_used
        
        logger.warning(f"No image data returned from OpenAI for slide {slide_index}")
        return None, None
        
    except Exception as e:
        logger.error(f"OpenAI image generation failed: {e}")
        return None, None


@pptx_generator_bp.route('/api/validate', methods=['POST'])
@login_required
def validate_json():
    """Validate JSON structure without generating. If invalid, try LLM fix."""
    try:
        raw_data = request.get_data(as_text=True)
        
        if not raw_data:
            return jsonify({'error': 'No data provided'}), 400
        
        # Try to parse as JSON first
        try:
            data = json.loads(raw_data)
        except json.JSONDecodeError:
            # If JSON parsing fails, use LLM to fix it
            logger.info("JSON parsing failed in validate, attempting LLM fix")
            data = fix_json_with_llm(raw_data)
            
            if data is None:
                return jsonify({
                    'valid': False,
                    'errors': ['Invalid JSON and LLM fix failed. Please provide valid JSON or clear text.'],
                    'warnings': [],
                    'slide_count': 0,
                    'llm_fixed': False
                })
            
            return jsonify({
                'valid': True,
                'errors': [],
                'warnings': ['JSON was automatically fixed by LLM'],
                'slide_count': len(data),
                'llm_fixed': True,
                'fixed_data': data
            })
        
        if not isinstance(data, list):
            return jsonify({'error': 'Data must be an array of slide objects'}), 400
        
        errors = []
        warnings = []
        
        for i, slide in enumerate(data):
            if not isinstance(slide, dict):
                errors.append(f'Slide {i}: must be an object')
                continue
            
            if 'title' not in slide:
                errors.append(f'Slide {i}: missing required field "title"')
            elif not slide['title'].strip():
                warnings.append(f'Slide {i}: title is empty')
            
            if 'body' not in slide:
                errors.append(f'Slide {i}: missing required field "body"')
            elif not slide['body'].strip():
                warnings.append(f'Slide {i}: body is empty')
            
            if 'image_description' in slide and slide['image_description']:
                if len(slide['image_description']) < 10:
                    warnings.append(f'Slide {i}: image_description too short for good generation')
        
        return jsonify({
            'valid': len(errors) == 0,
            'errors': errors,
            'warnings': warnings,
            'slide_count': len(data),
            'llm_fixed': False
        })
        
    except Exception as e:
        logger.error(f"Validation error: {e}")
        return jsonify({'error': f'Validation failed: {str(e)}'}), 500


@pptx_generator_bp.route('/api/upload-docx', methods=['POST'])
@login_required
def upload_docx():
    """
    Upload and extract text from DOCX file.
    
    Returns:
        Extracted text from DOCX file
    """
    try:
        if 'file' not in request.files:
            return jsonify({'error': 'No file provided'}), 400
        
        file = request.files['file']
        
        if file.filename == '':
            return jsonify({'error': 'No file selected'}), 400
        
        if not file.filename.endswith('.docx'):
            return jsonify({'error': 'File must be a .docx file'}), 400
        
        # Save uploaded file temporarily
        temp_dir = tempfile.mkdtemp(prefix='docx_upload_')
        file_path = os.path.join(temp_dir, file.filename)
        file.save(file_path)
        
        # Extract text
        text = extract_text_from_docx(file_path)
        
        # Cleanup
        shutil.rmtree(temp_dir, ignore_errors=True)
        
        if text is None:
            return jsonify({'error': 'Failed to extract text from DOCX'}), 500
        
        logger.info(f"Extracted {len(text)} characters from DOCX")
        
        return jsonify({
            'success': True,
            'text': text,
            'filename': file.filename
        })
        
    except Exception as e:
        logger.error(f"DOCX upload error: {e}")
        return jsonify({'error': f'Upload failed: {str(e)}'}), 500
