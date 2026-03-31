"""
Solana PumpFun Sniper Bot — Launchpad.Trade
=============================================
Full source code from the YouTube tutorial.

What this bot does:
1. Creates sniper wallets
2. Funds them from your main wallet
3. Initializes them for maximum speed
4. Monitors PumpFun in real-time via Solana RPC WebSocket
5. Auto-buys the first new token detected (or a filtered target)
6. Sells 100% — takes profit
7. Closes token accounts — recovers rent
8. Withdraws all SOL back to main wallet

Requirements:
    pip install requests websockets base58 python-dotenv

Setup:
    1. Get your API key at https://launchpad.trade
    2. Get a free Helius RPC key at https://www.helius.dev
    3. Copy .env.example to .env and fill in your keys
    4. Run: python sniper_bot.py

Documentation: https://docs.launchpad.trade
Discord: https://discord.com/invite/launchpad-trade
"""

import requests
import json
import os
import sys
import time
import asyncio
import base64
import struct
import threading
import logging
import base58
import websockets
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(message)s")
log = logging.getLogger(__name__)


# =============================================================================
# CONFIG — loaded from .env file (see .env.example)
# =============================================================================

API_KEY = os.getenv("LAUNCHPAD_API_KEY", "")
BASE_URL = "https://api.launchpad.trade"

MAIN_PRIVATE_KEY = os.getenv("MAIN_PRIVATE_KEY", "")

# Solana RPC WebSocket
SOLANA_WSS = os.getenv("SOLANA_WSS", "")

# PumpFun Program ID (bonding curve — where tokens are created)
PUMP_PROGRAM_ID = "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P"

# =============================================================================
# SNIPE SETTINGS
# =============================================================================

NUM_SNIPERS = 3           # Number of sniper wallets (max 50)
FUND_AMOUNT = 0.02        # SOL per sniper wallet
BUY_AMOUNT = 0.005        # SOL to spend per buy

# =============================================================================
# SNIPE FILTER (optional — leave empty to snipe ANY new token)
# =============================================================================

FILTER_CREATOR = ""       # Creator wallet address (e.g. "7xKXtg...")
FILTER_NAME = ""          # Token name contains (e.g. "DOGE")
FILTER_SYMBOL = ""        # Token symbol contains (e.g. "DOGE")

# =============================================================================
# CONSTANTS
# =============================================================================

API_TIMEOUT = 30                  # Seconds before API request times out
ANCHOR_DISCRIMINATOR_SIZE = 8     # Bytes to skip in Anchor event data
PUBKEY_SIZE = 32                  # Bytes in a Solana public key
SELL_DELAY = 3                    # Seconds to wait before selling
SELL_RETRIES = 3                  # Number of sell retry attempts
WS_RECONNECT_MAX_RETRIES = 5     # Max WebSocket reconnection attempts
WS_RECONNECT_BASE_DELAY = 1      # Base delay for reconnection backoff (seconds)

STATE_FILE = "state.json"
WALLETS_FILE = "wallets.json"


# =============================================================================
# --- Do not modify below this line ---
# =============================================================================

HEADERS = {
    "X-API-Key": API_KEY,
    "Content-Type": "application/json"
}

detected_token = None
snipe_ready = threading.Event()
tokens_seen = 0


# ---------------------------------------------------------------------------
# Key derivation
# ---------------------------------------------------------------------------

def derive_public_key(private_key_b58):
    """Derive the public key from a Solana private key (base58 keypair)."""
    key_bytes = base58.b58decode(private_key_b58)
    public_bytes = key_bytes[PUBKEY_SIZE:PUBKEY_SIZE * 2]
    return base58.b58encode(public_bytes).decode("utf-8")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def pause(msg="Press Enter to continue..."):
    input(f"\n-->  {msg}")
    print()


def is_success(data):
    return data.get("success") or data.get("status") == "success"


