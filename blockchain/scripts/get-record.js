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

async function getRecord() {
  console.log("====================================");
  console.log("LAND RECORD BLOCKCHAIN RETRIEVAL");
  console.log("====================================\n");

  let recordId = process.argv[2];

  if (!recordId) {
    recordId = await prompt("Enter Record ID: ");
  } else {
    console.log(`Record ID: ${recordId}`);
  }

  if (!recordId) {
    console.error("\n❌ Record ID cannot be empty.");
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

  console.log("\nFetching record from blockchain...\n");

  try {
    const isRegistered = await contract.methods.isRecordRegistered(recordId).call();
    if (!isRegistered) {
      console.log(`❌ Record '${recordId}' does not exist on the blockchain.`);
      return;
    }

    const stored = await contract.methods.getRecord(recordId).call();
    const timestampInt = Number(stored.timestamp);
    const dateFormatted = new Date(timestampInt * 1000).toUTCString();

    console.log("------------------------------------");
    console.log(`Record ID           : ${stored.recordIdOut}`);
    console.log(`Document Hash       : ${stored.documentHash}`);
    console.log(`Timestamp           : ${timestampInt} (${dateFormatted})`);
    console.log(`Registration Status : ${stored.isRegistered ? "Registered (Active)" : "Inactive"}`);
    console.log("------------------------------------");
  } catch (err) {
    console.error(`❌ Failed to retrieve record: ${err.message}`);
    process.exit(1);
  }
}

if (require.main === module) {
  getRecord()
    .then(() => process.exit(0))
    .catch((err) => {
      console.error("Unexpected error:", err);
      process.exit(1);
    });
}

module.exports = getRecord;
