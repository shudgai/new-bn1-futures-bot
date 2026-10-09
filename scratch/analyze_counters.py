import json

def analyze():
    with open("scratch/development_summary.json", "r") as f:
        results = json.load(f)

    for m in ["A", "B", "C"]:
        print(f"Model {m} counters:")
        for k, v in results[m]["counters"].items():
            print(f"  {k}: {v}")
        print()

if __name__ == "__main__":
    analyze()