def api(method, path, body=None):
    url = f"{BASE_URL}{path}"
    try:
        if method == "GET":
            r = requests.get(url, headers=HEADERS, timeout=API_TIMEOUT)
        else:
            r = requests.post(url, headers=HEADERS, json=body, timeout=API_TIMEOUT)
        data = r.json()
    except requests.exceptions.Timeout:
        log.error(f"  [ERROR] Request timed out after {API_TIMEOUT}s: {path}")
        return {"success": False, "error": {"code": "TIMEOUT", "message": f"Request timed out after {API_TIMEOUT}s"}}
    except requests.exceptions.ConnectionError:
        log.error(f"  [ERROR] Connection failed: {path}")
        return {"success": False, "error": {"code": "CONNECTION_ERROR", "message": "Could not connect to API"}}
    except requests.exceptions.JSONDecodeError:
        log.error(f"  [ERROR] Invalid JSON response from {path} (HTTP {r.status_code})")
        return {"success": False, "error": {"code": "INVALID_RESPONSE", "message": f"Non-JSON response (HTTP {r.status_code})"}}

    if not is_success(data):
        err = data.get("error", {})
        log.error(f"  [ERROR] {err.get('code', 'UNKNOWN')} -- {err.get('message', data)}")
    return data


def load_state():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, "r") as f:
            return json.load(f)
    return {}


def save_state(state):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)


def load_wallets():
    if os.path.exists(WALLETS_FILE):
        with open(WALLETS_FILE, "r") as f:
            return json.load(f)
    return None


def save_wallets(data):
    with open(WALLETS_FILE, "w") as f:
        json.dump(data, f, indent=2)


def validate_config():
    """Validate that all required config values are set before running."""
    missing = []
    if not API_KEY:
        missing.append("LAUNCHPAD_API_KEY")
    if not MAIN_PRIVATE_KEY:
        missing.append("MAIN_PRIVATE_KEY")
    if not SOLANA_WSS:
        missing.append("SOLANA_WSS")
    if missing:
        log.error(f"  [ERROR] Missing environment variables: {', '.join(missing)}")
        log.error(f"  Copy .env.example to .env and fill in your keys.")
        sys.exit(1)


# ---------------------------------------------------------------------------
# PumpFun Log Parser
# ---------------------------------------------------------------------------

def parse_create_event(b64_data):
    """Parse a PumpFun Create event from base64 Program data log."""
    try:
        raw = base64.b64decode(b64_data)
        offset = ANCHOR_DISCRIMINATOR_SIZE

        def read_string(data, off):
            length = struct.unpack('<I', data[off:off + 4])[0]
            off += 4
            s = data[off:off + length].decode('utf-8', errors='replace').strip('\x00')
            off += length
            return s, off

        def read_pubkey(data, off):
            pk = base58.b58encode(data[off:off + PUBKEY_SIZE]).decode('utf-8')
            off += PUBKEY_SIZE
            return pk, off

        event = {}
        event['name'], offset = read_string(raw, offset)
        event['symbol'], offset = read_string(raw, offset)
        event['uri'], offset = read_string(raw, offset)
        event['mint'], offset = read_pubkey(raw, offset)
        event['bondingCurve'], offset = read_pubkey(raw, offset)
        event['creator'], offset = read_pubkey(raw, offset)
        return event
    except (struct.error, IndexError, UnicodeDecodeError) as e:
        log.warning(f"  [WARN] Failed to parse create event: {e}")
        return None


# ---------------------------------------------------------------------------
# STEP 1 — Health Check
# ---------------------------------------------------------------------------

def step_health():
    print("=" * 60)
    print("  STEP 1 — Health Check")
    print("=" * 60)
    data = api("GET", "/health")
    if is_success(data):
        info = data["data"]
        print(f"  [OK] API is live")
        print(f"     Status  : {info['status']}")
        print(f"     Version : {info['version']}")
        print(f"     Region  : {info['region']}")
    else:
        print("  [FAIL] API not responding.")
        sys.exit(1)


# ---------------------------------------------------------------------------
# STEP 2 — Create Sniper Wallets
# ---------------------------------------------------------------------------

def step_create_wallets(main_public_key):
    print("=" * 60)
    print(f"  STEP 2 — Create {NUM_SNIPERS} Sniper Wallets")
    print("=" * 60)

    existing = load_wallets()
    if existing and existing.get("snipers"):
        snipers = existing["snipers"]
        print(f"  [LOADED] {len(snipers)} sniper wallets from {WALLETS_FILE}")
        for i, w in enumerate(snipers):
            print(f"     Sniper #{i+1}: {w['publicKey']}")
        return snipers

    data = api("POST", "/wallets/create", {"count": NUM_SNIPERS})
    if not is_success(data):
        sys.exit(1)

    snipers = data["data"]["wallets"]
    save_wallets({
        "mainWallet": {"publicKey": main_public_key, "privateKey": MAIN_PRIVATE_KEY},
        "snipers": snipers
    })

    print(f"  [OK] {len(snipers)} sniper wallets created")
    for i, w in enumerate(snipers):
        print(f"     Sniper #{i+1}: {w['publicKey']}")
    print(f"  Private keys saved to {WALLETS_FILE}")
    return snipers


