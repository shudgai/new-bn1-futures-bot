with open("core/engine.py", "r") as f:
    lines = f.readlines()

out = []
i = 0
while i < len(lines):
    line = lines[i]
    
    # Locate where `cr_info` is initialized and `has_pos` is checked
    if "cr_info = detect_ma5_ma25_cross_and_turn(df_cr_signal)" in line:
        # We want to insert `has_pos` and `curr_side` BEFORE cr_info evaluation
        # and ALSO the defense block!
        pass
    
    out.append(line)
    i += 1
