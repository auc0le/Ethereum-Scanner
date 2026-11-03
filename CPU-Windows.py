import os
import csv
import time
import json
import argparse
import sys
import platform
from datetime import datetime, timedelta
from typing import Set, Dict, Optional, List
import portalocker
import uuid
import multiprocessing
import threading
from concurrent.futures import ProcessPoolExecutor, as_completed

import requests
from requests.exceptions import RequestException, Timeout, HTTPError
from web3 import Web3
from eth_account import Account
from eth_hash.auto import keccak  # Keccak-256; provided by eth-hash (an eth-account dependency)
from colorama import Fore, Style, init

# New imports for faster Bitcoin address generation
import hashlib
from coincurve import PublicKey
from coincurve.utils import int_to_bytes
import base58
import secrets

# Initialize colorama for colored output
init(autoreset=True)

# API key constants
API_KEYS = {
    "INFURA": "INSERT API KEY HERE",
    "ETHERSCAN": "INSERT API KEY HERE",
    "ALCHEMY": "INSERT API KEY HERE"
}

# Configuration constants
MAX_RETRIES = 3
RETRY_DELAY = 5  # seconds

def load_addresses_from_file(filename: str, crypto: str = 'ethereum') -> Set[str]:
    addresses = set()
    try:
        # utf-8-sig strips a leading BOM (the target files have one); .strip()
        # does not, which otherwise leaves the first address unmatchable.
        with open(filename, 'r', encoding='utf-8-sig') as file:
            if crypto == 'ethereum':
                # Ethereum addresses are hex, matched case-insensitively.
                addresses = {line.strip().lower() for line in file if line.strip()}
            else:
                # Bitcoin addresses (base58 and bech32) are case-sensitive;
                # lowercasing corrupts the target and blocks every match.
                addresses = {line.strip() for line in file if line.strip()}
        print(f"Loaded {len(addresses):,} unique addresses from {filename}")
    except FileNotFoundError:
        print(f"Error: File {filename} not found.")
        sys.exit(1)
    except Exception as e:
        print(f"Error: An error occurred while reading the file: {e}")
        sys.exit(1)
    
    if not addresses:
        print(f"Error: No valid addresses found in {filename}")
        sys.exit(1)
    
    return addresses

def append_to_csv(filename: str, data: List):
    file_exists = os.path.isfile(filename)
    with portalocker.Lock(filename, 'a', timeout=10) as csvfile:
        writer = csv.writer(csvfile)
        if not file_exists:
            writer.writerow(['Timestamp', 'Instance ID', 'Private Key', 'Address', 'Balance', 'Cryptocurrency'])
        writer.writerow(data)

def get_timestamp() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

_BECH32_CHARSET = "qpzry9x8gf2tvdw0s3jn54khce6mua7l"


def _hash160(data: bytes) -> bytes:
    """RIPEMD-160(SHA-256(data))."""
    rip = hashlib.new('ripemd160')
    rip.update(hashlib.sha256(data).digest())
    return rip.digest()


def _b58check(payload: bytes) -> str:
    """Base58Check-encode a payload (version byte + hash) with a 4-byte checksum."""
    checksum = hashlib.sha256(hashlib.sha256(payload).digest()).digest()[:4]
    return base58.b58encode(payload + checksum).decode('ascii')


def _bech32_polymod(values: List[int]) -> int:
    generator = [0x3b6a57b2, 0x26508e6d, 0x1ea119fa, 0x3d4233dd, 0x2a1462b3]
    chk = 1
    for value in values:
        top = chk >> 25
        chk = (chk & 0x1ffffff) << 5 ^ value
        for i in range(5):
            chk ^= generator[i] if ((top >> i) & 1) else 0
    return chk


def _bech32_checksum(hrp: str, data: List[int], spec: str) -> List[int]:
    const = 0x2bc830a3 if spec == 'bech32m' else 1
    expanded = [ord(c) >> 5 for c in hrp] + [0] + [ord(c) & 31 for c in hrp]
    polymod = _bech32_polymod(expanded + data + [0, 0, 0, 0, 0, 0]) ^ const
    return [(polymod >> 5 * (5 - i)) & 31 for i in range(6)]


def _convertbits(data: bytes, frombits: int, tobits: int) -> List[int]:
    """Regroup 8-bit bytes into 5-bit groups, padding the final group."""
    acc = bits = 0
    ret = []
    maxv = (1 << tobits) - 1
    for value in data:
        acc = (acc << frombits) | value
        bits += frombits
        while bits >= tobits:
            bits -= tobits
            ret.append((acc >> bits) & maxv)
    if bits:
        ret.append((acc << (tobits - bits)) & maxv)
    return ret


