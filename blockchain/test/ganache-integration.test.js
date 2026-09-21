/**
 * Automated integration test verifying LandRecordIntegrity with Web3.js against Ganache.
 * Tests all 9 scenarios: A through I.
 */
const { getWeb3, getArtifact } = require("../scripts/contract-helper");

async function runGanacheIntegrationTests() {
  console.log("========================================================");
  console.log("RUNNING GANACHE + WEB3.JS INTEGRATION TEST SUITE");
  console.log("========================================================\n");

  const { web3, rpcUrl } = getWeb3();
  console.log(`Connecting to Ganache RPC at: ${rpcUrl}`);

  let accounts;
  try {
    accounts = await web3.eth.getAccounts();
  } catch (err) {
    console.error(`\n❌ Could not connect to Ganache at ${rpcUrl}.`);
    console.error("Please start Ganache before running integration tests.");
    process.exit(1);
  }

  const deployer = accounts[0];
  console.log(`Using test deployer account : ${deployer}\n`);

  const { abi, bytecode } = getArtifact();
  const contractFactory = new web3.eth.Contract(abi);

  let passed = 0;
  let failed = 0;

  function assert(condition, message) {
    if (condition) {
      console.log(`  ✅ PASS: ${message}`);
      passed++;
    } else {
      console.error(`  ❌ FAIL: ${message}`);
      failed++;
    }
  }

  // SCENARIO A: Deploy contract successfully
  console.log("Scenario A: Deploy contract successfully to Ganache");
  let contractAddress;
  let contract;
  try {
    const deployTx = contractFactory.deploy({ data: bytecode });
    const gasEstimate = await deployTx.estimateGas({ from: deployer });
    const gasLimit = Math.ceil(Number(gasEstimate) * 1.2);

    const instance = await deployTx.send({
      from: deployer,
      gas: gasLimit,
    });
    contractAddress = instance.options.address;
    contract = new web3.eth.Contract(abi, contractAddress);

    assert(
      web3.utils.isAddress(contractAddress),
      `Contract deployed at valid address: ${contractAddress}`
    );
  } catch (err) {
    assert(false, `Deployment failed: ${err.message}`);
  }

  const sampleId = "LR-2026-001";
  const originalHash = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855";
  const tamperedHash = "ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff";

  // SCENARIO B: Register new land record
  console.log("\nScenario B: Register new land record");
  try {
    const regTx = await contract.methods
      .registerRecord(sampleId, originalHash)
      .send({ from: deployer, gas: 300000 });
    assert(
      regTx.transactionHash && regTx.transactionHash.startsWith("0x"),
      `Record registered with tx: ${regTx.transactionHash}`
    );
  } catch (err) {
    assert(false, `Registration failed: ${err.message}`);
  }

  // SCENARIO C: Retrieve registered record
  console.log("\nScenario C: Retrieve registered record");
  try {
    const record = await contract.methods.getRecord(sampleId).call();
    assert(
      record.recordIdOut === sampleId &&
        record.documentHash === originalHash &&
        Number(record.timestamp) > 0 &&
        record.isRegistered === true,
      `Retrieved record matches: id=${record.recordIdOut}, timestamp=${record.timestamp}`
    );
  } catch (err) {
    assert(false, `Retrieval failed: ${err.message}`);
  }

  // SCENARIO D: Verify correct hash -> TRUE
  console.log("\nScenario D: Verify correct hash (expected TRUE)");
  try {
    const result = await contract.methods.verifyRecord(sampleId, originalHash).call();
    assert(result === true, `Original hash verified as authentic (result = ${result})`);
  } catch (err) {
    assert(false, `Verification query failed: ${err.message}`);
  }

  // SCENARIO E: Verify modified/tampered hash -> FALSE
  console.log("\nScenario E: Verify modified/tampered hash (expected FALSE)");
  try {
    const result = await contract.methods.verifyRecord(sampleId, tamperedHash).call();
    assert(result === false, `Tampered hash correctly detected and rejected (result = ${result})`);
  } catch (err) {
    assert(false, `Tampered verification query failed: ${err.message}`);
  }

  // SCENARIO F: Reject duplicate record
  console.log("\nScenario F: Reject duplicate record registration");
  try {
    await contract.methods
      .registerRecord(sampleId, originalHash)
      .send({ from: deployer, gas: 300000 });
    assert(false, "Duplicate registration should have reverted but succeeded");
  } catch (err) {
    assert(
      err.message.includes("Record already registered") || err.message.includes("revert"),
      `Duplicate registration rejected as expected: ${err.message}`
    );
  }

  // SCENARIO G: Reject empty record ID
  console.log("\nScenario G: Reject empty record ID");
  try {
    await contract.methods
      .registerRecord("", originalHash)
      .send({ from: deployer, gas: 300000 });
    assert(false, "Empty record ID should have reverted but succeeded");
  } catch (err) {
    assert(
      err.message.includes("Record ID cannot be empty") || err.message.includes("revert"),
      `Empty record ID rejected: ${err.message}`
    );
  }

  // SCENARIO H: Reject empty document hash
  console.log("\nScenario H: Reject empty document hash");
  try {
    await contract.methods
      .registerRecord("LR-EMPTY-HASH", "")
      .send({ from: deployer, gas: 300000 });
    assert(false, "Empty document hash should have reverted but succeeded");
  } catch (err) {
    assert(
      err.message.includes("Document hash cannot be empty") || err.message.includes("revert"),
      `Empty document hash rejected: ${err.message}`
    );
  }

  // SCENARIO I: Handle non-existent record
  console.log("\nScenario I: Handle non-existent record");
  try {
    const exists = await contract.methods.isRecordRegistered("NON_EXISTENT_PARCEL").call();
    assert(exists === false, `isRecordRegistered for non-existent record returned false`);

    let threwExpected = false;
    try {
      await contract.methods.getRecord("NON_EXISTENT_PARCEL").call();
    } catch (e) {
      threwExpected = true;
    }
    assert(threwExpected, `getRecord for non-existent record reverted as expected`);
  } catch (err) {
    assert(false, `Non-existent record handling failed: ${err.message}`);
  }

  console.log("\n========================================================");
  console.log(`TEST SUMMARY: ${passed} passed, ${failed} failed`);
  console.log("========================================================\n");

  if (failed > 0) {
    process.exit(1);
  }
}

if (require.main === module) {
  runGanacheIntegrationTests()
    .then(() => process.exit(0))
    .catch((err) => {
      console.error("Test runner encountered an unhandled error:", err);
      process.exit(1);
    });
}

module.exports = runGanacheIntegrationTests;
