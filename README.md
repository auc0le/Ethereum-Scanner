# Ethereum Balance Checker

This project is a proof-of-concept security scanner for Ethereum addresses. It demonstrates various methods to generate and check Ethereum addresses for existing balances. Please note that the odds of finding a collision with an existing address are extremely low, and the chances of finding one with an actual balance are even lower.

## Disclaimer

**This tool is for educational and research purposes only. Do not use it for any malicious or illegal activities. The authors and contributors are not responsible for any misuse of this software.**

## Features

- Multiple modes of operation: Infura, Etherscan, Alchemy, and Target
- Multi-processing support for improved performance in Target mode
- Colorized console output for better readability
- CSV logging of found addresses with balances

## Requirements

- Python 3.7+
- pip (Python package installer)

Installation

1. Clone this repository:
   git https://github.com/auc0le/Ethereum-Scanner.git
   cd ethereum-balance-checker

2. Install the required libraries:
   pip install web3 requests eth-account colorama portalocker

## Usage

The script can be run in four different modes:

1. Infura mode
2. Etherscan mode
3. Alchemy mode
4. Target mode

### Basic Usage

python Code.py <mode> [--processes <num_processes>]

Where <mode> is one of: infura, etherscan, alchemy, or target.

The --processes argument is optional and only applicable in target mode. It specifies the number of processes to use (default is the number of CPU cores).

### Mode Descriptions

1. Infura Mode: Generates random Ethereum addresses and checks their balance using the Infura API.
   python Code.py infura

2. Etherscan Mode: Generates random Ethereum addresses in batches and checks their balances using the Etherscan API.
   python Code.py etherscan

3. Alchemy Mode: Similar to Infura mode, but uses the Alchemy API for balance checks.
   python Code.py alchemy

4. Target Mode: Reads target addresses from target.csv and attempts to find collisions by generating random addresses.
   python Code.py target --processes 4

## Configuration

- API keys are stored in the API_KEYS dictionary in the script. Replace these with your own API keys.
- The target.csv file should contain a list of Ethereum addresses to check against in Target mode.

## Output

- Console output shows real-time progress and any found addresses with balances.
- Addresses with balances are logged in FoundETH.csv.

## Note

This is a proof-of-concept tool. The probability of finding a collision with an existing Ethereum address is extremely low, and finding one with a balance is even less likely. This tool should not be relied upon for any practical purpose other than educational exploration of Ethereum address generation and balance checking mechanisms.