def _segwit_address(witver: int, witprog: bytes, hrp: str = 'bc') -> str:
    spec = 'bech32' if witver == 0 else 'bech32m'
    data = [witver] + _convertbits(witprog, 8, 5)
    combined = data + _bech32_checksum(hrp, data, spec)
    return hrp + '1' + ''.join(_BECH32_CHARSET[d] for d in combined)


def _tagged_hash(tag: str, msg: bytes) -> bytes:
    """BIP340 tagged hash: SHA256(SHA256(tag) || SHA256(tag) || msg)."""
    tag_hash = hashlib.sha256(tag.encode()).digest()
    return hashlib.sha256(tag_hash + tag_hash + msg).digest()


def generate_bitcoin_addresses(num_addresses=1):
    # One random key maps to several address formats, and the target list mixes
    # them, so we derive and check every single-key type: legacy P2PKH "1..."
    # (compressed and uncompressed keys), nested SegWit P2SH-P2WPKH "3...",
    # native SegWit v0 P2WPKH "bc1q...", and Taproot key-path P2TR "bc1p...".
    # Multisig P2SH and P2WSH come from scripts, not a single key.
    addresses = []
    for _ in range(num_addresses):
        private_key = os.urandom(32)
        private_key_hex = private_key.hex()

        pk = PublicKey.from_valid_secret(private_key)
        h160_compressed = _hash160(pk.format(compressed=True))
        h160_uncompressed = _hash160(pk.format(compressed=False))

        # P2PKH "1..." from the compressed and the uncompressed public key
        addresses.append((private_key_hex, _b58check(b'\x00' + h160_compressed)))
        addresses.append((private_key_hex, _b58check(b'\x00' + h160_uncompressed)))
        # P2SH-P2WPKH "3...": hash160 of the witness program (OP_0 PUSH20 <h160>)
        addresses.append((private_key_hex, _b58check(b'\x05' + _hash160(b'\x00\x14' + h160_compressed))))
        # P2WPKH "bc1q...": native SegWit v0
        addresses.append((private_key_hex, _segwit_address(0, h160_compressed)))
        # P2TR "bc1p...": Taproot key-path output (BIP341). The output key is the
        # x-only internal key tweaked by t = H_TapTweak(x); with no script tree
        # the tweak commits to the key alone. Encoded as SegWit v1 (bech32m).
        internal_xonly = pk.format(compressed=True)[1:]
        tweak = _tagged_hash('TapTweak', internal_xonly)
        output_key = PublicKey.combine_keys([
            PublicKey(b'\x02' + internal_xonly),  # even-Y lift of the internal key
            PublicKey.from_valid_secret(tweak),   # t*G
        ])
        addresses.append((private_key_hex, _segwit_address(1, output_key.format(compressed=True)[1:])))

    return addresses if num_addresses > 1 else addresses[0]

def generate_ethereum_addresses(num_addresses=1):
    addresses = []
    for _ in range(num_addresses):
        # Raw 32 random bytes -> secp256k1 pubkey (coincurve, C) -> Keccak-256.
        # Avoids eth_account.Account.from_key, which dominated the per-key cost.
        private_key = secrets.token_bytes(32)
        public_key = PublicKey.from_valid_secret(private_key).format(compressed=False)[1:]
        address = '0x' + keccak(public_key)[-20:].hex()
        addresses.append((private_key.hex(), address))
    return addresses if num_addresses > 1 else addresses[0]

# Per-worker state, populated once by the pool initializer so the (large)
# target set is pickled to each worker a single time rather than on every task.
_WORKER_TARGETS: Set[str] = set()
_WORKER_CRYPTO: str = 'ethereum'


def _init_worker(target_addresses: Set[str], crypto: str):
    global _WORKER_TARGETS, _WORKER_CRYPTO
    _WORKER_TARGETS = target_addresses
    _WORKER_CRYPTO = crypto


def process_batch(batch_size: int) -> tuple:
    """Generate `batch_size` random keys; return (addresses_checked, matches).

    Bitcoin derives several address types per key, so addresses_checked can
    exceed batch_size. Reads the target set / crypto from per-worker globals
    (see _init_worker) and keeps no shared state in the hot loop.
    """
    targets = _WORKER_TARGETS
    generate = generate_ethereum_addresses if _WORKER_CRYPTO == 'ethereum' else generate_bitcoin_addresses
    found_matches = []
    checked = 0
    chunk_size = 10000

    for i in range(0, batch_size, chunk_size):
        current_chunk_size = min(chunk_size, batch_size - i)
        generated = generate(current_chunk_size)
        checked += len(generated)  # Bitcoin yields several addresses per key
        for private_key, address in generated:
            if address in targets:
                found_matches.append((private_key, address))

    return checked, (found_matches if found_matches else None)