# ---------------------------------------------------------------------------
# STEP 3 — Fund Sniper Wallets
# ---------------------------------------------------------------------------

def step_fund(snipers):
    print("=" * 60)
    print(f"  STEP 3 — Fund {len(snipers)} Sniper Wallets ({FUND_AMOUNT} SOL each)")
    print("=" * 60)

    pub_keys = [w["publicKey"] for w in snipers]
    bal_data = api("POST", "/wallets/balance", {"publicKeys": pub_keys})

    to_fund = []
    if is_success(bal_data):
        for b in bal_data["data"]["balances"]:
            sol = b.get("sol", 0)
            print(f"     {b['wallet'][:20]}... : {sol} SOL")
            if sol < FUND_AMOUNT:
                to_fund.append(b["wallet"])

    if not to_fund:
        print(f"  [OK] All wallets already funded, skipping")
        return

    print(f"\n  -> Funding {len(to_fund)} wallet(s) with {FUND_AMOUNT} SOL each...")

    data = api("POST", "/funding/distribute", {
        "sourcePrivateKey": MAIN_PRIVATE_KEY,
        "destinationPublicKeys": to_fund,
        "amount": {"mode": "FIXED", "value": FUND_AMOUNT},
        "method": "DIRECT"
    })

    if is_success(data):
        summary = data["data"].get("summary", {})
        print(f"  [OK] Funded {summary.get('successCount', '?')}/{summary.get('totalWallets', '?')} wallets")
        print(f"     Total SOL sent: {summary.get('totalSolSent', '?')}")


# ---------------------------------------------------------------------------
# STEP 4 — Initialize Wallets for Speed
# ---------------------------------------------------------------------------

def step_init(snipers):
    print("=" * 60)
    print(f"  STEP 4 — Initialize Wallets for Maximum Speed")
    print("=" * 60)

    private_keys = [MAIN_PRIVATE_KEY] + [w["privateKey"] for w in snipers]
    print(f"  -> Initializing {len(private_keys)} wallets...")

    data = api("POST", "/wallets/init", {"privateKeys": private_keys})

    if is_success(data):
        for w in data["data"].get("initialized", []):
            status = "[OK]" if w["status"] in ("initialized", "already_initialized") else "[FAIL]"
            print(f"     {status} {w['wallet'][:20]}... -> {w['status']}")


# ---------------------------------------------------------------------------
# STEP 5 — Monitor PumpFun via Solana RPC WebSocket
# ---------------------------------------------------------------------------

def matches_filter(event):
    if not FILTER_CREATOR and not FILTER_NAME and not FILTER_SYMBOL:
        return True
    if FILTER_CREATOR:
        if event.get("creator", "").lower() != FILTER_CREATOR.lower():
            return False
    if FILTER_NAME:
        if FILTER_NAME.lower() not in event.get("name", "").lower():
            return False
    if FILTER_SYMBOL:
        if FILTER_SYMBOL.lower() not in event.get("symbol", "").lower():
            return False
    return True


