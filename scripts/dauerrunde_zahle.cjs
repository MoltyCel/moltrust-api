// The payer of the standing round (ops G4-5): USDC on Base, directly from
// 0xa175, one transfer per row of a plan that Lars released with "ok".
//
// Only scripts/dauerrunde_auszahlung.py calls this, and only after it has
// checked the release (reply "ok" from Lars to the weekly Telegram message,
// bound to week, version and the hash of the rows). Run by hand it pays
// nothing without --echt, and refuses a plan that carries no release.
//
//   node scripts/dauerrunde_zahle.cjs <plan.json> <nachweis.jsonl> [--echt]
//
// plan.json:
//   {"kw": "2026-W45", "version": 1, "zweck": "...", "zahler": "0x...",
//    "eigene": ["0x...", ...], "freigabe": {"update_id": ..., "zeilen_sha256": ...},
//    "zeilen": [{"adresse": "0x...", "betrag_mikro": "50000", "grundlage": "..."}]}
//
// The private key of 0xa175 is decrypted in memory the way the taskmarket CLI
// does it (device key from its key server, AES-256-GCM), as ~/ops-pay/zahle.cjs
// did for P1 and ST-2 #89 on 21.10.2026. It is never printed or written.
//
// Exit codes: 0 done, 1 error (a transfer may have gone out; the proof file
// says which), 2 plan refused, 3 balance too low (nothing sent).
//
// Order per row: simulate, send, await the receipt, append one proof line
// with the tx hash. A row whose (kw, adresse) already carries a tx in the
// proof file is skipped, never repeated. The balance is checked against the
// sum of the open rows before the first transfer, so a short wallet sends
// nothing at all instead of paying half a week.
"use strict";
const fs = require("fs");
const crypto = require("crypto");
const path = require("path");
const { createRequire } = require("module");

const USDC = "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913";
// One source per endpoint (CLAUDE.md, 09.10.2026): BASE_RPC from the secrets,
// no fallback to a public node.
const RPC = process.env.BASE_RPC || "";

function weg(code, text) {
  console.error(text);
  process.exit(code);
}

function lib() {
  const tm = path.join(process.env.HOME, ".npm-global/lib/node_modules/@lucid-agents/taskmarket/");
  const req = createRequire(tm + "package.json");
  return { viem: req("viem"), accounts: req("viem/accounts"), chains: req("viem/chains") };
}

function decrypt(dekHex, encHex) {
  const key = Buffer.from(dekHex, "hex");
  const data = Buffer.from(encHex, "hex");
  const d = crypto.createDecipheriv("aes-256-gcm", key, data.subarray(0, 12));
  d.setAuthTag(data.subarray(12, 28));
  return Buffer.concat([d.update(data.subarray(28)), d.final()]).toString("utf8");
}

async function konto(accounts, erwartet) {
  const ks = JSON.parse(fs.readFileSync(path.join(process.env.HOME, ".taskmarket/keystore.json"), "utf8"));
  const basis = ks.keyServerUrl || "https://api.taskmarket.dev";
  const res = await fetch(`${basis}/api/devices/${ks.deviceId}/key`, {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ deviceId: ks.deviceId, apiToken: ks.apiToken }),
  });
  if (!res.ok) throw new Error(`Schluesseldienst ${res.status}`);
  const { deviceEncryptionKey } = await res.json();
  const acct = accounts.privateKeyToAccount(decrypt(deviceEncryptionKey, ks.encryptedKey));
  if (acct.address.toLowerCase() !== erwartet.toLowerCase()) throw new Error("Konto ist nicht der Zahler des Plans");
  return acct;
}

function bezahlt(nachweis) {
  const s = new Set();
  if (!fs.existsSync(nachweis)) return s;
  for (const l of fs.readFileSync(nachweis, "utf8").split("\n")) {
    if (!l.trim()) continue;
    try {
      const r = JSON.parse(l);
      if (r.tx && r.adresse) s.add(`${r.kw}|${r.adresse.toLowerCase()}`);
    } catch (_) { /* a torn line carries no tx */ }
  }
  return s;
}

