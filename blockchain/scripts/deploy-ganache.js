const {
  getWeb3,
  getArtifact,
  saveDeployedContract,
} = require("./contract-helper");

async function deploy() {
  const { web3, rpcUrl } = getWeb3();

  console.log("====================================================");
  console.log("Deploying LandRecordIntegrity to Ganache...");
  console.log("====================================================");
  console.log(`Connecting to Ganache at: ${rpcUrl}`);

  // Verify connection and obtain chain ID
  let chainId;
  try {
    chainId = await web3.eth.getChainId();
  } catch (err) {
    console.error(`\n❌ Failed to connect to Ganache at ${rpcUrl}.`);
    console.error("Please make sure Ganache is running on port 7545 (GUI or CLI).");
    console.error(`Original Error: ${err.message}\n`);
    process.exit(1);
  }

  const accounts = await web3.eth.getAccounts();
  if (!accounts || accounts.length === 0) {
    console.error("❌ No accounts available on Ganache instance.");
    process.exit(1);
  }

  const deployer = accounts[0];
  const balanceWei = await web3.eth.getBalance(deployer);
  const balanceEth = web3.utils.fromWei(balanceWei, "ether");

  console.log(`Ganache Chain ID : ${chainId}`);
  console.log(`Deployer Account : ${deployer}`);
  console.log(`Account Balance  : ${balanceEth} ETH`);

  const { abi, bytecode } = getArtifact();
  const contract = new web3.eth.Contract(abi);

  console.log("Broadcasting deployment transaction...");
  const deployTx = contract.deploy({
    data: bytecode,
  });

  let txHash = "";
  const gasEstimate = await deployTx.estimateGas({ from: deployer });
  const gasLimit = Math.ceil(Number(gasEstimate) * 1.2);

  const deployedInstance = await deployTx
    .send({
      from: deployer,
      gas: gasLimit,
    })
    .on("transactionHash", (hash) => {
      txHash = hash;
    });

  const contractAddress = deployedInstance.options.address;

  console.log("----------------------------------------------------");
  console.log(`Ganache Network  : ${rpcUrl} (Chain ID: ${chainId})`);
  console.log(`Deployer Address : ${deployer}`);
  console.log(`Contract Address : ${contractAddress}`);
  console.log(`Transaction Hash : ${txHash}`);
  console.log("====================================================");

  saveDeployedContract({
    network: "ganache",
    rpcUrl,
    chainId: Number(chainId),
    contractAddress,
    deployer,
    transactionHash: txHash,
    deployedAt: new Date().toISOString(),
    abi,
  });

  console.log("Deployment information saved to deployed-contract.json");
  return contractAddress;
}

if (require.main === module) {
  deploy()
    .then(() => process.exit(0))
    .catch((err) => {
      console.error("❌ Deployment failed:", err);
      process.exit(1);
    });
}

module.exports = deploy;
