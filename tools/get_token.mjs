#!/usr/bin/env node
/**
 * OFFSIDE · TxLINE token bootstrap — БЕЗ Phantom.
 * Кошелёк = локальный файл wallet.json (создаётся сам). Скрипт делает всё:
 *   1) создаёт/грузит кошелёк
 *   2) devnet: сам просит SOL из крана | mainnet: просит пополнить ~0.02 SOL
 *   3) on-chain subscribe() на бесплатный тир TxLINE
 *   4) активирует API-токен подписью
 *   5) сохраняет токены в .secrets/txline.json
 *
 * Запуск:
 *   node tools/get_token.mjs                          -> devnet, level 1 (60s delay)
 *   node tools/get_token.mjs --network mainnet --level 12   -> real-time (нужно ~0.02 SOL)
 */
import * as anchor from "@coral-xyz/anchor";
import {
  ASSOCIATED_TOKEN_PROGRAM_ID,
  TOKEN_2022_PROGRAM_ID,
  getAssociatedTokenAddressSync,
  createAssociatedTokenAccountIdempotentInstruction,
} from "@solana/spl-token";
import {
  Connection,
  Keypair,
  PublicKey,
  SystemProgram,
  Transaction,
  LAMPORTS_PER_SOL,
} from "@solana/web3.js";
import axios from "axios";
import nacl from "tweetnacl";
import fs from "fs";
import path from "path";
import { fileURLToPath } from "url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const ROOT = path.join(__dirname, "..");

const args = process.argv.slice(2);
const argVal = (n, d) => {
  const i = args.indexOf(n);
  return i >= 0 && args[i + 1] ? args[i + 1] : d;
};
const NETWORK = argVal("--network", "devnet");
const SERVICE_LEVEL_ID = parseInt(argVal("--level", NETWORK === "mainnet" ? "12" : "1"), 10);
const DURATION_WEEKS = 4;
const SELECTED_LEAGUES = [];

const CONFIG = {
  mainnet: {
    rpcUrl: "https://api.mainnet-beta.solana.com",
    apiOrigin: "https://txline.txodds.com",
    programId: new PublicKey("9ExbZjAapQww1vfcisDmrngPinHTEfpjYRWMunJgcKaA"),
    txlTokenMint: new PublicKey("Zhw9TVKp68a1QrftncMSd6ELXKDtpVMNuMGr1jNwdeL"),
  },
  devnet: {
    rpcUrl: "https://api.devnet.solana.com",
    apiOrigin: "https://txline-dev.txodds.com",
    programId: new PublicKey("6pW64gN1s2uqjHkn1unFeEjAwJkPGHoppGvS715wyP2J"),
    txlTokenMint: new PublicKey("4Zao8ocPhmMgq7PdsYWyxvqySMGx7xb9cMftPMkEokRG"),
  },
};

if (!CONFIG[NETWORK]) {
  console.error(`--network должен быть devnet или mainnet, получил: ${NETWORK}`);
  process.exit(1);
}
const { rpcUrl, apiOrigin, programId, txlTokenMint } = CONFIG[NETWORK];
const apiBaseUrl = `${apiOrigin}/api`;

const log = (...a) => console.log("[offside]", ...a);

// ---------- 1. кошелёк ----------
const WALLET_PATH = path.join(ROOT, "wallet.json");
let kp;
if (fs.existsSync(WALLET_PATH)) {
  kp = Keypair.fromSecretKey(Uint8Array.from(JSON.parse(fs.readFileSync(WALLET_PATH, "utf8"))));
  log("кошелёк загружен:", kp.publicKey.toBase58());
} else {
  kp = Keypair.generate();
  fs.writeFileSync(WALLET_PATH, JSON.stringify(Array.from(kp.secretKey)));
  log("кошелёк СОЗДАН:", kp.publicKey.toBase58());
  log("файл wallet.json = приватный ключ. НЕ удалять, НЕ коммитить, НЕ пересылать.");
}

const connection = new Connection(rpcUrl, "confirmed");
const wallet = new anchor.Wallet(kp);
const provider = new anchor.AnchorProvider(connection, wallet, { commitment: "confirmed" });
anchor.setProvider(provider);

