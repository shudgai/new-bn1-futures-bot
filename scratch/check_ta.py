import ast

with open("core/engine.py", "r") as f:
    tree = ast.parse(f.read())

for node in ast.walk(tree):
    if isinstance(node, ast.Import):
        for alias in node.names:
            if alias.name == "pandas_ta":
                print(f"Imported {alias.name} as {alias.asname}")
    elif isinstance(node, ast.ImportFrom):
        if node.module == "pandas_ta":
            print(f"Imported from pandas_ta")
