// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

/**
 * @title LandRecordIntegrity
 * @notice Standalone smart contract for registering and verifying cryptographic integrity
 *         hashes of land records without storing sensitive PII on-chain.
 */
contract LandRecordIntegrity {
    // Structure holding minimal integrity metadata
    struct Record {
        string recordId;
        string documentHash;
        uint256 timestamp;
        bool isRegistered;
    }

    // Mapping from recordId to Record details
    mapping(string => Record) private _records;

    // Event emitted when a new record hash is successfully anchored on-chain
    event RecordRegistered(
        string recordId,
        string documentHash,
        uint256 timestamp,
        address indexed registeredBy
    );

    /**
     * @notice Register a new land record's cryptographic hash on the blockchain.
     * @param recordId Unique identifier for the land record (e.g. cadastral/document identifier).
     * @param documentHash Cryptographic SHA-256 hash of the land record document.
     */
    function registerRecord(
        string calldata recordId,
        string calldata documentHash
    ) external {
        require(bytes(recordId).length > 0, "Record ID cannot be empty");
        require(bytes(documentHash).length > 0, "Document hash cannot be empty");
        require(!_records[recordId].isRegistered, "Record already registered");

        _records[recordId] = Record({
            recordId: recordId,
            documentHash: documentHash,
            timestamp: block.timestamp,
            isRegistered: true
        });

        emit RecordRegistered(
            recordId,
            documentHash,
            block.timestamp,
            msg.sender
        );
    }

    /**
     * @notice Retrieve the registered record details by record ID.
     * @param recordId Unique identifier of the land record.
     * @return recordIdOut The record identifier.
     * @return documentHash The registered cryptographic document hash.
     * @return timestamp The Unix timestamp when the record was registered.
     * @return isRegistered The registration status boolean.
     */
    function getRecord(
        string calldata recordId
    )
        external
        view
        returns (
            string memory recordIdOut,
            string memory documentHash,
            uint256 timestamp,
            bool isRegistered
        )
    {
        require(bytes(recordId).length > 0, "Record ID cannot be empty");
        Record memory rec = _records[recordId];
        require(rec.isRegistered, "Record not found");

        return (
            rec.recordId,
            rec.documentHash,
            rec.timestamp,
            rec.isRegistered
        );
    }

    /**
     * @notice Verify a candidate document hash against the stored hash for a given record.
     * @param recordId Unique identifier of the land record.
     * @param documentHash The candidate document hash to verify.
     * @return isValid True if the candidate hash exactly matches the registered hash; false otherwise.
     */
    function verifyRecord(
        string calldata recordId,
        string calldata documentHash
    ) external view returns (bool isValid) {
        require(bytes(recordId).length > 0, "Record ID cannot be empty");
        require(bytes(documentHash).length > 0, "Document hash cannot be empty");
        require(_records[recordId].isRegistered, "Record not found");

        return keccak256(bytes(_records[recordId].documentHash)) == keccak256(bytes(documentHash));
    }

    /**
     * @notice Check whether a record ID is already registered.
     * @param recordId Unique identifier of the land record.
     * @return exists True if the record ID is registered, false otherwise.
     */
    function isRecordRegistered(
        string calldata recordId
    ) external view returns (bool exists) {
        return _records[recordId].isRegistered;
    }
}
