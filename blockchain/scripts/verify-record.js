const readline = require("readline");
const { getWeb3, getDeployedContract } = require("./contract-helper");

function prompt(question) {
  const rl = readline.createInterface({
    input: process.stdin,
    output: process.stdout,
  });
  return new Promise((resolve) => {
    rl.question(question, (answer) => {
      rl.close();
      resolve(answer.trim());
    });
  });
}

async function verifyRecord() {
  console.log("====================================");
  console.log("LAND RECORD BLOCKCHAIN VERIFICATION");
  console.log("====================================\n");

  let recordId = process.argv[2];
  let documentHash = process.argv[3];

  if (!recordId) {
    recordId = await prompt("Enter Record ID: ");
  } else {
    console.log(`Record ID: ${recordId}`);
  }

  if (!documentHash) {
    documentHash = await prompt("Enter Document Hash: ");
  } else {
    console.log(`Candidate Document Hash: ${documentHash}`);
  }

  if (!recordId) {
    console.error("\n❌ Record ID cannot be empty.");
    process.exit(1);
  }

  if (!documentHash) {
    console.error("\n❌ Document Hash cannot be empty.");
    process.exit(1);
  }

  const { web3 } = getWeb3();
  let contract;

  try {
    const deployed = getDeployedContract(web3);
    contract = deployed.contract;
  } catch (err) {
    console.error(`\n❌ ${err.message}`);
    process.exit(1);
  }

  console.log("\nQuerying blockchain for record verification...\n");

  try {
    // 1. Check if record exists
    const isRegistered = await contract.methods.isRecordRegistered(recordId).call();

    if (!isRegistered) {
      console.log(`Record Registered  : false`);
      console.log(`Verification Result: false\n`);
      console.log(`❌ Record '${recordId}' does not exist on the blockchain.`);
      return;
    }

    // 2. Retrieve stored record details
    const stored = await contract.methods.getRecord(recordId).call();
    const storedHash = stored.documentHash;

    // 3. Verify supplied hash using smart contract
    const verificationResult = await contract.methods.verifyRecord(recordId, documentHash).call();

    console.log(`Record Registered  : ${isRegistered}`);
    console.log(`Stored Hash        : ${storedHash}`);
    console.log(`Verification Result: ${verificationResult}\n`);

    if (verificationResult) {
      console.log("✅ Document is authentic. Hash matches.");
    } else {
      console.log("❌ Document has been modified. Hash does not match.");
    }
  } catch (err) {
    console.error(`❌ Verification query failed: ${err.message}`);
    process.exit(1);
  }
}

if (require.main === module) {
  verifyRecord()
    .then(() => process.exit(0))
    .catch((err) => {
      console.error("Unexpected error:", err);
      process.exit(1);
    });
}

module.exports = verifyRecord;
