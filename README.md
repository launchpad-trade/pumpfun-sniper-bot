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
pip install requests websockets base58 python-dotenv
```

## Setup

1. Copy `.env.example` to `.env`:
```bash
cp .env.example .env
```

2. Fill in your keys in `.env`:
```env
LAUNCHPAD_API_KEY=your_api_key_here
MAIN_PRIVATE_KEY=your_private_key_here
SOLANA_WSS=wss://mainnet.helius-rpc.com/?api-key=your_helius_key_here
```

The public key is automatically derived from your private key — no need to enter it separately.

> **Important:** Never commit your `.env` file. Add it to `.gitignore`.

## Run

```bash
python sniper_bot.py
```

Press Enter at each step. At Step 5, the bot monitors PumpFun and auto-buys the first new token detected.

## Snipe a specific token

To target a specific token launch, edit the filters in `sniper_bot.py`:

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

**Detection:** Connects to Solana's RPC WebSocket and subscribes to the PumpFun program (`6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P`) using `logsSubscribe` at `processed` commitment (fastest). Filters for `CreateV2` instructions and parses the Anchor event data to extract token name, symbol, mint address, and creator. Auto-reconnects with exponential backoff if the connection drops.

**Execution:** Uses the [Launchpad.Trade](https://launchpad.trade) REST API to buy with multiple wallets in a single HTTP request. No Solana SDK needed, no transaction building, no RPC node configuration.

## Links

- [Launchpad.Trade](https://launchpad.trade) — Solana Trading API
- [Documentation](https://docs.launchpad.trade)
- [Discord](https://discord.com/invite/launchpad-trade)
- [YouTube Tutorial](https://youtube.com)

## Disclaimer

This project is for educational purposes only. Trading cryptocurrency involves risk. Always do your own research. This is not financial advice.
