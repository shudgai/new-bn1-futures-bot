import json

log_file = "data/paper_account.json"
# We want to find the exact K-line data that triggered the entry.
# The logs unfortunately only printed Close, MA3, MA15, KC_Lower, KC_Upper.
# But we can see if there is any other log.