async function main() {
  // ---------- 2. баланс ----------
  let bal = await connection.getBalance(kp.publicKey);
  log(`сеть: ${NETWORK} | level: ${SERVICE_LEVEL_ID} | баланс: ${(bal / LAMPORTS_PER_SOL).toFixed(4)} SOL`);

  if (bal < 0.004 * LAMPORTS_PER_SOL) {
    if (NETWORK === "devnet") {
      log("прошу 1 SOL из devnet-крана...");
      try {
        const sig = await connection.requestAirdrop(kp.publicKey, 1 * LAMPORTS_PER_SOL);
        await connection.confirmTransaction(sig, "confirmed");
        bal = await connection.getBalance(kp.publicKey);
        log(`кран налил, баланс: ${(bal / LAMPORTS_PER_SOL).toFixed(4)} SOL`);
      } catch (e) {
        console.error("\nКран не дал SOL (бывает, он капризный). Сделай вручную:");
        console.error("  1) открой https://faucet.solana.com");
        console.error("  2) вставь адрес:", kp.publicKey.toBase58());
        console.error("  3) выбери Devnet, запроси 1 SOL, потом перезапусти этот скрипт\n");
        process.exit(1);
      }
    } else {
      console.error("\nНа mainnet нужен газ. Отправь ~0.02 SOL с любой биржи на адрес:");
      console.error("  " + kp.publicKey.toBase58());
      console.error("Сеть при выводе: Solana. Потом перезапусти этот скрипт.\n");
      process.exit(1);
    }
  }

  // ---------- 3. программа (IDL с чейна или из файла) ----------
  let idl = null;
  try {
    idl = await anchor.Program.fetchIdl(programId, provider);
  } catch (_) {}
  if (!idl) {
    const localIdl = path.join(ROOT, "idl", `txoracle.${NETWORK}.json`);
    if (fs.existsSync(localIdl)) {
      idl = JSON.parse(fs.readFileSync(localIdl, "utf8"));
      log("IDL взят из файла", localIdl);
    } else {
      log("IDL не нашёлся on-chain — качаю из офиц. репозитория TxODDS...");
      const rawUrl = "https://raw.githubusercontent.com/txodds/tx-on-chain/main/idl/txoracle.json";
      const resp = await axios.get(rawUrl, { timeout: 20000 });
      idl = resp.data;
      idl.address = programId.toBase58(); // подгоняем под выбранную сеть
      fs.mkdirSync(path.join(ROOT, "idl"), { recursive: true });
      fs.writeFileSync(localIdl, JSON.stringify(idl));
      log("IDL сохранён:", localIdl);
    }
  }
  if (!idl.address) idl.address = programId.toBase58();
  const program = new anchor.Program(idl, provider);
  log("программа:", program.programId.toBase58());

  // ---------- 4. PDA и счета ----------
  const [tokenTreasuryPda] = PublicKey.findProgramAddressSync(
    [Buffer.from("token_treasury_v2")],
    program.programId
  );
  const tokenTreasuryVault = getAssociatedTokenAddressSync(
    txlTokenMint, tokenTreasuryPda, true, TOKEN_2022_PROGRAM_ID, ASSOCIATED_TOKEN_PROGRAM_ID
  );
  const [pricingMatrixPda] = PublicKey.findProgramAddressSync(
    [Buffer.from("pricing_matrix")],
    program.programId
  );
  const userTokenAccount = getAssociatedTokenAddressSync(
    txlTokenMint, kp.publicKey, false, TOKEN_2022_PROGRAM_ID, ASSOCIATED_TOKEN_PROGRAM_ID
  );

  // ATA под TxL может не существовать (free tier платежа не требует) — создаём идемпотентно
  const ataInfo = await connection.getAccountInfo(userTokenAccount);
  if (!ataInfo) {
    log("создаю token-account под TxL (нужен инструкции subscribe)...");
    const tx = new Transaction().add(
      createAssociatedTokenAccountIdempotentInstruction(
        kp.publicKey, userTokenAccount, kp.publicKey, txlTokenMint,
        TOKEN_2022_PROGRAM_ID, ASSOCIATED_TOKEN_PROGRAM_ID
      )
    );
    const sig = await provider.sendAndConfirm(tx, []);
    log("ATA создан:", sig);
  }

  // ---------- 5. on-chain subscribe ----------
  log(`подписываюсь on-chain: level=${SERVICE_LEVEL_ID}, weeks=${DURATION_WEEKS} (бесплатно)...`);
  const txSig = await program.methods
    .subscribe(SERVICE_LEVEL_ID, DURATION_WEEKS)
    .accounts({
      user: kp.publicKey,
      pricingMatrix: pricingMatrixPda,
      tokenMint: txlTokenMint,
      userTokenAccount,
      tokenTreasuryVault,
      tokenTreasuryPda,
      tokenProgram: TOKEN_2022_PROGRAM_ID,
      associatedTokenProgram: ASSOCIATED_TOKEN_PROGRAM_ID,
      systemProgram: SystemProgram.programId,
    })
    .rpc();
  log("subscribe tx:", txSig);

  // ---------- 6. активация API ----------
  log("получаю гостевой JWT...");
  const authResponse = await axios.post(`${apiOrigin}/auth/guest/start`);
  const jwt = authResponse.data.token;

  const messageString = `${txSig}:${SELECTED_LEAGUES.join(",")}:${jwt}`;
  const message = new TextEncoder().encode(messageString);
  const signatureBytes = nacl.sign.detached(message, kp.secretKey);
  const walletSignature = Buffer.from(signatureBytes).toString("base64");

  log("активирую API-токен...");
  const activationResponse = await axios.post(
    `${apiBaseUrl}/token/activate`,
    { txSig, walletSignature, leagues: SELECTED_LEAGUES },
    { headers: { Authorization: `Bearer ${jwt}` } }
  );
  const apiToken = activationResponse.data.token || activationResponse.data;

  // ---------- 7. сохранение ----------
  const secretsDir = path.join(ROOT, ".secrets");
  fs.mkdirSync(secretsDir, { recursive: true });
  const out = {
    network: NETWORK,
    serviceLevel: SERVICE_LEVEL_ID,
    apiOrigin,
    apiBaseUrl,
    wallet: kp.publicKey.toBase58(),
    txSig,
    jwt,
    apiToken,
    createdAt: new Date().toISOString(),
  };
  fs.writeFileSync(path.join(secretsDir, "txline.json"), JSON.stringify(out, null, 2));

  console.log("\n================ ГОТОВО ================");
  console.log("Сеть:", NETWORK, "| level:", SERVICE_LEVEL_ID);
  console.log("Токены сохранены в offside/.secrets/txline.json");
  console.log("Следующий шаг:  python backend\\probe_txline.py");
  console.log("========================================\n");
}

main().catch((e) => {
  console.error("\n[ОШИБКА]", e?.response?.status || "", e?.response?.data || e.message || e);
  console.error("Пришли этот вывод целиком — разберу.");
  process.exit(1);
});