async def monitor_pumpfun():
    global detected_token, tokens_seen

    for attempt in range(WS_RECONNECT_MAX_RETRIES):
        try:
            async with websockets.connect(SOLANA_WSS) as ws:
                subscribe = {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "logsSubscribe",
                    "params": [
                        {"mentions": [PUMP_PROGRAM_ID]},
                        {"commitment": "processed"}
                    ]
                }
                await ws.send(json.dumps(subscribe))

                response = json.loads(await ws.recv())
                sub_id = response.get("result")
                print(f"  [SUBSCRIBED] Solana RPC logsSubscribe (id: {sub_id})")
                print(f"  [LISTENING] Watching PumpFun for new tokens...")
                print()

                async for msg in ws:
                    data = json.loads(msg)
                    logs = (data.get("params", {})
                               .get("result", {})
                               .get("value", {})
                               .get("logs", []))

                    if not logs:
                        continue

                    joined = " ".join(logs)
                    if "CreateV2" not in joined:
                        continue

                    for log_line in logs:
                        if "Program data: " not in log_line:
                            continue

                        b64_data = log_line.split("Program data: ")[1]
                        event = parse_create_event(b64_data)

                        if not event:
                            continue

                        tokens_seen += 1
                        has_filter = FILTER_CREATOR or FILTER_NAME or FILTER_SYMBOL

                        if has_filter and not matches_filter(event):
                            print(f"  [SKIP #{tokens_seen}] {event['name']} ({event['symbol']}) — no match")
                            continue

                        print()
                        print(f"  *** TARGET FOUND! ***")
                        print(f"     Name     : {event['name']}")
                        print(f"     Symbol   : {event['symbol']}")
                        print(f"     Mint     : {event['mint']}")
                        print(f"     Creator  : {event['creator']}")
                        print(f"     Time     : {time.strftime('%H:%M:%S')}")
                        print(f"     PumpFun  : https://pump.fun/coin/{event['mint']}")
                        print(f"     Axiom    : https://axiom.trade/t/{event['mint']}")
                        print()

                        detected_token = {
                            "address": event["mint"],
                            "name": event["name"],
                            "symbol": event["symbol"],
                            "creator": event["creator"]
                        }
                        snipe_ready.set()
                        return

        except (websockets.exceptions.ConnectionClosed, ConnectionError, OSError) as e:
            delay = WS_RECONNECT_BASE_DELAY * (2 ** attempt)
            print(f"  [WS DISCONNECTED] {e} — reconnecting in {delay}s (attempt {attempt + 1}/{WS_RECONNECT_MAX_RETRIES})")
            await asyncio.sleep(delay)

    print(f"  [FATAL] WebSocket failed after {WS_RECONNECT_MAX_RETRIES} attempts")
    sys.exit(1)


def step_monitor():
    print("=" * 60)
    print("  STEP 5 — Monitoring PumpFun (Solana RPC WebSocket)")
    print("=" * 60)
    print()
    print(f"  Method   : logsSubscribe on {PUMP_PROGRAM_ID[:20]}...")
    print(f"  Commit   : processed (fastest)")
    print()

    has_filter = FILTER_CREATOR or FILTER_NAME or FILTER_SYMBOL
    if has_filter:
        print("  [FILTER ACTIVE]")
        if FILTER_CREATOR:
            print(f"     Creator : {FILTER_CREATOR}")
        if FILTER_NAME:
            print(f"     Name    : contains '{FILTER_NAME}'")
        if FILTER_SYMBOL:
            print(f"     Symbol  : contains '{FILTER_SYMBOL}'")
        print()
    else:
        print("  [NO FILTER] Will snipe the FIRST new token created")
        print()

    def run_monitor():
        asyncio.run(monitor_pumpfun())

    monitor_thread = threading.Thread(target=run_monitor, daemon=True)
    monitor_thread.start()
    snipe_ready.wait()
    return detected_token


# ---------------------------------------------------------------------------
# STEP 6 — THE SNIPE (Buy)
# ---------------------------------------------------------------------------

def step_snipe(snipers, token_address):
    print("=" * 60)
    print(f"  STEP 6 — SNIPE! Buy with {len(snipers)} wallets")
    print("=" * 60)

    print(f"  Token   : {token_address}")
    print(f"  Amount  : {BUY_AMOUNT} SOL per wallet")
    print(f"  Wallets : {len(snipers)}")
    print(f"  -> Sending buy order...")

    private_keys = [w["privateKey"] for w in snipers]

    start = time.time()
    data = api("POST", "/trading/instant/buy", {
        "tokenAddress": token_address,
        "privateKeys": private_keys,
        "amount": {"mode": "FIXED", "value": BUY_AMOUNT},
        "priorityFee": {"mode": "FAST"}
    })
    elapsed = time.time() - start

    if is_success(data):
        print(f"\n  RESULTS ({elapsed:.2f}s total):")
        print(f"  {'-'*50}")
        for tx in data["data"].get("transactions", []):
            if tx.get("status") == "confirmed":
                print(f"  [OK] {tx['wallet'][:20]}...")
                print(f"       SOL spent       : {tx.get('amountSol', '?')} SOL")
                print(f"       Tokens received : {tx.get('tokensReceived', '?')}")
                print(f"       Confirm latency : {tx.get('confirmLatency', '?')}ms")
            else:
                print(f"  [FAIL] {tx['wallet'][:20]}... -> {tx.get('error', '?')}")

        summary = data["data"].get("summary", {})
        print(f"\n  SUMMARY:")
        print(f"     Successful   : {summary.get('successful', '?')}/{summary.get('totalWallets', '?')}")
        print(f"     Total spent  : {summary.get('totalSolSpent', '?')} SOL")
        print(f"     Total tokens : {summary.get('totalTokensReceived', '?')}")
    else:
        print(f"  [DEBUG] {json.dumps(data, indent=2)}")
    return data


