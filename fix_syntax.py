#!/usr/bin/env python3

# Fix the syntax error in routes.py
with open('blueprints/pptx_generator/routes.py', 'r') as f:
    lines = f.readlines()

# Find and fix the broken line 294 (0-indexed as 293)
for i, line in enumerate(lines):
    if i == 293:  # Line 294
        # Replace the broken line with a proper one
        lines[i] = '- If text doesn\'t have clear slide structure - divide it logically into 3-5 slides\n'
        break

# Write back the fixed content
with open('blueprints/pptx_generator/routes.py', 'w') as f:
    f.writelines(lines)

print("Fixed line 294 syntax error")

# Test the syntax
try:
    with open('blueprints/pptx_generator/routes.py', 'r') as f:
        compile(f.read(), 'blueprints/pptx_generator/routes.py', 'exec')
    print("Syntax check passed!")
except SyntaxError as e:
    print(f"Syntax error still exists: {e}")
