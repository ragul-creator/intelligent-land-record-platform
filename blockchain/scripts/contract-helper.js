const fs = require("fs");
const path = require("path");
const { Web3 } = require("web3");

const GANACHE_RPC_URL = process.env.GANACHE_URL || "http://127.0.0.1:7545";
const ARTIFACT_PATH = path.join(
  __dirname,
  "..",
  "artifacts",
  "contracts",
  "LandRecordIntegrity.sol",
  "LandRecordIntegrity.json"
);
const DEPLOYED_INFO_PATH = path.join(__dirname, "..", "deployed-contract.json");

/**
 * Initializes and returns a Web3 instance connected to Ganache.
 */
function getWeb3() {
  const web3 = new Web3(GANACHE_RPC_URL);
  return { web3, rpcUrl: GANACHE_RPC_URL };
}

/**
 * Loads the compiled smart contract artifact (ABI and bytecode).
 */
function getArtifact() {
  if (!fs.existsSync(ARTIFACT_PATH)) {
    throw new Error(
      `Artifact not found at ${ARTIFACT_PATH}. Please run 'npm run compile' first.`
    );
  }
  const artifact = JSON.parse(fs.readFileSync(ARTIFACT_PATH, "utf8"));
  return {
    abi: artifact.abi,
    bytecode: artifact.bytecode,
  };
}

/**
 * Saves deployment metadata (address, network, ABI) to deployed-contract.json.
 */
function saveDeployedContract(info) {
  fs.writeFileSync(DEPLOYED_INFO_PATH, JSON.stringify(info, null, 2), "utf8");
}

/**
 * Loads the deployed contract instance and deployment metadata.
 */
function getDeployedContract(web3Instance) {
  if (!fs.existsSync(DEPLOYED_INFO_PATH)) {
    throw new Error(
      "Deployed contract file (deployed-contract.json) not found.\n" +
        "Please deploy the contract first using: npm run deploy:ganache"
    );
  }

  const deployedInfo = JSON.parse(fs.readFileSync(DEPLOYED_INFO_PATH, "utf8"));
  const contract = new web3Instance.eth.Contract(
    deployedInfo.abi,
    deployedInfo.contractAddress
  );

  return {
    contract,
    deployedInfo,
    address: deployedInfo.contractAddress,
  };
}

module.exports = {
  GANACHE_RPC_URL,
  getWeb3,
  getArtifact,
  saveDeployedContract,
  getDeployedContract,
};
