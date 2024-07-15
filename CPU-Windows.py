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
from concurrent.futures import ProcessPoolExecutor, as_completed

import requests
from requests.exceptions import RequestException, Timeout, HTTPError
from web3 import Web3
from eth_account import Account
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

def load_addresses_from_file(filename: str) -> Set[str]:
    addresses = set()
    try:
        with open(filename, 'r') as file:
            addresses = {line.strip().lower() for line in file if line.strip()}
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

def generate_bitcoin_addresses(num_addresses=1):
    addresses = []
    for _ in range(num_addresses):
        # Generate a random private key
        private_key = os.urandom(32)
        private_key_hex = private_key.hex()

        # Get the public key
        public_key = PublicKey.from_valid_secret(private_key).format(compressed=True)

        # Hash the public key using SHA-256 and RIPEMD-160
        sha256_hash = hashlib.sha256(public_key).digest()
        ripemd160 = hashlib.new('ripemd160')
        ripemd160.update(sha256_hash)
        hash160 = ripemd160.digest()

        # Add version byte (0x00 for mainnet)
        versioned_hash = b'\x00' + hash160

        # Double SHA-256 hash for checksum
        checksum = hashlib.sha256(hashlib.sha256(versioned_hash).digest()).digest()[:4]

        # Combine versioned hash and checksum
        binary_address = versioned_hash + checksum

        # Base58 encode the result
        address = base58.b58encode(binary_address).decode('utf-8')
        
        addresses.append((private_key_hex, address))

    return addresses if num_addresses > 1 else addresses[0]

def generate_ethereum_addresses(num_addresses=1):
    addresses = []
    for _ in range(num_addresses):
        private_key = secrets.token_hex(32)
        account = Account.from_key(private_key)
        addresses.append((private_key, account.address.lower()))
    return addresses if num_addresses > 1 else addresses[0]

def process_batch(batch_size: int, target_addresses: Set[str], shared_dict: Dict, crypto: str, batch_number: int) -> Optional[List[tuple]]:
    found_matches = []

    if crypto == 'ethereum':
        chunk_size = 10000  # Increase chunk size for Ethereum
        for i in range(0, batch_size, chunk_size):
            current_chunk_size = min(chunk_size, batch_size - i)
            addresses = generate_ethereum_addresses(current_chunk_size)
            
            shared_dict['addresses_checked'] += current_chunk_size
            
            for private_key, address in addresses:
                if address in target_addresses:
                    found_matches.append((private_key, address))
    else:  # bitcoin
        chunk_size = 10000  # Chunk size for Bitcoin
        for i in range(0, batch_size, chunk_size):
            current_chunk_size = min(chunk_size, batch_size - i)
            addresses = generate_bitcoin_addresses(current_chunk_size)
            
            shared_dict['addresses_checked'] += current_chunk_size
            
            for private_key, address in addresses:
                if address in target_addresses:
                    found_matches.append((private_key, address))

    return found_matches if found_matches else None

def format_time(seconds: int) -> str:
    return str(timedelta(seconds=seconds)).split('.')[0]

def print_status(shared_dict, start_time, instance_id, stop_event, crypto):
    while not stop_event.is_set():
        current_time = time.time()
        elapsed_time = int(current_time - start_time)
        formatted_time = format_time(elapsed_time)
        addresses_per_second = shared_dict['addresses_checked'] / elapsed_time if elapsed_time > 0 else 0
        crypto_colored = Fore.BLUE + "BITCOIN" + Style.RESET_ALL if crypto == 'bitcoin' else Fore.YELLOW + "ETHEREUM" + Style.RESET_ALL
        status_message = f"Instance {instance_id} - Checked {shared_dict['addresses_checked']:,} {crypto_colored} addresses in {formatted_time} (Avg: {addresses_per_second:,.2f} addr/s)"
        print(f"\033[K{status_message}", end="\r", flush=True)
        time.sleep(1)

class CryptoBalanceChecker:
    def __init__(self, mode: str, num_processes: int = 1, crypto: str = 'ethereum', target_file: str = None):
        self.mode = mode
        self.num_processes = num_processes
        self.crypto = crypto
        self.counter = 0
        self.instance_id = str(uuid.uuid4())[:8]
        self.manager = multiprocessing.Manager()
        self.shared_dict = self.manager.dict()
        self.shared_dict['addresses_checked'] = 0
        if mode == 'target':
            if target_file is None:
                target_file = 'eth-target.csv' if crypto == 'ethereum' else 'btc-target.csv'
            self.target_addresses = load_addresses_from_file(target_file)
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
        batch_size = 100000  # Increased batch size for better performance
        
        stop_event = self.manager.Event()
        start_time = time.time()

        # Print information about processes and CPU
        print(f"Running on {platform.processor()} with {multiprocessing.cpu_count()} cores")
        print(f"Using {self.num_processes} processes for address generation")
        print()  # Add an empty line

        # Start the status printing process
        status_process = multiprocessing.Process(target=print_status, args=(self.shared_dict, start_time, self.instance_id, stop_event, self.crypto))
        status_process.start()

        try:
            with ProcessPoolExecutor(max_workers=self.num_processes) as executor:
                batch_number = 0
                while True:  # This will make the process run indefinitely
                    batch_number += 1
                    futures = [executor.submit(process_batch, batch_size, self.target_addresses, self.shared_dict, self.crypto, batch_number) for _ in range(self.num_processes)]
                    
                    for future in as_completed(futures):
                        result = future.result()
                        if result:
                            for private_key, address in result:
                                print("\n" * 2)  # Add two empty lines
                                self.process_address(private_key, address)
                                print(f"Instance {self.instance_id} - Found {self.crypto.upper()} match: {address}")
                                print()  # Add an empty line
        except KeyboardInterrupt:
            print("\n\nStopping the process...")
        finally:
            # Stop the status printing process
            stop_event.set()
            status_process.join()

        print(f"\nInstance {self.instance_id} - Finished checking {self.crypto.upper()} addresses.")

    def run(self):
        if self.mode == 'target':
            self.run_target_mode()
        elif self.mode == 'etherscan':
            while True:
                accounts = [Account.create() for _ in range(20)]
                addresses = [account.address for account in addresses]
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