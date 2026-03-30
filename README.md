# PumpFun Sniper Bot — Solana

A Python sniper bot that detects new tokens on PumpFun in real-time and buys them automatically in milliseconds.

Built with [Launchpad.Trade](https://launchpad.trade) API + Solana RPC WebSocket.

## What it does

1. Creates sniper wallets
2. Funds them from your main wallet
3. Initializes them for maximum speed
4. Connects to Solana's blockchain via WebSocket
5. Monitors PumpFun for new token creations in real-time
6. **Auto-buys the instant a new token is detected**
7. Sells 100% — takes profit
8. Closes token accounts — recovers rent
9. Withdraws all SOL back to main wallet

## Requirements

- Python 3.8+
- A [Launchpad.Trade](https://launchpad.trade) API key (free)
- A [Helius](https://www.helius.dev) RPC key (free)
- ~0.1 SOL in your main wallet

## Installation

```bash
pip install requests websockets base58
```

## Setup

Open `sniper_bot.py` and fill in the CONFIG section:

```python
API_KEY = "YOUR_LAUNCHPAD_TRADE_API_KEY"
MAIN_PRIVATE_KEY = "YOUR_MAIN_WALLET_PRIVATE_KEY"
MAIN_PUBLIC_KEY = "YOUR_MAIN_WALLET_PUBLIC_KEY"
SOLANA_WSS = "wss://mainnet.helius-rpc.com/?api-key=YOUR_HELIUS_API_KEY"
```

## Run

```bash
python sniper_bot.py
```

Press Enter at each step. At Step 5, the bot monitors PumpFun and auto-buys the first new token detected.

## Snipe a specific token

To target a specific token launch, set the filters:

```python
FILTER_CREATOR = "CreatorWalletAddress"   # Only buy from this creator
FILTER_NAME = "PEPE"                       # Only buy tokens with this name
FILTER_SYMBOL = "PEPE"                     # Only buy tokens with this symbol
```

Leave empty to snipe any new token.

## Settings

| Setting | Default | Description |
|---------|---------|-------------|
| `NUM_SNIPERS` | 3 | Number of sniper wallets (max 50) |
| `FUND_AMOUNT` | 0.02 | SOL per sniper wallet |
| `BUY_AMOUNT` | 0.005 | SOL to spend per buy |

## How it works

**Detection:** Connects to Solana's RPC WebSocket and subscribes to the PumpFun program (`6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P`) using `logsSubscribe` at `processed` commitment (fastest). Filters for `CreateV2` instructions and parses the Anchor event data to extract token name, symbol, mint address, and creator.

**Execution:** Uses the [Launchpad.Trade](https://launchpad.trade) REST API to buy with multiple wallets in a single HTTP request. No Solana SDK needed, no transaction building, no RPC node configuration.

## Links

- [Launchpad.Trade](https://launchpad.trade) — Solana Trading API
- [Documentation](https://docs.launchpad.trade)
- [Discord](https://discord.gg/launchpadtrade)
- [YouTube Tutorial](https://youtube.com)

## Disclaimer

This project is for educational purposes only. Trading cryptocurrency involves risk. Always do your own research. This is not financial advice.
