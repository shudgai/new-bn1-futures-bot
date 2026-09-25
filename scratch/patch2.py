import sys

with open("core/paper_account.py", "r") as f:
    lines = f.readlines()

out_lines = []
for i, line in enumerate(lines):
    if "async def open_position" in line:
        out_lines.append(line)
    elif "def open_position" not in line and "self.log(" in line and "return False" in line:
        # just for safety, don't mess with it
        out_lines.append(line)
    else:
        out_lines.append(line)
        if "async def open_position" in lines[i-1] if i > 0 else False:
            out_lines.append('        import logging\n')
            out_lines.append('        logging.getLogger("uvicorn.error").info("ENTER PAPER OPEN_POSITION")\n')

with open("core/paper_account.py", "w") as f:
    f.writelines(out_lines)
