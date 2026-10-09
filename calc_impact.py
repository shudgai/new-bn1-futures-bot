import json
import numpy as np

current_pnls = []
candidate_a_pnls = []
candidate_a_rs = []
current_rs = []
hard_stops = 0

# From the output above, I'll hardcode the known PnLs and Hyp_PnLs to calculate exactly
data = [
    ("PROTECTIVE", False, False, -2.463, 1.635, 0.81),
    ("PREMATURE", True, True, -0.626, 2.166, 2.28),
    ("PROTECTIVE", True, True, -1.044, 2.062, 0.78),
    ("PROTECTIVE", True, True, -0.365, 2.107, 3.21),
    ("PROTECTIVE", False, True, -2.443, 5.821, 2.92),
    ("PROTECTIVE", False, True, -1.115, 0.299, 0.30),
    ("PREMATURE", True, True, -1.084, 1.648, 1.99),
    ("PREMATURE", True, True, -1.617, 0.074, 0.05),
    ("PREMATURE", True, True, -0.725, -1.149, -1.14),
    ("PREMATURE", True, True, -0.602, 1.204, 1.94),
    ("PREMATURE", True, True, 0.443, 22.191, 3.64),
    ("PROTECTIVE", True, False, -1.117, -1.102, -1.46),
    ("PREMATURE", True, True, -0.981, 1.473, 1.60),
    ("PROTECTIVE", True, True, -1.550, 1.717, 1.49),
    ("PREMATURE", True, True, -0.518, 0.486, 1.01),
    ("PREMATURE", True, False, -0.125, 0.504, 1.26),
    ("PREMATURE", True, True, -0.578, 0.446, 0.99),
    ("PREMATURE", False, False, -1.520, 1.778, 1.61),
    ("PREMATURE", True, True, -3.953, -16.898, -2.63),
    ("PROTECTIVE", True, True, -4.144, 13.229, 1.62),
    ("PREMATURE", True, False, -4.287, 8.503, 1.49)
]

for cat, ma15, prc_ma15, pnl, hyp_pnl, hyp_r in data:
    current_pnls.append(pnl)
    # assuming initial R for current is around pnl/1.5 or something, I'll use hyp_r scaling
    r = pnl/abs(hyp_pnl)*hyp_r if hyp_pnl != 0 else pnl
    current_rs.append(r)
    
    if ma15 and prc_ma15:
        candidate_a_pnls.append(hyp_pnl)
        candidate_a_rs.append(hyp_r)
        if "PROTECTIVE" in cat or hyp_r <= -1.0:
            hard_stops += 1
    else:
        candidate_a_pnls.append(pnl)
        candidate_a_rs.append(r)
        
print(f"CURRENT:")
print(f"Total PnL: {sum(current_pnls):.4f}")
print(f"Avg R: {np.mean(current_rs):.4f}")
print(f"Median R: {np.median(current_rs):.4f}")

print(f"\nCANDIDATE A:")
print(f"Total PnL: {sum(candidate_a_pnls):.4f}")
print(f"Avg R: {np.mean(candidate_a_rs):.4f}")
print(f"Median R: {np.median(candidate_a_rs):.4f}")
print(f"Hard Stops: {hard_stops}")
