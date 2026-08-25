import os
import re

def replace_in_file(filepath):
    if not os.path.exists(filepath):
        return
    with open(filepath, 'r') as f:
        content = f.read()
    
    # We want to replace MA3 and MA7 with MA5, ma3 and ma7 with ma5
    # Be careful not to replace something unintentionally. 
    # MA7_ -> MA5_, ma7 -> ma5, MA3_ -> MA5_, ma3 -> ma5
    # For strategy/indicators, we might have MA7<MA25, MA7>MA25 etc.
    
    # Let's just do a blanket replace for MA7->MA5, ma7->ma5, MA3->MA5, ma3->ma5
    # Is there any risk? Let's check if there are other words containing ma7 or ma3.
    # Like formatting strings? None known.
    
    new_content = content
    new_content = new_content.replace('MA7', 'MA5')
    new_content = new_content.replace('ma7', 'ma5')
    new_content = new_content.replace('MA3', 'MA5')
    new_content = new_content.replace('ma3', 'ma5')
    
    if new_content != content:
        with open(filepath, 'w') as f:
            f.write(new_content)
        print(f"Updated {filepath}")

files = [
    'core/config.py',
    '.env',
    '.env.example',
    'core/indicators.py',
    'core/strategy.py',
    'core/engine.py',
    'services/api.py',
    'web/index.html',
    'tests/test_bot.py',
    'tests/test_testnet_account.py'
]

for f in files:
    replace_in_file(f)

