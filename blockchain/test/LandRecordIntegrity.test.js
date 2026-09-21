const { expect } = require("chai");
const { ethers } = require("hardhat");

describe("LandRecordIntegrity", function () {
  let landRecordIntegrity;
  let owner;
  let addr1;

  const sampleRecordId = "LR-MH-PUN-2026-00104";
  const sampleDocumentHash = "a591a6d40bf420404a011733cfb7b190d62c65bf0bcda32b57b277d9ad9f146e"; // SHA-256
  const alteredDocumentHash = "ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff";

  beforeEach(async function () {
    [owner, addr1] = await ethers.getSigners();
    const LandRecordIntegrity = await ethers.getContractFactory("LandRecordIntegrity");
    landRecordIntegrity = await LandRecordIntegrity.deploy();
    await landRecordIntegrity.waitForDeployment();
  });

  describe("1. Deployment", function () {
    it("should deploy contract successfully with valid address", async function () {
      const address = await landRecordIntegrity.getAddress();
      expect(address).to.be.properAddress;
      expect(address).to.not.equal(ethers.ZeroAddress);
    });
  });

  describe("2. Record Registration", function () {
    it("should register a new land record successfully", async function () {
      await expect(
        landRecordIntegrity.registerRecord(sampleRecordId, sampleDocumentHash)
      ).to.not.be.reverted;

      const isRegistered = await landRecordIntegrity.isRecordRegistered(sampleRecordId);
      expect(isRegistered).to.be.true;
    });

    it("should emit RecordRegistered event upon successful registration", async function () {
      const tx = await landRecordIntegrity.connect(addr1).registerRecord(sampleRecordId, sampleDocumentHash);
      const receipt = await tx.wait();
      const block = await ethers.provider.getBlock(receipt.blockNumber);

      await expect(tx)
        .to.emit(landRecordIntegrity, "RecordRegistered")
        .withArgs(sampleRecordId, sampleDocumentHash, block.timestamp, addr1.address);
    });
  });

  describe("3. Record Retrieval", function () {
    it("should retrieve stored record details correctly", async function () {
      const tx = await landRecordIntegrity.registerRecord(sampleRecordId, sampleDocumentHash);
      const receipt = await tx.wait();
      const block = await ethers.provider.getBlock(receipt.blockNumber);

      const record = await landRecordIntegrity.getRecord(sampleRecordId);

      expect(record.recordIdOut).to.equal(sampleRecordId);
      expect(record.documentHash).to.equal(sampleDocumentHash);
      expect(record.timestamp).to.equal(block.timestamp);
      expect(record.isRegistered).to.be.true;
    });
  });

  describe("4. Hash Verification", function () {
    beforeEach(async function () {
      await landRecordIntegrity.registerRecord(sampleRecordId, sampleDocumentHash);
    });

    it("should return true when document hash matches", async function () {
      const isValid = await landRecordIntegrity.verifyRecord(sampleRecordId, sampleDocumentHash);
      expect(isValid).to.be.true;
    });

    it("should return false when candidate document hash does not match (tampered)", async function () {
      const isValid = await landRecordIntegrity.verifyRecord(sampleRecordId, alteredDocumentHash);
      expect(isValid).to.be.false;
    });
  });

  describe("5. Duplicate Record Rejection", function () {
    it("should reject duplicate registration of the same record ID", async function () {
      await landRecordIntegrity.registerRecord(sampleRecordId, sampleDocumentHash);

      await expect(
        landRecordIntegrity.registerRecord(sampleRecordId, sampleDocumentHash)
      ).to.be.revertedWith("Record already registered");
    });
  });

  describe("6. Timestamp Existence", function () {
    it("should record a valid, positive block timestamp upon registration", async function () {
      const tx = await landRecordIntegrity.registerRecord(sampleRecordId, sampleDocumentHash);
      const receipt = await tx.wait();
      const block = await ethers.provider.getBlock(receipt.blockNumber);

      const record = await landRecordIntegrity.getRecord(sampleRecordId);
      expect(record.timestamp).to.be.gt(0);
      expect(record.timestamp).to.equal(block.timestamp);
    });
  });

  describe("7. Failure Cases & Input Validation", function () {
    it("should reject registration with empty record ID", async function () {
      await expect(
        landRecordIntegrity.registerRecord("", sampleDocumentHash)
      ).to.be.revertedWith("Record ID cannot be empty");
    });

    it("should reject registration with empty document hash", async function () {
      await expect(
        landRecordIntegrity.registerRecord(sampleRecordId, "")
      ).to.be.revertedWith("Document hash cannot be empty");
    });

    it("should revert getRecord when record ID does not exist", async function () {
      await expect(
        landRecordIntegrity.getRecord("NON_EXISTENT_ID")
      ).to.be.revertedWith("Record not found");
    });

    it("should revert getRecord with empty record ID", async function () {
      await expect(
        landRecordIntegrity.getRecord("")
      ).to.be.revertedWith("Record ID cannot be empty");
    });

    it("should revert verifyRecord when record does not exist", async function () {
      await expect(
        landRecordIntegrity.verifyRecord("NON_EXISTENT_ID", sampleDocumentHash)
      ).to.be.revertedWith("Record not found");
    });

    it("should revert verifyRecord with empty record ID", async function () {
      await expect(
        landRecordIntegrity.verifyRecord("", sampleDocumentHash)
      ).to.be.revertedWith("Record ID cannot be empty");
    });

    it("should revert verifyRecord with empty document hash", async function () {
      await landRecordIntegrity.registerRecord(sampleRecordId, sampleDocumentHash);
      await expect(
        landRecordIntegrity.verifyRecord(sampleRecordId, "")
      ).to.be.revertedWith("Document hash cannot be empty");
    });

    it("should return false from isRecordRegistered for non-existent record", async function () {
      const exists = await landRecordIntegrity.isRecordRegistered("UNREGISTERED_ID");
      expect(exists).to.be.false;
    });
  });
});
