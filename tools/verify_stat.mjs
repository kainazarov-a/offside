#!/usr/bin/env node
/**
 * OFFSIDE · verify_stat.mjs — НАСТОЯЩАЯ on-chain проверка гола.
 * 1) тянет Merkle-пакет: GET /api/scores/stat-validation?fixtureId&seq&statKeys
 * 2) строит payload как в офиц. примере tx-on-chain/subscription_scores_1stat.ts
 * 3) вызывает validateStatV2(...).view() у программы Txoracle на MAINNET
 *    против PDA ["daily_scores_roots", u16le(epochDay)]
 * stdout: ОДНА строка JSON (парсится бекендом). Логи — в stderr.
 *
 *   node tools/verify_stat.mjs --fid 18213979 --seq 512 --stat 1 --expect 1
 */
import * as anchor from "@coral-xyz/anchor";
import { Connection, Keypair, PublicKey, ComputeBudgetProgram } from "@solana/web3.js";
import BN from "bn.js";
import axios from "axios";
import fs from "fs";
import path from "path";
import { fileURLToPath } from "url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const ROOT = path.join(__dirname, "..");
const err = (...a) => console.error("[verify]", ...a);
const out = (o) => { process.stdout.write(JSON.stringify(o) + "\n"); };

const args = process.argv.slice(2);
const argVal = (n, d) => { const i = args.indexOf(n); return i >= 0 && args[i + 1] ? args[i + 1] : d; };
const FID = argVal("--fid"), SEQ = argVal("--seq");
const STAT = argVal("--stat", "1");
const EXPECT = argVal("--expect", null);
if (!FID || !SEQ) { out({ error: "нужны --fid и --seq" }); process.exit(1); }

const PROGRAM_ID = new PublicKey("9ExbZjAapQww1vfcisDmrngPinHTEfpjYRWMunJgcKaA"); // mainnet Txoracle
const RPC = "https://api.mainnet-beta.solana.com";
const SEC = path.join(ROOT, ".secrets", "txline.json");

async function main() {
  const cfg = JSON.parse(fs.readFileSync(SEC, "utf8"));
  const api = cfg.apiBaseUrl.replace(/\/$/, "");
  const hdrs = () => ({ Authorization: `Bearer ${cfg.jwt}`, "X-Api-Token": cfg.apiToken });

  // ---- 1. Merkle-пакет от TxLINE (401 -> свежий гостевой JWT, ретрай)
  const url = `${api}/scores/stat-validation?fixtureId=${FID}&seq=${SEQ}&statKeys=${STAT}`;
  let val;
  try {
    val = (await axios.get(url, { headers: hdrs(), timeout: 25000 })).data;
  } catch (e) {
    if (e?.response?.status === 401) {
      err("401 — обновляю гостевой JWT");
      const r = await axios.post(`${cfg.apiOrigin.replace(/\/$/, "")}/auth/guest/start`);
      cfg.jwt = r.data.token;
      try { fs.writeFileSync(SEC, JSON.stringify(cfg, null, 2)); } catch (_) {}
      val = (await axios.get(url, { headers: hdrs(), timeout: 25000 })).data;
    } else {
      throw e;
    }
  }
  err("пакет получен: subTreeProof", val.subTreeProof?.length,
      "mainTreeProof", val.mainTreeProof?.length,
      "statsToProve", val.statsToProve?.length);

  // ---- 2. PDA дня
  const targetTs = val.summary.updateStats.minTimestamp;
  const epochDay = Math.floor(targetTs / 86400000);
  const [pda] = PublicKey.findProgramAddressSync(
    [Buffer.from("daily_scores_roots"), new BN(epochDay).toBuffer("le", 2)],
    PROGRAM_ID
  );

  const connection = new Connection(RPC, "confirmed");
  const acc = await connection.getAccountInfo(pda);
  const base = {
    fid: Number(FID), seq: Number(SEQ), statKey: Number(STAT),
    epochDay, pda: pda.toBase58(),
    solscan: `https://solscan.io/account/${pda.toBase58()}`,
    pdaExists: !!acc,
    eventStatRoot: Buffer.from(val.eventStatRoot).toString("hex"),
    subTreeRoot: Buffer.from(val.summary.eventStatsSubTreeRoot).toString("hex"),
    proofNodes: {
      subTree: val.subTreeProof.length,
      mainTree: val.mainTreeProof.length,
      stat: (val.statProofs || []).map(p => p.length),
    },
    stats: val.statsToProve,
  };
  if (!acc) { out({ ...base, ok: null, note: "корень дня ещё не опубликован on-chain" }); return; }

  // ---- 3. программа + payload (1-в-1 с офиц. примером)
  let idl;
  const localIdl = path.join(ROOT, "idl", "txoracle.mainnet.json");
  if (fs.existsSync(localIdl)) {
    idl = JSON.parse(fs.readFileSync(localIdl, "utf8"));
  } else {
    err("качаю IDL из репо TxODDS");
    idl = (await axios.get(
      "https://raw.githubusercontent.com/txodds/tx-on-chain/main/idl/txoracle.json",
      { timeout: 20000 })).data;
  }
  idl.address = PROGRAM_ID.toBase58();

  const kp = Keypair.fromSecretKey(
    Uint8Array.from(JSON.parse(fs.readFileSync(path.join(ROOT, "wallet.json"), "utf8"))));
  const provider = new anchor.AnchorProvider(connection, new anchor.Wallet(kp),
    { commitment: "confirmed" });
  anchor.setProvider(provider);
  const program = new anchor.Program(idl, provider);

  const mapProof = (arr) => arr.map(n => ({
    hash: Array.from(n.hash), isRightSibling: n.isRightSibling }));

  const payload = {
    ts: new BN(targetTs),
    fixtureSummary: {
      fixtureId: new BN(val.summary.fixtureId),
      updateStats: {
        updateCount: val.summary.updateStats.updateCount,
        minTimestamp: new BN(val.summary.updateStats.minTimestamp),
        maxTimestamp: new BN(val.summary.updateStats.maxTimestamp),
      },
      eventsSubTreeRoot: Array.from(val.summary.eventStatsSubTreeRoot),
    },
    fixtureProof: mapProof(val.subTreeProof),
    mainTreeProof: mapProof(val.mainTreeProof),
    eventStatRoot: Array.from(val.eventStatRoot),
    stats: val.statsToProve.map((statObj, i) => ({
      stat: statObj, statProof: mapProof(val.statProofs[i]) })),
  };

  // предикат: знаем ожидаемое значение -> equalTo, иначе "стат существует" (> -1)
  const predicate = EXPECT != null
    ? { threshold: Number(EXPECT), comparison: { equalTo: {} } }
    : { threshold: -1, comparison: { greaterThan: {} } };
  const strategy = {
    geometricTargets: [], distancePredicate: null,
    discretePredicates: [{ single: { index: 0, predicate } }],
  };

  err(`view: validateStatV2 fid=${FID} seq=${SEQ} stat=${STAT} expect=${EXPECT}`);
  const ok = await program.methods
    .validateStatV2(payload, strategy)
    .accounts({ dailyScoresMerkleRoots: pda })
    .preInstructions([ComputeBudgetProgram.setComputeUnitLimit({ units: 1_400_000 })])
    .view();

  out({ ...base, ok: !!ok, expect: EXPECT == null ? null : Number(EXPECT) });
}

main().catch(e => {
  out({ error: String(e?.response?.status || "") + " " +
        (JSON.stringify(e?.response?.data) || e?.message || String(e)) });
  process.exit(0); // ошибка уходит в JSON, не в exit-код — бекенд покажет её судье честно
});