def format_time(seconds: int) -> str:
    return str(timedelta(seconds=seconds)).split('.')[0]

def print_status(counter, start_time, instance_id, stop_event, crypto):
    while not stop_event.is_set():
        elapsed_time = int(time.time() - start_time)
        formatted_time = format_time(elapsed_time)
        checked = counter[0]
        addresses_per_second = checked / elapsed_time if elapsed_time > 0 else 0
        crypto_colored = Fore.BLUE + "BITCOIN" + Style.RESET_ALL if crypto == 'bitcoin' else Fore.YELLOW + "ETHEREUM" + Style.RESET_ALL
        status_message = f"Instance {instance_id} - Checked {checked:,} {crypto_colored} addresses in {formatted_time} (Avg: {addresses_per_second:,.2f} addr/s)"
        print(f"\033[K{status_message}", end="\r", flush=True)
        time.sleep(1)

class CryptoBalanceChecker:
    def __init__(self, mode: str, num_processes: int = 1, crypto: str = 'ethereum', target_file: str = None):
        self.mode = mode
        self.num_processes = num_processes
        self.crypto = crypto
        self.counter = 0
        self.instance_id = str(uuid.uuid4())[:8]
        if mode == 'target':
            if target_file is None:
                target_file = 'eth-target.csv' if crypto == 'ethereum' else 'btc-target.csv'
            self.target_addresses = load_addresses_from_file(target_file, crypto)
        else:
            self.w3 = Web3(Web3.HTTPProvider(f"https://mainnet.infura.io/v3/{API_KEYS['INFURA']}"))

    def get_balance_infura(self, address: str) -> Optional[float]:
        try:
            balance = self.w3.eth.get_balance(address)
            return self.w3.from_wei(balance, 'ether')
        except Exception as e:
            print(f"Error querying Infura: {e}")
            return None

    def get_balance_etherscan(self, addresses: List[str]) -> Dict[str, float]:
        address_list = ','.join(addresses)
        url = f"https://api.etherscan.io/api?module=account&action=balancemulti&address={address_list}&tag=latest&apikey={API_KEYS['ETHERSCAN']}"
        return self._make_api_request(url, self._parse_etherscan_response)

    def get_balance_alchemy(self, address: str) -> float:
        url = f"https://eth-mainnet.g.alchemy.com/v2/{API_KEYS['ALCHEMY']}"
        payload = {
            "jsonrpc": "2.0",
            "method": "eth_getBalance",
            "params": [address, "latest"],
            "id": 1
        }
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json"
        }
        return self._make_api_request(url, self._parse_alchemy_response, method='POST', json=payload, headers=headers)

    def _make_api_request(self, url: str, parser_func, method='GET', **kwargs):
        for attempt in range(MAX_RETRIES):
            try:
                response = requests.request(method, url, timeout=10, **kwargs)
                response.raise_for_status()
                return parser_func(response.json())
            except Timeout:
                print(f"{self.mode.capitalize()} API request timed out")
            except HTTPError as e:
                print(f"HTTP error occurred: {e}")
            except RequestException as e:
                print(f"An error occurred while querying {self.mode.capitalize()}: {e}")
            except ValueError as e:
                print(f"Error parsing {self.mode.capitalize()} response: {e}")
            
            if attempt < MAX_RETRIES - 1:
                print(f"Retrying in {RETRY_DELAY} seconds...")
                time.sleep(RETRY_DELAY)
            else:
                print("Max retries reached. Skipping this request.")
        return {} if self.mode == 'etherscan' else 0

    @staticmethod
    def _parse_etherscan_response(data: Dict) -> Dict[str, float]:
        if data['status'] == '1':
            return {item['account']: float(item['balance']) / 1e18 for item in data['result']}
        else:
            print(f"Etherscan API error: {data.get('message', 'Unknown error')}")
            return {}

    @staticmethod
    def _parse_alchemy_response(data: Dict) -> float:
        if 'result' in data:
            balance_wei = int(data['result'], 16)
            return Web3.from_wei(balance_wei, 'ether')
        else:
            print(f"Alchemy API error: {data.get('error', 'Unknown error')}")
            return 0

    def check_balance(self, private_key: str, address: str) -> Optional[float]:
        if self.mode == 'infura':
            return self.get_balance_infura(address)
        elif self.mode == 'alchemy':
            return self.get_balance_alchemy(address)
        elif self.mode == 'etherscan':
            return self.get_balance_etherscan([address]).get(address, 0)
        else:
            print(f"Invalid mode: {self.mode}")
            return None

    def process_address(self, private_key: str, address: str) -> bool:
        self.counter += 1
        timestamp = get_timestamp()

        print(f"{timestamp} - Instance {self.instance_id} - Iteration {self.counter} - {self.mode.capitalize()} - {self.crypto.upper()}")
        print(f"Private Key: {private_key}")
        print(f"Address: {address}")

        if self.mode == 'target':
            balance = 1.0  # Assume balance of 1 for target mode
        else:
            balance = self.check_balance(private_key, address)
        
        if balance is None:
            print(f"Failed to get balance. Continuing to next address.")
            return False

        print(Fore.RED + f"Balance: {balance} {self.crypto.upper()}")
        print("-" * 80)

        if balance > 0:
            filename = 'Found.csv'
            data_to_append = [timestamp, self.instance_id, private_key, address, balance, self.crypto.upper()]
            append_to_csv(filename, data_to_append)
            print(Fore.GREEN + f"{timestamp} - Instance {self.instance_id} - Found {self.crypto.upper()}! Saved to {filename}")
            return True

        return False

    def run_target_mode(self):
        batch_size = 100000  # Keys generated per task; tune for status cadence

        start_time = time.time()
        counter = [0]  # running total: written by main thread, read by status thread
        stop_event = threading.Event()

        # Print information about processes and CPU
        print(f"Running on {platform.processor()} with {multiprocessing.cpu_count()} cores")
        print(f"Using {self.num_processes} processes for address generation")
        print()  # Add an empty line

        # Status runs as a thread in the main process, so it needs no
        # cross-process shared state (works the same under Windows 'spawn'
        # and WSL 'fork').
        status_thread = threading.Thread(
            target=print_status,
            args=(counter, start_time, self.instance_id, stop_event, self.crypto),
            daemon=True,
        )
        status_thread.start()

        try:
            # The target set is pickled to each worker once, via the initializer,
            # instead of being re-sent with every task submission.
            with ProcessPoolExecutor(
                max_workers=self.num_processes,
                initializer=_init_worker,
                initargs=(self.target_addresses, self.crypto),
            ) as executor:
                while True:  # Run indefinitely until interrupted
                    futures = [executor.submit(process_batch, batch_size) for _ in range(self.num_processes)]

                    for future in as_completed(futures):
                        count, result = future.result()
                        counter[0] += count
                        if result:
                            for private_key, address in result:
                                print("\n" * 2)  # Add two empty lines
                                self.process_address(private_key, address)
                                print(f"Instance {self.instance_id} - Found {self.crypto.upper()} match: {address}")
                                print()  # Add an empty line
        except KeyboardInterrupt:
            print("\n\nStopping the process...")
        finally:
            stop_event.set()
            status_thread.join(timeout=2)

        print(f"\nInstance {self.instance_id} - Finished checking {self.crypto.upper()} addresses.")

    def run(self):
        if self.mode == 'target':
            self.run_target_mode()
        elif self.mode == 'etherscan':
            while True:
                accounts = [Account.create() for _ in range(20)]
                addresses = [account.address for account in accounts]
                balances = self.get_balance_etherscan(addresses)
                for account, address in zip(accounts, addresses):
                    if self.process_address(account._private_key.hex(), address):
                        return
                time.sleep(1)
        else:  # infura or alchemy
            while True:
                account = Account.create()
                if self.process_address(account._private_key.hex(), account.address):
                    break
                time.sleep(1)

def main():
    parser = argparse.ArgumentParser(description="Cryptocurrency Balance Checker")
    parser.add_argument("mode", choices=['infura', 'etherscan', 'alchemy', 'target'], help="Mode to run the balance check")
    parser.add_argument("--processes", type=int, default=multiprocessing.cpu_count(), help="Number of processes to use in target mode (default: number of CPU cores)")
    parser.add_argument("--crypto", choices=['ethereum', 'bitcoin'], default='ethereum', help="Cryptocurrency to use in target mode (default: ethereum)")
    parser.add_argument("--target-file", help="Path to the target CSV file (only used in target mode)")
    args = parser.parse_args()

    num_processes = args.processes if args.mode == 'target' else 1
    checker = CryptoBalanceChecker(args.mode, num_processes, args.crypto, args.target_file)
    checker.run()

if __name__ == "__main__":
    multiprocessing.freeze_support()  # This is necessary for Windows compatibility
    main()                