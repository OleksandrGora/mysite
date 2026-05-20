from playwright.sync_api import sync_playwright
with sync_playwright() as p:
    b = p.chromium.launch_persistent_context(
        '/home/openclaw/.chrome-moodle-profile',
        headless=False,
        channel='chrome',
        args=['--no-sandbox']
    )
    page = b.pages[0] if b.pages else b.new_page()
    page.goto('https://elearn.nubip.edu.ua/', wait_until='networkidle')
    print('OK:', page.url)
    b.close()
