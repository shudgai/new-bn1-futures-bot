import re
with open('tests/test_v9_core_logic.py', 'r') as f:
    content = f.read()

content = content.replace('''            "volume": 100, "vol_ma_5": 50
        })
    ]''', '''            "volume": 100, "vol_ma_5": 50
        }),
        _default_row()
    ]''')

with open('tests/test_v9_core_logic.py', 'w') as f:
    f.write(content)

