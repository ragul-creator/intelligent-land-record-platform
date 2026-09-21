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

async function registerRecord() {
  console.log("====================================");
  console.log("LAND RECORD BLOCKCHAIN REGISTRATION");
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
    console.log(`Document Hash: ${documentHash}`);
  }

  if (!recordId) {
    console.error("\n❌ Record ID cannot be empty.");
    process.exit(1);
  }

  if (!documentHash) {
    console.error("\n❌ Document Hash cannot be empty.");
    process.exit(1);
  }

  const { web3, rpcUrl } = getWeb3();
  let contract, deployedInfo;

  try {
    const deployed = getDeployedContract(web3);
    contract = deployed.contract;
    deployedInfo = deployed.deployedInfo;
  } catch (err) {
    console.error(`\n❌ ${err.message}`);
    process.exit(1);
  }

  const accounts = await web3.eth.getAccounts();
  if (!accounts || accounts.length === 0) {
    console.error("\n❌ No accounts found in Ganache.");
    process.exit(1);
  }
  const sender = accounts[0];

  // Check if record is already registered
  const alreadyRegistered = await contract.methods.isRecordRegistered(recordId).call();
  if (alreadyRegistered) {
    console.error(`\n❌ Record already registered: '${recordId}' is already anchored on-chain.`);
    process.exit(1);
  }

  console.log("\nRegistering record...");

  try {
    const gasEstimate = await contract.methods
      .registerRecord(recordId, documentHash)
      .estimateGas({ from: sender });
    const gasLimit = Math.ceil(Number(gasEstimate) * 1.2);

    let txHash = "";
    const receipt = await contract.methods
      .registerRecord(recordId, documentHash)
      .send({
        from: sender,
        gas: gasLimit,
      })
      .on("transactionHash", (hash) => {
        txHash = hash;
      });

    // Retrieve stored record details
    const stored = await contract.methods.getRecord(recordId).call();
    const timestampInt = Number(stored.timestamp);
    const dateFormatted = new Date(timestampInt * 1000).toUTCString();

    console.log("\n------------------------------------");
    console.log(`Transaction Hash       : ${receipt.transactionHash || txHash}`);
    console.log(`Registered Record ID   : ${stored.recordIdOut}`);
    console.log(`Stored Document Hash   : ${stored.documentHash}`);
    console.log(`Registration Timestamp : ${timestampInt} (${dateFormatted})`);
    console.log(`Registered By Account  : ${sender}`);
    console.log("------------------------------------");
    console.log("✅ Record registered successfully.");
  } catch (err) {
    console.error(`\n❌ Failed to register record: ${err.message}`);
    process.exit(1);
  }
}

if (require.main === module) {
  registerRecord()
    .then(() => process.exit(0))
    .catch((err) => {
      console.error("Unexpected error:", err);
      process.exit(1);
    });
}

module.exports = registerRecord;