# ---------------------------------------------------------------------------
# STEP 7 — Check Token Balances
# ---------------------------------------------------------------------------

def step_check_tokens(snipers, token_address):
    print("=" * 60)
    print(f"  STEP 7 — Check Token Balances")
    print("=" * 60)

    pub_keys = [w["publicKey"] for w in snipers]
    data = api("POST", "/wallets/balance", {
        "publicKeys": pub_keys,
        "tokenAddress": token_address
    })

    if is_success(data):
        for b in data["data"]["balances"]:
            print(f"     {b['wallet'][:20]}... : {b.get('token', 0)} tokens | {b.get('sol', 0)} SOL")
        print(f"\n     Total tokens : {data['data'].get('totalToken', '?')}")
        print(f"     Total SOL    : {data['data'].get('totalSol', '?')}")


# ---------------------------------------------------------------------------
# STEP 8 — Take Profit (Sell 100%)
# ---------------------------------------------------------------------------

def step_sell(snipers, token_address):
    print("=" * 60)
    print(f"  STEP 8 — Take Profit! Sell 100%")
    print("=" * 60)

    print(f"  -> Waiting {SELL_DELAY}s for blockchain sync...")
    time.sleep(SELL_DELAY)

    private_keys = [w["privateKey"] for w in snipers]

    for attempt in range(SELL_RETRIES):
        print(f"  -> Selling 100% (attempt {attempt+1}/{SELL_RETRIES})...")

        start = time.time()
        data = api("POST", "/trading/instant/sell", {
            "tokenAddress": token_address,
            "privateKeys": private_keys,
            "amount": {"type": "PERCENT", "mode": "FIXED", "value": 100},
            "priorityFee": {"mode": "FAST"}
        })
        elapsed = time.time() - start

        if is_success(data):
            transactions = data["data"].get("transactions", [])

            if not transactions:
                print(f"  [WARN] No transactions returned — sell may have failed")
                if attempt < SELL_RETRIES - 1:
                    print(f"  -> Retrying in {SELL_DELAY}s...")
                    time.sleep(SELL_DELAY)
                continue

            all_confirmed = all(tx.get("status") == "confirmed" for tx in transactions)

            print(f"\n  RESULTS ({elapsed:.2f}s total):")
            print(f"  {'-'*50}")
            for tx in transactions:
                if tx.get("status") == "confirmed":
                    print(f"  [OK] {tx['wallet'][:20]}...")
                    print(f"       Tokens sold   : {tx.get('tokensSold', '?')}")
                    print(f"       SOL received  : {tx.get('solReceived', '?')} SOL")
                    print(f"       Confirm latency: {tx.get('confirmLatency', '?')}ms")
                else:
                    print(f"  [FAIL] {tx['wallet'][:20]}... -> {tx.get('error', '?')}")

            summary = data["data"].get("summary", {})
            print(f"\n  SUMMARY:")
            print(f"     Successful    : {summary.get('successful', '?')}/{summary.get('totalWallets', '?')}")
            print(f"     Total SOL back: {summary.get('totalSolReceived', '?')} SOL")

            if all_confirmed:
                return data

        if attempt < SELL_RETRIES - 1:
            print(f"  -> Retrying in {SELL_DELAY}s...")
            time.sleep(SELL_DELAY)

    return data


# ---------------------------------------------------------------------------
# STEP 9 — Close Token Accounts (Recover Rent)
# ---------------------------------------------------------------------------

def step_close_accounts(snipers):
    print("=" * 60)
    print(f"  STEP 9 — Close Token Accounts (Recover Rent)")
    print("=" * 60)

    private_keys = [w["privateKey"] for w in snipers]

    print(f"  -> Simulating close...")
    sim = api("POST", "/utilities/close-accounts", {
        "privateKeys": private_keys,
        "simulate": True
    })

    if is_success(sim):
        summary = sim["data"].get("summary", {})
        total = summary.get("totalAccountsToClose", 0)
        rent = summary.get("totalRentRecoverable", 0)
        print(f"     Accounts to close : {total}")
        print(f"     Rent recoverable  : {rent} SOL")

        if total == 0:
            print(f"  [OK] No accounts to close")
            return

    print(f"  -> Closing accounts...")
    data = api("POST", "/utilities/close-accounts", {
        "privateKeys": private_keys,
        "simulate": False
    })

    if is_success(data):
        summary = data["data"].get("summary", {})
        print(f"  [OK] Closed {summary.get('totalAccountsClosed', '?')} accounts")
        print(f"     Rent recovered: {summary.get('totalRentRecovered', '?')} SOL")


