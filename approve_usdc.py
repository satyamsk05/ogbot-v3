import asyncio
import os
from dotenv import load_dotenv
from py_clob_client.client import ClobClient
from py_clob_client.clob_types import BalanceAllowanceParams, AssetType, ApiCreds

load_dotenv()

PRIVATE_KEY = os.getenv("POLYMARKET_PRIVATE_KEY")
WALLET_ADDRESS = os.getenv("FOUNDER_ADDRESS", "")
API_KEY = os.getenv("POLYMARKET_API_KEY")
API_SECRET = os.getenv("POLYMARKET_API_SECRET")
PASSPHRASE = os.getenv("POLYMARKET_PASSPHRASE")

async def approve_usdc():
    if not PRIVATE_KEY:
        print("❌ Error: 'POLYMARKET_PRIVATE_KEY' is missing in your .env file.")
        return

    print(f"Initializing USDC Approval for Polymarket (Polygon)...")
    
    sig_type = int(os.getenv("SIGNATURE_TYPE", "2"))
    
    client = ClobClient(
        host="https://clob.polymarket.com",
        key=PRIVATE_KEY,
        chain_id=137,
        signature_type=sig_type,
        funder=WALLET_ADDRESS if sig_type == 1 else None,
        creds=ApiCreds(
            api_key=API_KEY,
            api_secret=API_SECRET,
            api_passphrase=PASSPHRASE,
        )
    )
    
    # Check current allowance
    params = BalanceAllowanceParams(asset_type=AssetType.COLLATERAL)
    
    try:
        ba = client.get_balance_allowance(params)
        balance = float(ba.get('balance', 0)) / 1e6
        allowance = float(ba.get('allowance', 0)) / 1e6
        
        print(f"Current USDC Balance:   ${balance:.2f}")
        print(f"Current USDC Allowance: ${allowance:.2f}")
        
        # Approve if allowance is less than $1,000 (adjust as needed)
        if allowance < 1000:
            print("\nUpdating allowance to infinite for Polymarket contracts...")
            print("Note: If you have 6 contract permutations, py_clob_client manages the correct exchange address approval.")
            try:
                resp = client.update_balance_allowance(params)
                print(f"Approval Response: {resp}")
                print("✅ DONE! Allowance request sent to chain.")
            except Exception as e:
                print(f"❌ Approval failed: {e}")
                print("Make sure you have some POL (MATIC) in your wallet for gas fees!")
        else:
            print("\n✅ Allowance is already sufficient (> $1,000).")
    
    except Exception as e:
        print(f"❌ Failed to fetch balance/allowance: {e}")

if __name__ == "__main__":
    asyncio.run(approve_usdc())
