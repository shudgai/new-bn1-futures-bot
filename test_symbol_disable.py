import asyncio
from core.config import is_entry_disabled

def main():
    print(f"PEPE disabled: {is_entry_disabled('1000PEPE/USDT')}")
    print(f"LOBSTER disabled: {is_entry_disabled('1000LUNC/USDT')}")
    print(f"龙虾/USDT disabled: {is_entry_disabled('龙虾/USDT')}")

if __name__ == '__main__':
    main()