# ---------------------------------------------------------------------------
# STEP 10 — Withdraw Everything to Main Wallet
# ---------------------------------------------------------------------------

def step_withdraw(snipers, main_public_key):
    print("=" * 60)
    print(f"  STEP 10 — Withdraw All SOL to Main Wallet")
    print("=" * 60)

    private_keys = [w["privateKey"] for w in snipers]
    print(f"  -> Withdrawing from {len(snipers)} wallets...")

    data = api("POST", "/funding/withdraw", {
        "sourcePrivateKeys": private_keys,
        "destinationPublicKey": main_public_key,
        "amount": {"mode": "ALL"},
        "method": "DIRECT"
    })

    if is_success(data):
        summary = data["data"].get("summary", {})
        print(f"  [OK] Withdrawn from {summary.get('successCount', '?')}/{summary.get('totalWallets', '?')} wallets")
        print(f"     Total SOL recovered: {summary.get('totalSolReceived', '?')} SOL")

    bal = api("POST", "/wallets/balance", {"publicKeys": [main_public_key]})
    if is_success(bal):
        sol = bal["data"]["balances"][0].get("sol", 0)
        print(f"\n  Main wallet balance: {sol} SOL")


# ---------------------------------------------------------------------------
# MAIN
# ---------------------------------------------------------------------------

def main():
    validate_config()

    main_public_key = derive_public_key(MAIN_PRIVATE_KEY)

    print()
    print("=" * 60)
    print("  SOLANA SNIPER BOT — Powered by Launchpad.Trade")
    print("  Monitor → Detect → Snipe → Profit → Cleanup")
    print("=" * 60)
    print()
    print(f"  Main wallet : {main_public_key}")
    print(f"  Snipers     : {NUM_SNIPERS} wallets")
    print(f"  Buy amount  : {BUY_AMOUNT} SOL per wallet")
    print(f"  Detection   : Solana RPC logsSubscribe (processed)")
    print()

    # STEP 1
    pause("STEP 1: Check API connection (Enter)")
    step_health()

    # STEP 2
    pause("STEP 2: Create sniper wallets (Enter)")
    snipers = step_create_wallets(main_public_key)

    # STEP 3
    pause("STEP 3: Fund sniper wallets (Enter)")
    step_fund(snipers)

    # STEP 4
    pause("STEP 4: Initialize wallets for speed (Enter)")
    step_init(snipers)

    # STEP 5
    pause("STEP 5: Start monitoring PumpFun for new tokens (Enter)")
    token_data = step_monitor()

    token_address = token_data["address"]
    token_name = token_data["name"]
    token_symbol = token_data["symbol"]

    print(f"  TARGET ACQUIRED: {token_name} ({token_symbol})")
    print(f"  Address: {token_address}")
    print()

    state = load_state()
    state["tokenAddress"] = token_address
    state["tokenName"] = token_name
    save_state(state)

    # STEP 6 — Auto-buy (no pause, speed matters!)
    print("  [AUTO-SNIPING] No delay — buying NOW!")
    print()
    step_snipe(snipers, token_address)

    # STEP 7
    pause("STEP 7: Check token balances (Enter)")
    step_check_tokens(snipers, token_address)

    # STEP 8
    pause("STEP 8: Take profit — sell 100% (Enter)")
    step_sell(snipers, token_address)

    # STEP 9
    pause("STEP 9: Close token accounts (Enter)")
    step_close_accounts(snipers)

    # STEP 10
    pause("STEP 10: Withdraw all SOL to main wallet (Enter)")
    step_withdraw(snipers, main_public_key)

    print()
    print("=" * 60)
    print("  SNIPE COMPLETE!")
    print("=" * 60)
    print(f"  Token sniped : {token_name} ({token_symbol})")
    print("  All tokens sold. All SOL recovered.")
    print("  All token accounts closed.")
    print(f"  Wallet keys saved in {WALLETS_FILE}")
    print()


if __name__ == "__main__":
    main()
