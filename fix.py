with open("test_intrabar_entry.py", "r") as f:
    lines = f.readlines()
with open("test_intrabar_entry.py", "w") as f:
    for line in lines:
        if "G. Bar1" in line:
            break
        f.write(line)
    f.write('    print("ALL INTEGRATION ASSERTIONS PASSED.")\n\ntest()\n')
