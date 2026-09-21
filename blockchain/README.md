# Land Record Blockchain Integrity Module (Ganache + Web3.js)

A standalone, decentralized land record integrity verification subsystem for the Intelligent Land Record Platform.

This module provides an immutable, tamper-evident audit trail by anchoring cryptographic document digests on an Ethereum-compatible blockchain using Solidity smart contracts, a local **Ganache** blockchain, and **Web3.js**.

---

## Table of Contents

1. [Overview](#overview)
2. [Why Store Cryptographic Hashes (Not Full Documents)?](#why-store-cryptographic-hashes-not-full-documents)
3. [Technology Stack](#technology-stack)
4. [Architecture & Workflow](#architecture--workflow)
5. [Project Structure](#project-structure)
6. [Ganache Setup & Configuration](#ganache-setup--configuration)
7. [Installation & Compilation](#installation--compilation)
8. [Deploying the Smart Contract](#deploying-the-smart-contract)
9. [Registering a Land Record](#registering-a-land-record)
10. [Retrieving a Land Record](#retrieving-a-land-record)
11. [Verifying Document Integrity (Tamper Detection)](#verifying-document-integrity-tamper-detection)
12. [Automated Testing](#automated-testing)
13. [Security Considerations](#security-considerations)

---

## Overview

In traditional land registries, records (scanned sale deeds, 7/12 extracts, title registers, cadastral vector files) can be vulnerable to retroactive tampering or unauthorized modification in central databases without leaving an irrefutable audit trail.

This module solves that vulnerability by anchoring an immutable cryptographic fingerprint (**SHA-256 hash**) of each land record on a local blockchain network alongside a unique record identifier and an immutable block timestamp.

---

## Why Store Cryptographic Hashes (Not Full Documents)?

Storing complete PDFs, images, or spatial GeoTIFF files directly on a blockchain is an anti-pattern. We store only cryptographic hashes for the following critical reasons:

| Factor | Full Document On-Chain | Cryptographic Hash On-Chain |
| :--- | :--- | :--- |
| **Privacy & PII** | Exposes sensitive personal data (Aadhaar, phone numbers, ownership names, financial transactions) publicly to all node validators. Violates data protection laws (e.g. DPDP Act). | **Zero PII Exposure**: SHA-256 is a one-way mathematical function. It reveals zero confidential personal information. |
| **Storage & Cost** | Extremely high gas and storage costs. Storing a 5 MB PDF requires massive block gas and bloats the ledger state. | **Negligible Cost**: Fixed-length string or 32-byte digest requires minimal storage and minimal transaction gas. |
| **Scalability & Throughput** | Blocks have strict gas limits. Large payloads cause network congestion and slow confirmation. | **High Throughput**: Allows thousands of land record registrations with fast block confirmation. |
| **Mathematical Determinism** | Not needed for integrity verification; checking content equivalence is computationally expensive. | **Instant Verification**: Any single-bit modification in a multi-megabyte document drastically alters the resulting hash (Avalanche Effect). |

---

## Technology Stack

- **Smart Contract**: Solidity (`^0.8.24`)
- **Compilation & Unit Testing**: Hardhat (`^2.22.18`)
- **Local Blockchain**: Ganache (`v7.x` CLI / Ganache GUI)
- **Application Interaction**: Web3.js (`^4.16.x`)
- **Runtime**: Node.js (`v18+`, `v20+`, `v24+`)

---

## Architecture & Workflow

```
Land Record Document (PDF / Scan / GeoTIFF)
                 ↓
       Compute SHA-256 Hash
                 ↓
   LandRecordIntegrity.sol (Smart Contract)
                 ↓
    Ganache Local Blockchain (http://127.0.0.1:7545)
                 ↓
             Web3.js
                 ↓
   Register / Retrieve / Verify Land Record
```

---

## Project Structure

```
blockchain/
├── contracts/
│   └── LandRecordIntegrity.sol        # Solidity smart contract for hash registry
├── scripts/
│   ├── contract-helper.js             # Shared Web3.js connection & ABI loading utility
│   ├── deploy.js                      # Hardhat in-memory deployment script
│   ├── deploy-ganache.js              # Web3.js deployment script for Ganache
│   ├── register-record.js             # Interactive/CLI tool to register a record hash
│   ├── get-record.js                  # Interactive/CLI tool to retrieve record details
│   └── verify-record.js               # Interactive/CLI tool to verify document authenticity
├── test/
│   ├── LandRecordIntegrity.test.js    # Hardhat unit tests (16 tests)
│   └── ganache-integration.test.js    # Web3.js + Ganache integration tests (Scenarios A-I)
├── hardhat.config.js                  # Hardhat compiler and network configuration
├── package.json                       # NPM dependencies and development scripts
├── .gitignore                         # Excludes node_modules, build artifacts, and cache
└── README.md                          # Subsystem documentation
```

---

## Ganache Setup & Configuration

> [!NOTE]
> Ganache is a local development and test blockchain. It simulates an Ethereum network with pre-funded test accounts and zero real-money cost.

### Default Connection Details
- **RPC URL**: `http://127.0.0.1:7545`
- **Network ID / Chain ID**: Automatically detected (defaults to `1337` or `5777`)
- **Default Accounts**: Pre-funded with 1000 test ETH

### How to Start Ganache

#### Option A: Ganache GUI
1. Download and open **Ganache GUI** from [Truffle Suite](https://trufflesuite.com/ganache/).
2. Select **Quickstart (Ethereum)**.
3. Verify the server is configured to port `7545` (`Settings -> Server -> Port: 7545`).

#### Option B: Ganache CLI
Run the following command from your terminal:

```bash
npx ganache --port 7545 --wallet.totalAccounts 10
```

Ganache will start and display 10 available test accounts and private keys.

---

## Installation & Compilation

Navigate into the `blockchain/` directory:

```bash
cd blockchain
```

Install dependencies:

```bash
npm install
```

Compile the Solidity smart contract:

```bash
npm run compile
```

Expected output:
```
Compiled 1 Solidity file successfully (evm target: paris).
```

---

## Deploying the Smart Contract

Ensure Ganache is running on port `7545`, then execute:

```bash
npm run deploy:ganache
```

### Expected Output:
```text
====================================================
Deploying LandRecordIntegrity to Ganache...
====================================================
Connecting to Ganache at: http://127.0.0.1:7545
Ganache Chain ID : 1337
Deployer Account : 0x12edb1D87c7EE077C66e0e8e901C980feF86c57b
Account Balance  : 1000 ETH
Broadcasting deployment transaction...
----------------------------------------------------
Ganache Network  : http://127.0.0.1:7545 (Chain ID: 1337)
Deployer Address : 0x12edb1D87c7EE077C66e0e8e901C980feF86c57b
Contract Address : 0x08F9B7E995219B00DcF02947B6Af2B8Dbf109603
Transaction Hash : 0xa10569bc0a25a23fd9d2e6cd18d557b84737fa45ba79c82d4e5bd8f3abe1c456
====================================================
Deployment information saved to deployed-contract.json
```

Deployment parameters (address, network, and ABI) are automatically saved to `deployed-contract.json` for subsequent Web3.js operations.

---

## Registering a Land Record

You can register a record interactively or by passing command-line arguments:

### Interactive Mode:
```bash
npm run register
```

Terminal Prompt:
```text
====================================
LAND RECORD BLOCKCHAIN REGISTRATION
====================================

Enter Record ID: LR-2026-001
Enter Document Hash: a591a6d40bf420404a011733cfb7b190d62c65bf0bcda32b57b277d9ad9f146e

Registering record...

------------------------------------
Transaction Hash       : 0x66be1eb563c962d94ad4d6e760af5816d7a6afc4779681939ba26d8e47349e77
Registered Record ID   : LR-2026-001
Stored Document Hash   : a591a6d40bf420404a011733cfb7b190d62c65bf0bcda32b57b277d9ad9f146e
Registration Timestamp : 1789987398 (Mon, 21 Sep 2026 10:43:18 GMT)
Registered By Account  : 0x12edb1D87c7EE077C66e0e8e901C980feF86c57b
------------------------------------
✅ Record registered successfully.
```

### Direct CLI Argument Mode:
```bash
node scripts/register-record.js LR-2026-001 a591a6d40bf420404a011733cfb7b190d62c65bf0bcda32b57b277d9ad9f146e
```

---

## Retrieving a Land Record

Retrieve anchored record details from Ganache by Record ID:

### Interactive Mode:
```bash
npm run get-record
```

Terminal Prompt:
```text
====================================
LAND RECORD BLOCKCHAIN RETRIEVAL
====================================

Enter Record ID: LR-2026-001

Fetching record from blockchain...

------------------------------------
Record ID           : LR-2026-001
Document Hash       : a591a6d40bf420404a011733cfb7b190d62c65bf0bcda32b57b277d9ad9f146e
Timestamp           : 1789987398 (Mon, 21 Sep 2026 10:43:18 GMT)
Registration Status : Registered (Active)
------------------------------------
```

### Direct CLI Argument Mode:
```bash
node scripts/get-record.js LR-2026-001
```

---

## Verifying Document Integrity (Tamper Detection)

The verification script compares a candidate document's hash against the hash permanently anchored on-chain.

### Authentic Document Verification (Matching Hash)
```bash
node scripts/verify-record.js LR-2026-001 a591a6d40bf420404a011733cfb7b190d62c65bf0bcda32b57b277d9ad9f146e
```

Output:
```text
====================================
LAND RECORD BLOCKCHAIN VERIFICATION
====================================

Record ID: LR-2026-001
Candidate Document Hash: a591a6d40bf420404a011733cfb7b190d62c65bf0bcda32b57b277d9ad9f146e

Querying blockchain for record verification...

Record Registered  : true
Stored Hash        : a591a6d40bf420404a011733cfb7b190d62c65bf0bcda32b57b277d9ad9f146e
Verification Result: true

✅ Document is authentic. Hash matches.
```

### Tampered Document Verification (Altered Hash)
Simulate a modified document by altering the hash:

```bash
node scripts/verify-record.js LR-2026-001 ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff
```

Output:
```text
====================================
LAND RECORD BLOCKCHAIN VERIFICATION
====================================

Record ID: LR-2026-001
Candidate Document Hash: ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff

Querying blockchain for record verification...

Record Registered  : true
Stored Hash        : a591a6d40bf420404a011733cfb7b190d62c65bf0bcda32b57b277d9ad9f146e
Verification Result: false

❌ Document has been modified. Hash does not match.
```

---

## Automated Testing

### 1. Hardhat Contract Unit Tests
Executes 16 unit tests for contract deployment, events, timestamp validity, and failure conditions:

```bash
npm test
```

### 2. Ganache + Web3.js Integration Tests
Executes the end-to-end integration test suite against the running Ganache RPC instance, covering all scenarios (A through I):

```bash
npm run test:ganache
```

Expected output:
```text
Scenario A: Deploy contract successfully to Ganache
  ✅ PASS: Contract deployed at valid address: 0x...
Scenario B: Register new land record
  ✅ PASS: Record registered with tx: 0x...
Scenario C: Retrieve registered record
  ✅ PASS: Retrieved record matches: id=LR-2026-001, timestamp=...
Scenario D: Verify correct hash (expected TRUE)
  ✅ PASS: Original hash verified as authentic (result = true)
Scenario E: Verify modified/tampered hash (expected FALSE)
  ✅ PASS: Tampered hash correctly detected and rejected (result = false)
Scenario F: Reject duplicate record registration
  ✅ PASS: Duplicate registration rejected as expected
Scenario G: Reject empty record ID
  ✅ PASS: Empty record ID rejected
Scenario H: Reject empty document hash
  ✅ PASS: Empty document hash rejected
Scenario I: Handle non-existent record
  ✅ PASS: isRecordRegistered for non-existent record returned false
  ✅ PASS: getRecord for non-existent record reverted as expected

TEST SUMMARY: 10 passed, 0 failed
```

---

## Security Considerations

1. **Local Development Keys**:
   - Accounts and private keys shown in Ganache are public knowledge and strictly for local development and demonstration.
   - Never use Ganache private keys on public testnets or mainnet.

2. **Zero Hardcoded Secrets**:
   - Web3.js scripts connect dynamically to Ganache RPC and use node-managed accounts (`web3.eth.getAccounts()`). No private keys are hardcoded into scripts or configuration files.

3. **Immutability & Non-Repudiation**:
   - The smart contract disallows modifying or deleting registered records.
   - Any official administrative update or deed reassignment must be anchored as a new version (e.g. `LR-2026-001-V2`).