// Everything a plan must satisfy before a key is even fetched.
function pruefePlan(plan) {
  if (!plan || typeof plan !== "object") return "kein Plan";
  if (!/^\d{4}-W\d{2}$/.test(plan.kw || "")) return "kw fehlt";
  if (!plan.freigabe || !plan.freigabe.update_id || !plan.freigabe.zeilen_sha256) return "Plan ohne Freigabe";
  if (!/^0x[0-9a-fA-F]{40}$/.test(plan.zahler || "")) return "zahler fehlt";
  if (!Array.isArray(plan.eigene) || plan.eigene.length < 5) return "plan.eigene fehlt (app/own_addresses.py)";
  if (!Array.isArray(plan.zeilen) || plan.zeilen.length === 0) return "keine Zeilen";
  const eigene = plan.eigene.map((e) => e.toLowerCase());
  for (const z of plan.zeilen) {
    if (!/^0x[0-9a-fA-F]{40}$/.test(z.adresse || "")) return "Zeile ohne Base-Adresse";
    if (eigene.includes(z.adresse.toLowerCase())) return "Empfaenger ist eigene Adresse";
    if (!/^[1-9]\d*$/.test(String(z.betrag_mikro))) return "Betrag ist keine positive ganze Zahl";
  }
  return null;
}

(async () => {
  const [planPfad, nachweis, flag] = process.argv.slice(2);
  if (!planPfad || !nachweis) weg(2, "Aufruf: dauerrunde_zahle.cjs <plan.json> <nachweis.jsonl> [--echt]");
  const echt = flag === "--echt";
  const plan = JSON.parse(fs.readFileSync(planPfad, "utf8"));
  const fehler = pruefePlan(plan);
  if (fehler) weg(2, `Plan abgewiesen: ${fehler}`);
  if (!RPC) weg(2, "BASE_RPC ist nicht gesetzt");

  const { viem, accounts, chains } = lib();
  const ABI = viem.parseAbi([
    "function transfer(address to, uint256 value) returns (bool)",
    "function balanceOf(address) view returns (uint256)",
  ]);
  const pub = viem.createPublicClient({ chain: chains.base, transport: viem.http(RPC) });
  const acct = await konto(accounts, plan.zahler);
  const wal = viem.createWalletClient({ account: acct, chain: chains.base, transport: viem.http(RPC) });

  const schon = bezahlt(nachweis);
  const offen = plan.zeilen.filter((z) => !schon.has(`${plan.kw}|${z.adresse.toLowerCase()}`));
  const summe = offen.reduce((s, z) => s + BigInt(z.betrag_mikro), 0n);
  const vorher = await pub.readContract({ address: USDC, abi: ABI, functionName: "balanceOf", args: [acct.address] });
  console.log(JSON.stringify({ modus: echt ? "echt" : "probe", kw: plan.kw, offen: offen.length,
    uebersprungen: plan.zeilen.length - offen.length, summe_mikro: summe.toString(), usdc_vorher_mikro: vorher.toString() }));
  if (vorher < summe) weg(3, JSON.stringify({ bestand_zu_klein: true, usdc_vorher_mikro: vorher.toString(), summe_mikro: summe.toString() }));

  for (const z of offen) {
    const to = viem.getAddress(z.adresse);
    const wert = BigInt(z.betrag_mikro);
    await pub.simulateContract({ account: acct, address: USDC, abi: ABI, functionName: "transfer", args: [to, wert] });
    if (!echt) { console.log(JSON.stringify({ kw: plan.kw, adresse: to, betrag_mikro: z.betrag_mikro, probe: "ok" })); continue; }
    const tx = await wal.writeContract({ address: USDC, abi: ABI, functionName: "transfer", args: [to, wert] });
    const rc = await pub.waitForTransactionReceipt({ hash: tx, timeout: 120000 });
    const zeile = {
      ts: new Date().toISOString(), kw: plan.kw, version: plan.version, zweck: plan.zweck, adresse: to,
      betrag_mikro: z.betrag_mikro, betrag: (Number(wert) / 1e6).toFixed(6), weg: "direkt",
      tx, block: Number(rc.blockNumber), status: rc.status, gas_used: rc.gasUsed.toString(),
      freigabe_update_id: plan.freigabe.update_id, grundlage: z.grundlage || null,
    };
    fs.appendFileSync(nachweis, JSON.stringify(zeile) + "\n");
    console.log(JSON.stringify(zeile));
    if (rc.status !== "success") throw new Error("tx nicht erfolgreich, Abbruch");
  }
  const nachher = await pub.readContract({ address: USDC, abi: ABI, functionName: "balanceOf", args: [acct.address] });
  console.log(JSON.stringify({ usdc_nachher_mikro: nachher.toString() }));
})().catch((e) => { console.error("FEHLER", e.shortMessage || e.message); process.exit(1); });
