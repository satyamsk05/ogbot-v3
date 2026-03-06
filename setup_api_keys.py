import os
from dotenv import load_dotenv
from py_clob_client.client import ClobClient
from py_clob_client.clob_types import ApiCreds

load_dotenv()

PRIVATE_KEY = os.getenv("POLYMARKET_PRIVATE_KEY")
WALLET_ADDRESS = os.getenv("WALLET_ADDRESS", "") 

def setup_api_keys():
    if not PRIVATE_KEY:
        print("❌ Error: 'POLYMARKET_PRIVATE_KEY' is missing in your .env file.")
        print("Please add your private key to .env before generating Polymarket API keys.")
        return

    print("Generating new Polymarket API keys for your wallet...")
    sig_type = int(os.getenv("SIGNATURE_TYPE", "1"))

    # Initialize a temporary client just for deriving keys
    # To create API keys, py_clob_client requires the host and chain_id
    client = ClobClient(
        host="https://clob.polymarket.com",
        key=PRIVATE_KEY,
        chain_id=137, # Polygon Mainnet
        signature_type=sig_type,
        funder=WALLET_ADDRESS if sig_type == 1 else None
    )

    try:
        # Create API Key (Requires signing multiple messages behind the scenes)
        # This will register your wallet with the Polymarket Relay & API
        creds: ApiCreds = client.create_or_derive_api_creds()
        
        print("\n✅ Successfully generated Polymarket API Keys!")
        print("-" * 50)
        print("Add the following to your .env file:\n")
        print(f"POLYMARKET_API_KEY={creds.api_key}")
        print(f"POLYMARKET_API_SECRET={creds.api_secret}")
        print(f"POLYMARKET_PASSPHRASE={creds.api_passphrase}")
        print("-" * 50)
        print("After adding these, your bot will stop giving 'Unauthorized' errors!")

    except Exception as e:
        print(f"❌ Failed to generate API keys: {e}")

if __name__ == "__main__":
    setup_api_keys()
