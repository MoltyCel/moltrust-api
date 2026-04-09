#!/usr/bin/env node
/**
 * MolTrust XMTP Outreach — sends wallet trust profile links to
 * unregistered wallets with x402 payment activity.
 *
 * Usage:
 *   node outreach_xmtp.js --dry-run    # check eligible wallets, no send
 *   node outreach_xmtp.js              # send messages
 *
 * Requires: BASE_WRITE_KEY in env (wallet private key for XMTP client)
 */

const { Client } = require("@xmtp/xmtp-js");
const { Wallet } = require("ethers");
const { Pool } = require("pg");
const fs = require("fs");
const path = require("path");

const DRY_RUN = process.argv.includes("--dry-run");
const MIN_TX = parseInt(process.env.MIN_TX || "1", 10);

// Load secrets
function loadSecret(name) {
  const secretsPath = path.join(process.env.HOME, ".moltrust_secrets");
  const lines = fs.readFileSync(secretsPath, "utf-8").split("\n");
  for (const line of lines) {
    if (line.startsWith(name + "=")) {
      return line.slice(name.length + 1).trim();
    }
  }
  return "";
}

const PRIVATE_KEY = loadSecret("BASE_WRITE_KEY");
if (!PRIVATE_KEY) {
  console.error("ERROR: BASE_WRITE_KEY not found in ~/.moltrust_secrets");
  process.exit(1);
}

const pool = new Pool({
  connectionString: "postgresql://moltstack@localhost/moltstack",
  max: 3,
});

function buildMessage(address, txCount, totalUsdc, source) {
  if (source === "erc8004") {
    return [
      `You are registered as ERC-8004 Agent #${txCount}.`,
      "",
      "MolTrust adds W3C DID-based identity and verifiable credentials",
      "to your ERC-8004 identity — free, takes 2 minutes.",
      "",
      `Your trust profile: https://moltrust.ch/wallet/${address}`,
      "",
      "The MolTrust Team",
      "https://moltrust.ch",
    ].join("\n");
  }
  return [
    `Your wallet has ${txCount} verified x402 transaction${txCount > 1 ? "s" : ""} on Base L2`,
    totalUsdc > 0 ? ` (${totalUsdc.toFixed(2)} USDC total).` : ".",
    "",
    "MolTrust can turn this on-chain activity into a portable Trust Score",
    "— free, takes 2 minutes.",
    "",
    `View your profile: https://moltrust.ch/wallet/${address}`,
    "",
    "The MolTrust Team",
    "https://moltrust.ch",
  ].join("\n");
}

async function getEligibleWallets() {
  // Source 1: payment_events (x402 tx activity)
  const payments = await pool.query(`
    SELECT p.from_address as wallet,
           COUNT(*) as tx_count,
           COALESCE(SUM(p.amount_usdc), 0)::float as total_usdc,
           MAX(p.received_at) as last_seen,
           'payment' as source
    FROM payment_events p
    LEFT JOIN agents a ON LOWER(a.wallet_address) = LOWER(p.from_address)
    LEFT JOIN outreach_sent o ON LOWER(o.wallet_address) = LOWER(p.from_address)
    WHERE a.did IS NULL
      AND o.wallet_address IS NULL
      AND p.from_address IS NOT NULL
      AND p.from_address != ''
    GROUP BY p.from_address
    HAVING COUNT(*) >= $1
    ORDER BY COUNT(*) DESC
  `, [MIN_TX]);

  // Source 2: erc8004_outreach (on-chain registered agents)
  const erc8004 = await pool.query(`
    SELECT e.wallet_address as wallet,
           e.agent_id as tx_count,
           0 as total_usdc,
           e.first_seen as last_seen,
           'erc8004' as source
    FROM erc8004_outreach e
    LEFT JOIN outreach_sent o ON LOWER(o.wallet_address) = LOWER(e.wallet_address)
    WHERE e.moltrust_registered = FALSE
      AND e.outreach_sent = FALSE
      AND o.wallet_address IS NULL
      AND e.wallet_address IS NOT NULL
    ORDER BY e.agent_id DESC
    LIMIT 50
  `);

  return [...payments.rows, ...erc8004.rows];
}

