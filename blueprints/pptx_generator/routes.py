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
    
    If JSON parsing fails, LLM will be used to fix the structure.
    """
    try:
        raw_data = request.get_data(as_text=True)
        
        if not raw_data:
            return jsonify({'error': 'No data provided'}), 400
        
        # Try to parse as JSON first
        try:
            data = json.loads(raw_data)
        except json.JSONDecodeError:
            # If JSON parsing fails, use LLM to fix it
            logger.info("JSON parsing failed, attempting LLM fix")
            data = fix_json_with_llm(raw_data)
            
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
        
        # Generate images if descriptions provided
        slides_with_images = []
        for i, slide in enumerate(data):
            slide_copy = slide.copy()
            image_desc = slide.get('image_description', '')
            
            if image_desc:
                # Try to generate image using LLM
                try:
                    image_path = generate_image_with_llm(image_desc, images_dir, i)
                    if image_path:
                        slide_copy['image_path'] = image_path
                        logger.info(f"Generated image for slide {i}")
                except Exception as e:
                    logger.warning(f"Failed to generate image for slide {i}: {e}")
            
            slides_with_images.append(slide_copy)
        
        # Generate PPTX
        output_filename = f"presentation_{uuid.uuid4().hex[:8]}.pptx"
        output_path = os.path.join(temp_dir, output_filename)
        
        create_presentation(output_path, slides_with_images, images_dir)
        
        logger.info(f"Generated presentation: {output_path}")
        
        # Return the file
        return send_file(
            output_path,
            as_attachment=True,
            download_name=output_filename,
            mimetype='application/vnd.openxmlformats-officedocument.presentationml.presentation'
        )
        
    except Exception as e:
        logger.error(f"Error generating PPTX: {e}", exc_info=True)
        return jsonify({'error': f'Generation failed: {str(e)}'}), 500


def fix_json_with_llm(raw_text):
    """
    Use LLM to fix and format JSON structure for slides.
    
    Args:
        raw_text: raw text input that may not be valid JSON
    
    Returns:
        parsed JSON list or None if failed
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
            return data
        else:
            logger.warning("LLM returned non-array JSON")
            return None
            
    except Exception as e:
        logger.error(f"Failed to fix JSON with LLM: {e}")
        return None


def generate_image_with_llm(description, output_dir, slide_index):
    """
    Generate image using LLM based on description.
    Currently uses Gemini API if available.
    
    Args:
        description: text description of the image
        output_dir: directory to save the image
        slide_index: index of the slide for filename
    
    Returns:
        path to generated image or None if failed
    """
    try:
        from dotenv import load_dotenv
        load_dotenv()
        
        api_key = os.getenv('GEMINI_API_KEY')
        if not api_key:
            logger.warning("GEMINI_API_KEY not set, skipping image generation")
            return None
        
        # For now, we'll use a placeholder approach
        # In production, you would integrate with an image generation API
        # like DALL-E, Midjourney, or Gemini's image capabilities
        
        # Create a simple placeholder image with text
        from PIL import Image, ImageDraw, ImageFont
        
        img = Image.new('RGB', (800, 600), color='white')
        draw = ImageDraw.Draw(img)
        
        # Add description text
        try:
            font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 24)
        except:
            font = ImageFont.load_default()
        
        # Wrap text
        text = description[:100] + "..." if len(description) > 100 else description
        draw.text((50, 50), f"Slide {slide_index + 1}", fill='black', font=font)
        draw.text((50, 100), text, fill='gray', font=font)
        
        # Save image
        image_path = os.path.join(output_dir, f"slide_{slide_index}.png")
        img.save(image_path)
        
        return image_path
        
    except Exception as e:
        logger.error(f"Image generation failed: {e}")
        return None


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
