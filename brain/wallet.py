import os
import requests
from datetime import datetime

SOLANA_RPC     = os.getenv("SOLANA_RPC_URL", "https://api.mainnet-beta.solana.com")
JUPITER_PRICE  = "https://api.jup.ag/price/v2"
TOKEN_PROGRAM  = "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"


def _rpc(method: str, params: list) -> dict:
    resp = requests.post(
        SOLANA_RPC,
        json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params},
        timeout=15
    )
    return resp.json()


def get_wallet_balance(address: str) -> dict:
    """SOL balance + SPL token holdings for an address."""
    sol_data   = _rpc("getBalance", [address])
    lamports   = sol_data.get("result", {}).get("value", 0)
    sol        = lamports / 1e9

    token_data = _rpc("getTokenAccountsByOwner", [
        address,
        {"programId": TOKEN_PROGRAM},
        {"encoding": "jsonParsed"}
    ])
    tokens = []
    for acct in token_data.get("result", {}).get("value", []):
        info      = acct.get("account", {}).get("data", {}).get("parsed", {}).get("info", {})
        mint      = info.get("mint", "")
        ui_amount = info.get("tokenAmount", {}).get("uiAmount", 0)
        decimals  = info.get("tokenAmount", {}).get("decimals", 0)
        if ui_amount and ui_amount > 0:
            tokens.append({"mint": mint, "amount": ui_amount, "decimals": decimals})

    return {"address": address, "sol": round(sol, 6), "tokens": tokens}


def get_wallet_history(address: str, limit: int = 10) -> dict:
    """Recent transactions for an address."""
    data = _rpc("getSignaturesForAddress", [address, {"limit": limit}])
    txs  = []
    for s in data.get("result", []):
        block_time = s.get("blockTime")
        ts = datetime.utcfromtimestamp(block_time).strftime("%Y-%m-%d %H:%M UTC") if block_time else None
        txs.append({
            "signature": s.get("signature", "")[:20] + "...",
            "time":      ts,
            "slot":      s.get("slot"),
            "error":     s.get("err"),
            "memo":      s.get("memo")
        })
    return {"address": address, "transactions": txs}


def get_token_price(token: str) -> dict:
    """Token price in USD via Jupiter Price API v2. Pass mint address (symbols not supported in v2)."""
    resp = requests.get(JUPITER_PRICE, params={"ids": token}, timeout=10)
    data = resp.json().get("data", {})
    if token in data:
        p = data[token]
        return {"token": token, "price_usd": p.get("price"), "mint": p.get("id")}
    # Try case-insensitive match
    for key, p in data.items():
        if key.lower() == token.lower():
            return {"token": key, "price_usd": p.get("price"), "mint": p.get("id")}
    return {"error": f"Token not found: {token}. Try a mint address or common symbol like SOL, USDC."}
