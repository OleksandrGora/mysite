#!/usr/bin/env python3

# Fix the syntax error by replacing the entire f-string
with open('blueprints/pptx_generator/routes.py', 'r') as f:
    content = f.read()

# Replace the problematic f-string with a simpler version
old_prompt = '''                prompt = f"""You are an expert in formatting data for PowerPoint presentations.

Convert the following text into a valid JSON array of slide objects.

STRUCTURE FOR EACH SLIDE:
- title: slide title (required)
- body: main slide text (required)
- image_description: illustration description (optional)

REQUIREMENTS:
- Return ONLY JSON array, no additional explanations
- If text doesn't have clear slide structure - divide it logically into 3-5 slides
- If there are headings/subsections - use them as titles
- If text is short - create 1 slide
- Ukrainian language

INPUT TEXT:
{raw_text}

Return JSON:"""'''

new_prompt = '''                prompt = f'''You are an expert in formatting data for PowerPoint presentations.

Convert the following text into a valid JSON array of slide objects.

STRUCTURE FOR EACH SLIDE:
- title: slide title (required)
- body: main slide text (required)
- image_description: illustration description (optional)

REQUIREMENTS:
- Return ONLY JSON array, no additional explanations
- If text doesn't have clear slide structure - divide it logically into 3-5 slides
- If there are headings/subsections - use them as titles
- If text is short - create 1 slide
- Ukrainian language

INPUT TEXT:
{raw_text}

Return JSON:''' '''

# Replace the problematic section
content = content.replace(old_prompt, new_prompt)

# Write back the fixed content
with open('blueprints/pptx_generator/routes.py', 'w') as f:
    f.write(content)

print("Fixed f-string syntax error")

# Test the syntax
try:
    compile(content, 'blueprints/pptx_generator/routes.py', 'exec')
    print("Syntax check passed!")
except SyntaxError as e:
    print(f"Syntax error still exists: {e}")
