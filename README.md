# Ethereum Balance Checker

This project is a proof-of-concept security scanner for Ethereum addresses. It demonstrates various methods to generate and check Ethereum addresses for existing balances. Please note that the odds of finding a collision with an existing address are extremely low, and the chances of finding one with an actual balance are even lower.

## Disclaimer

**This tool is for educational and research purposes only. Do not use it for any malicious or illegal activities. The authors and contributors are not responsible for any misuse of this software.**

## Features

- Supports both **Ethereum** and **Bitcoin** address generation (`--crypto`)
- In Bitcoin target mode, derives and checks **every single-key address type** per random key — legacy, nested SegWit, native SegWit, and Taproot (see [Address Coverage](#address-coverage))
- Multiple modes of operation: Infura, Etherscan, Alchemy, and Target
- Multi-processing support for improved performance in Target mode
- Colorized console output for better readability
- CSV logging of found addresses with balances

## Requirements

- Python 3.7+
- pip (Python package installer)

Installation

1. Clone this repository:
   ```
   git https://github.com/auc0le/Ethereum-Scanner.git
   cd ethereum-balance-checker
   ```

2. Install the required libraries:
   ```
   pip install web3 requests eth-account colorama portalocker
   ```

## Usage

The script can be run in four different modes:

1. Infura mode
2. Etherscan mode
3. Alchemy mode
4. Target mode

YOU WILL NEED TO EDIT THE SCRIPT AND PROVIDE YOUR OWN API KEY FOR the first 3 modes.  The default timeout is set to keep you within the free API limits.  Increase it if you are on a paid account as the API is the real limit of speed in these modes.

The 4th (target) mode does NOT require an API and only runs locally against the provided target addresses - which should be the address with the highest balanaces as of July 2024.  This mode runs the fastest as it isn't limited by any APIs. Instead it is limited by your CPU power.

### Basic Usage

python CPU-Windows.py <mode> [--crypto <coin>] [--target-file <target.csv>] [--processes <num_processes>]

Where <mode> is one of: infura, etherscan, alchemy, or target.

Where <coin> is one of: ethereum or bitcoin.   ethereum is the default and will be used if not specified.

The --processes argument is optional and only applicable in target mode. It specifies the number of processes to use (default is the number of CPU cores).

The --target-file argument is optional and only applicable in target mode.  It allows you to provide your own target file and override the default ones that were provided.

### Mode Descriptions

1. Infura Mode: Generates random Ethereum addresses and checks their balance using the Infura API.

   ```python CPU-Windows.py infura```

2. Etherscan Mode: Generates random Ethereum addresses in batches and checks their balances using the Etherscan API.

   ```python CPU-Windows.py etherscan```

3. Alchemy Mode: Similar to Infura mode, but uses the Alchemy API for balance checks.

   ```python CPU-Windows.py alchemy```

4. Target Mode: Reads target addresses from target.csv and attempts to find collisions by generating random addresses.

   ```python CPU-Windows.py target --processes 4```

## Address Coverage

A single private key can be encoded as several different public addresses, and the target lists mix those formats. **Target mode therefore derives and checks every address type a single key can produce**, rather than only one canonical form per key — so a key is counted as a hit if *any* of its representations appears in the target list.

### Ethereum (`--crypto ethereum`, the default)

One address per key: the last 20 bytes of the Keccak-256 hash of the uncompressed public key, rendered as `0x...` lowercase hex. Matching is case-insensitive, so EIP-55 mixed-case checksummed targets are still matched correctly.

### Bitcoin (`--crypto bitcoin`)

Five addresses per key, covering every single-key (non-script) output type:

| Type | Prefix | Encoding | Notes |
|------|--------|----------|-------|
| P2PKH (compressed) | `1...` | Base58Check | Legacy, from the compressed public key |
| P2PKH (uncompressed) | `1...` | Base58Check | Legacy, from the uncompressed public key |
| P2SH-P2WPKH | `3...` | Base58Check | Nested ("wrapped") SegWit |
| P2WPKH | `bc1q...` | bech32 | Native SegWit v0 |
| P2TR | `bc1p...` | bech32m | Taproot key-path output (BIP341) |

Base58 and bech32/bech32m are case-sensitive, so Bitcoin targets are matched with their original case preserved (unlike Ethereum, which is lowercased).

**Out of scope:** multisig and other script-derived outputs (e.g. P2SH / P2WSH redeem scripts) are *not* generated. They are defined by a script rather than a single key and so cannot be produced from one random private key.

## Configuration

- API keys are stored in the API_KEYS dictionary in the script. **Replace these with your own API keys**.

## Output

- Console output shows real-time progress and any found addresses with balances.
- Addresses with balances are logged in Found.csv.

## Note

This is a proof-of-concept tool. The probability of finding a collision with an existing Ethereum address is extremely low and finding one with a balance is even less likely. This tool should not be relied upon for any practical purpose other than educational exploration of Ethereum/Bitcoin address generation and balance checking mechanisms.
