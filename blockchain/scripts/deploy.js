const hre = require("hardhat");

async function main() {
  console.log("====================================================");
  console.log("Deploying LandRecordIntegrity smart contract...");
  console.log("====================================================");

  const [deployer] = await hre.ethers.getSigners();
  console.log(`Deployer Account: ${deployer.address}`);

  const balance = await hre.ethers.provider.getBalance(deployer.address);
  console.log(`Account Balance : ${hre.ethers.formatEther(balance)} ETH`);

  const LandRecordIntegrity = await hre.ethers.getContractFactory("LandRecordIntegrity");
  const landRecordIntegrity = await LandRecordIntegrity.deploy();

  await landRecordIntegrity.waitForDeployment();

  const contractAddress = await landRecordIntegrity.getAddress();
  console.log("----------------------------------------------------");
  console.log(`Contract Address: ${contractAddress}`);
  console.log("Network         : " + hre.network.name);
  console.log("====================================================");

  return contractAddress;
}

if (require.main === module) {
  main()
    .then(() => process.exit(0))
    .catch((error) => {
      console.error("Deployment failed:", error);
      process.exit(1);
    });
}

module.exports = main;