async function recordOutreach(wallet, xmtpCapable, messageId) {
  await pool.query(
    `INSERT INTO outreach_sent (wallet_address, channel, xmtp_capable, message_id)
     VALUES ($1, 'xmtp', $2, $3) ON CONFLICT DO NOTHING`,
    [wallet, xmtpCapable, messageId]
  );
}

async function main() {
  console.log(`\n=== MolTrust XMTP Outreach ===`);
  console.log(`Mode: ${DRY_RUN ? "DRY RUN" : "LIVE"}`);
  console.log(`Min TX threshold: ${MIN_TX}\n`);

  // Get eligible wallets
  const wallets = await getEligibleWallets();
  console.log(`Eligible wallets (${MIN_TX}+ tx, not registered, not contacted): ${wallets.length}`);

  if (wallets.length === 0) {
    console.log("No wallets to contact. Done.");
    await pool.end();
    return;
  }

  for (const w of wallets) {
    console.log(`  ${w.wallet}  tx=${w.tx_count}  usdc=${parseFloat(w.total_usdc).toFixed(2)}  src=${w.source || "payment"}`);
  }

  // Initialize XMTP client
  const wallet = new Wallet(PRIVATE_KEY);
  console.log(`\nXMTP sender: ${wallet.address}`);

  let xmtpClient;
  if (!DRY_RUN) {
    try {
      xmtpClient = await Client.create(wallet, { env: "production" });
      console.log("XMTP client initialized (production)\n");
    } catch (err) {
      console.error("XMTP init failed:", err.message);
      await pool.end();
      process.exit(1);
    }
  }

  // Process each wallet
  let sent = 0, notCapable = 0, errors = 0;

  for (const w of wallets) {
    const addr = w.wallet;
    try {
      if (DRY_RUN) {
        // In dry run, just check XMTP capability
        try {
          const tempClient = await Client.create(wallet, { env: "production" });
          const canMsg = await tempClient.canMessage(addr);
          console.log(`  [DRY] ${addr}: XMTP=${canMsg ? "YES" : "NO"}  tx=${w.tx_count}`);
          if (!canMsg) notCapable++;
          await tempClient.close();
        } catch {
          console.log(`  [DRY] ${addr}: XMTP=UNKNOWN (client error)  tx=${w.tx_count}`);
        }
        continue;
      }

      // Check if wallet can receive XMTP
      const canMsg = await xmtpClient.canMessage(addr);
      if (!canMsg) {
        console.log(`  [SKIP] ${addr}: not XMTP-capable`);
        await recordOutreach(addr, false, null);
        notCapable++;
        continue;
      }

      // Send message
      const msg = buildMessage(addr, w.tx_count, parseFloat(w.total_usdc), w.source || "payment");
      const conversation = await xmtpClient.conversations.newConversation(addr);
      const sentMsg = await conversation.send(msg);

      await recordOutreach(addr, true, sentMsg.id || "sent");
      sent++;
      console.log(`  [SENT] ${addr}  tx=${w.tx_count}  msgId=${sentMsg.id || "ok"}`);

      // Rate limit: 2 second delay between messages
      await new Promise(r => setTimeout(r, 2000));
    } catch (err) {
      console.error(`  [ERROR] ${addr}: ${err.message}`);
      errors++;
    }
  }

  console.log(`\n=== Report ===`);
  console.log(`Eligible:      ${wallets.length}`);
  console.log(`Sent:          ${sent}`);
  console.log(`Not XMTP:      ${notCapable}`);
  console.log(`Errors:        ${errors}`);
  console.log(`Mode:          ${DRY_RUN ? "DRY RUN" : "LIVE"}`);

  if (xmtpClient) await xmtpClient.close();
  await pool.end();
}

main().catch(err => {
  console.error("Fatal:", err);
  process.exit(1);
});
