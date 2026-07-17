/* OFFSIDE · app.js v2 — vanilla, SSE-driven, canvas sparklines, no build */
"use strict";

const S = { matches:new Map(), series:new Map(), tape:[], signals:[], lags:[],
            open:[], closed:[], stats:{}, eq:[], dirty:true, replay:{},
            rp:{running:false}, tapeV:0 };
const OUT = ["home","draw","away"];
/* реплей приватен: чужие R-карточки этот браузер просто не рисует */
const CID = sessionStorage.oid || (sessionStorage.oid = Math.random().toString(36).slice(2,10));
const myReplays = new Set((sessionStorage.myReplays||"").split(",").filter(Boolean));
const foreignR = m => String(m||"").startsWith("R") && !myReplays.has(String(m));
const $ = id => document.getElementById(id);
const fmtP = p => p==null ? "–" : (p*100).toFixed(1)+"%";
const fmtT = t => new Date(t*1000).toLocaleTimeString();

/* ---------- tabs ---------- */
const TABS=["home","live","agent","lag","pnl","replay","verify","docs"];
function showTab(n){
  if(n.startsWith("d-")){                    // docs anchor deep-link
    document.querySelectorAll(".nav a.tab").forEach(a=>a.classList.toggle("active",a.dataset.tab==="docs"));
    TABS.forEach(t=>{const el=$("tab-"+t); if(el) el.hidden = t!=="docs";});
    const el=document.getElementById(n); if(el) el.scrollIntoView({block:"start"});
    S.dirty=true; return;
  }
  if(!TABS.includes(n)) n="home";
  document.querySelectorAll(".nav a.tab").forEach(a=>a.classList.toggle("active",a.dataset.tab===n));
  TABS.forEach(t=>{const el=$("tab-"+t); if(el) el.hidden = t!==n;});
  window.scrollTo({top:0});
  S.dirty=true;
}
window.addEventListener("hashchange",()=>showTab(location.hash.replace("#","")||"home"));
showTab(location.hash.replace("#","")||"home");

/* ---------- state ---------- */
function ensureMatch(m){
  if(!S.matches.has(m)) S.matches.set(m,{m,home:"?",away:"?",score:[0,0],minute:0,
    status:"pre",kickoff:null,probs:{},flash:0});
  return S.matches.get(m);
}
function serKey(m,o){ return m+"|"+o; }
function pushSeries(m,o,src,p){
  const k=serKey(m,o); if(!S.series.has(k)) S.series.set(k,{tx:[],pm:[]});
  const a=S.series.get(k)[src]; a.push(p); if(a.length>160) a.shift();
}
function pushTape(cls,text){
  S.tape.unshift({cls,text,ts:new Date().toLocaleTimeString()});
  if(S.tape.length>90) S.tape.pop();
  S.tapeV++;
}

function onEvent(ev){
  if(ev.m!=null && foreignR(ev.m)) return;          // не мой реплей — не моё дело
  switch(ev.type){
    case "remove":{ S.matches.delete(ev.m);
      OUT.forEach(o=>S.series.delete(serKey(ev.m,o)));
      S.signals=S.signals.filter(s=>s.m!==ev.m); break; }
    case "fixture":{ const M=ensureMatch(ev.m); M.home=ev.home; M.away=ev.away;
      if(ev.kickoff) M.kickoff=ev.kickoff; break; }
    case "tick":{ const M=ensureMatch(ev.m);
      (M.probs[ev.o] ||= {})[ev.src]=ev.p; pushSeries(ev.m,ev.o,ev.src,ev.p);
      M.flash=Date.now(); break; }
    case "event":{ const M=ensureMatch(ev.m);
      if(ev.score) M.score=ev.score; if(ev.minute!=null) M.minute=ev.minute;
      if(ev.kind==="kickoff"){M.status="live";pushTape("",`▶ KICKOFF · ${M.home} vs ${M.away}`);}
      if(ev.kind==="goal") pushTape("goal",`⚽ GOAL ${ev.team==="home"?M.home:M.away} · ${M.score[0]}–${M.score[1]} · ${ev.minute}'`);
      if(ev.kind==="ft"){M.status="ft";pushTape("",`⏹ FT · ${M.home} ${M.score[0]}–${M.score[1]} ${M.away}`);}
      break; }
    case "signal":{ S.signals.unshift(ev); if(S.signals.length>90)S.signals.pop();
      pushTape("signal",`◎ SIGNAL #${ev.id} ${ev.kind} · ${name(ev.m)} ${ev.o} · gap ${ev.gap==null?"–":(ev.gap*100).toFixed(1)+"pp"}`);
      break; }
    case "lag":{ S.lags.unshift(ev); if(S.lags.length>200)S.lags.pop();
      pushTape("disloc",`⏱ crowd reacted in ${ev.react_ms==null?">60s":ev.react_ms+"ms"} · gap ${ev.gap0_pp}pp`);
      break; }
    case "trade":{
      if(ev.status==="open"){ S.open.unshift(ev);
        pushTape("signal",`▲ OPEN #${ev.id} ${ev.side} ${name(ev.m)} ${ev.o} @ ${ev.entry}`);
      } else { S.open=S.open.filter(x=>x.id!==ev.id);
        S.closed.unshift(ev); if(S.closed.length>200)S.closed.pop();
        pushTape(ev.pnl>=0?"signal":"goal",`${ev.pnl>=0?"✔":"✘"} CLOSE #${ev.id} ${ev.pnl>=0?"+":""}${ev.pnl}$ · ${ev.reason}`);
      } break; }
    case "stats":{ S.stats=ev; S.eq.push(ev.equity??0); if(S.eq.length>240)S.eq.shift(); break; }
  }
  S.dirty=true;
}
function name(m){ const M=S.matches.get(m); return M?`${M.home}–${M.away}`:m; }
function nextKick(){
  const pre=[...S.matches.values()].filter(m=>m.status==="pre"&&m.kickoff&&m.kickoff>Date.now())
    .sort((a,b)=>a.kickoff-b.kickoff)[0];
  if(!pre) return "";
  const d=pre.kickoff-Date.now(), h=Math.floor(d/3.6e6), mn=Math.floor(d%3.6e6/6e4);
  return ` First kickoff in ${h}h ${String(mn).padStart(2,"0")}m: ${pre.home} vs ${pre.away}.`;
}

/* ---------- SSE + snapshot ---------- */
const es=new EventSource("/api/stream");
es.onopen =()=>{const b=$("feedBadge");b.textContent="live";b.className="badge anchored";};
es.onerror=()=>{const b=$("feedBadge");b.textContent="reconnecting…";b.className="badge live";};
es.onmessage=e=>{try{onEvent(JSON.parse(e.data));}catch(_){}};
fetch("/api/state").then(r=>r.json()).then(sn=>{
  (sn.matches||[]).forEach(mt=>{
    if(foreignR(mt.m)) return;
    const M=ensureMatch(mt.m);
    Object.assign(M,{home:mt.home,away:mt.away,score:mt.score,minute:mt.minute,
                     status:mt.status,kickoff:mt.kickoff});
    OUT.forEach(o=>{ M.probs[o]={tx:mt.probs[o].tx,pm:mt.probs[o].pm};
      const k=serKey(mt.m,o), s={tx:[],pm:[]};
      (mt.probs[o].spark||[]).forEach(x=>s.tx.push(x[1]));
      (mt.probs[o].spark_pm||[]).forEach(x=>s.pm.push(x[1]));
      S.series.set(k,s); });
  });
  S.signals=(sn.signals||[]).filter(x=>!foreignR(x.m)).slice().reverse();
  S.lags=(sn.lags||[]).filter(x=>!foreignR(x.m)).slice().reverse();
  S.open=(sn.open_trades||[]).filter(x=>!foreignR(x.m));
  S.closed=(sn.closed||[]).filter(x=>!foreignR(x.m)).slice().reverse();
  S.stats=sn.stats||{}; S.cfg=sn.cfg||{}; S.dirty=true;
}).catch(()=>{});

/* ---------- canvas helpers ---------- */
function spark(cv,tx,pm){
  const w=cv.width=cv.clientWidth*2, h=cv.height=56, c=cv.getContext("2d");
  c.clearRect(0,0,w,h);
  const all=tx.concat(pm); if(all.length<2) return;
  let mn=Math.min(...all), mx=Math.max(...all);
  if(mx-mn<0.02){const mid=(mx+mn)/2;mn=mid-0.01;mx=mid+0.01;}
  const X=(i,n)=>i/(n-1)*(w-4)+2, Y=v=>h-3-(v-mn)/(mx-mn)*(h-6);
  const line=(arr,col,lw)=>{ if(arr.length<2)return;
    c.strokeStyle=col;c.lineWidth=lw;c.beginPath();
    arr.forEach((v,i)=>i?c.lineTo(X(i,arr.length),Y(v)):c.moveTo(X(i,arr.length),Y(v)));
    c.stroke(); };
  line(pm,"rgba(57,199,255,.9)",2); line(tx,"rgba(200,255,46,.95)",3);
}
function histogram(cv,vals){
  const w=cv.width=cv.clientWidth*2,h=cv.height=180,c=cv.getContext("2d");
  c.clearRect(0,0,w,h);
  const edges=[0,1000,2000,4000,8000,15000,30000,60000];
  const labels=["<1s","1-2","2-4","4-8","8-15","15-30","30-60",">60/–"];
  const bins=new Array(8).fill(0);
  vals.forEach(v=>{ if(v==null){bins[7]++;return;}
    for(let i=0;i<7;i++) if(v>=edges[i]&&v<edges[i+1]){bins[i]++;return;}
    bins[7]++; });
  const mx=Math.max(1,...bins), bw=w/8;
  bins.forEach((b,i)=>{ const bh=b/mx*(h-40);
    c.fillStyle=i===7?"rgba(255,77,77,.8)":"rgba(200,255,46,.85)";
    c.fillRect(i*bw+8,h-24-bh,bw-16,bh);
    c.fillStyle="#8B949C";c.font="16px JetBrains Mono";c.textAlign="center";
    c.fillText(labels[i],i*bw+bw/2,h-6);
    if(b){c.fillStyle="#F2F5F3";c.fillText(b,i*bw+bw/2,h-30-bh);} });
}

/* ---------- renders ---------- */
const TIP={
  "crowd lag p50":"median time the crowd (Polymarket) needs to absorb a sharp move",
  "brier":"signal calibration: 0 is perfect, 0.25 is a coin flip",
  "accuracy":"share of signals whose favourite at signal time actually won",
  "paper equity":"cumulative paper PnL under the cost model printed on PnL",
  "equity $":"cumulative paper PnL under the cost model printed above the table",
  "win rate":"share of closed paper trades with positive PnL",
  "signals":"sharp jumps and goals the detector considered worth acting on",
  "dislocations":"moments when the crowd price lagged the sharp feed",
  "no reaction":"dislocations the crowd never closed within 60 seconds"};
function kv(el,pairs){ el.innerHTML=pairs.map(([k,v])=>
  `<div class="item" title="${TIP[k]||""}"><div class="k">${k}</div><div class="v">${v??"–"}</div></div>`).join(""); }
function countdown(ko){ if(!ko)return""; const d=ko-Date.now(); if(d<=0)return"";
  const h=Math.floor(d/3.6e6),mn=Math.floor(d%3.6e6/6e4);
  return `kickoff in ${h}h ${String(mn).padStart(2,"0")}m`; }

function renderLive(){
  const rk=x=>myReplays.has(String(x.m))?-1:(x.status==="live"?0:x.status==="pre"?1:2);
  const ms=[...S.matches.values()].sort((a,b)=>
    rk(a)-rk(b) || (a.kickoff||9e15)-(b.kickoff||9e15));
  $("cards").innerHTML = ms.length? ms.map(M=>{
    const cd=M.status==="pre"?countdown(M.kickoff):"";
    const rows=OUT.map(o=>{
      const p=M.probs[o]||{}, k=serKey(M.m,o);
      return `<div class="probrow">
        <div class="lbl">${o}</div>
        <div class="mid">
          <canvas class="spark" data-k="${k}"></canvas>
          <div class="bars">
            <div class="bar tx" style="width:${((p.tx??0)*100).toFixed(1)}%"></div>
            <div class="bar pm" style="width:${((p.pm??0)*100).toFixed(1)}%"></div>
          </div>
        </div>
        <div class="vals"><span class="tx">${fmtP(p.tx)}</span><span class="sep">·</span><span class="pm">${fmtP(p.pm)}</span></div>
      </div>`;}).join("");
    const live=M.status==="live", isR=myReplays.has(String(M.m));
    const mine = isR && S.rp.running && S.rp.cid===CID && ("R"+S.rp.fid)===String(M.m);
    const prog = mine && S.rp.msg ? ` · <span class="rp-prog">${S.rp.msg}</span>` : "";
    const stopBtn = mine ? ` <button class="btnstop" onclick="stopReplay()">■ stop</button>` : "";
    return `<div class="card match ${live?"is-live":""} ${isR?"is-replay":""} ${Date.now()-M.flash<400?"pulse":""}">
      <div class="teams"><span>${M.home}</span><span class="score">${M.score[0]}–${M.score[1]}</span><span>${M.away}</span></div>
      <div class="meta">${isR?`<span class="chip-replay">REPLAY ×30 · only you see this</span> · `:""}${live?`<span class="dotlive"></span>LIVE · ${M.minute}'`:M.status.toUpperCase()} ${cd?"· "+cd:""} · <span class="fid">${M.m}</span>${prog}${stopBtn}</div>
      ${rows}</div>`;
  }).join("") : `<div class="empty">Waiting for the feed.</div>`;
  document.querySelectorAll("canvas.spark").forEach(cv=>{
    const s=S.series.get(cv.dataset.k)||{tx:[],pm:[]}; spark(cv,s.tx,s.pm); });
  if(S.tapeV!==S._tapeVR){ S._tapeVR=S.tapeV;      // тейп перерисовываем только при новых строках (без мельтешения)
  $("tape").innerHTML=S.tape.map(x=>
    `<div class="tape-item ${x.cls}"><span class="ts">${x.ts}</span><span>${x.text}</span></div>`).join("")
    || `<div class="empty">The tape is empty. It fills the moment a match goes live.</div>`; }
  const st=S.stats;
  kv($("heroKv"),[["matches",[...S.matches.values()].filter(m=>m.status!=="ft").length],
    ["signals",st.signals],["crowd lag p50",st.lag_p50_ms==null?"–":st.lag_p50_ms+"ms"],
    ["paper equity",st.equity==null?"–":(st.equity>=0?"+$":"-$")+Math.abs(st.equity)]]);
}
function renderAgent(){
  const st=S.stats;
  kv($("agentKv"),[["signals",st.signals],["open",st.open_trades],["closed",st.n_trades],
    ["accuracy",st.acc==null?"–":st.acc+"%"],["brier",st.brier]]);
  $("sigTable").tBodies[0].innerHTML=S.signals.slice(0,40).map(s=>
    `<tr><td class="num">${s.id}</td><td class="num">${fmtT(s.t)}</td><td>${name(s.m)}</td>
     <td>${s.o}</td><td>${s.kind}</td>
     <td class="num ${s.dp>0?"up":"down"}">${s.dp==null?"–":(s.dp*100).toFixed(1)}</td>
     <td class="num">${s.gap==null?"–":(s.gap*100).toFixed(1)}</td></tr>`).join("")
    || `<tr><td colspan="7" class="empty">No signals yet. Signals are born during live play, and every match is still pre-kickoff.${nextKick()}<br><a class="btn ghost" style="margin-top:12px" href="#replay">Watch a replay instead</a></td></tr>`;
  $("openTable").tBodies[0].innerHTML=S.open.map(t=>
    `<tr><td class="num">${t.id}</td><td>${name(t.m)}</td><td>${t.o}</td><td>${t.side}</td>
     <td class="num">${t.entry}</td><td class="num">${t.fair0}</td></tr>`).join("")
    || `<tr><td colspan="6" class="empty">No open positions. The agent only trades live dislocations.${nextKick()}</td></tr>`;
}
function renderLag(){
  const reacts=S.lags.map(x=>x.react_ms);
  const good=reacts.filter(v=>v!=null).sort((a,b)=>a-b);
  kv($("lagKv"),[["dislocations",S.lags.length],
    ["median",good.length?good[good.length>>1]+"ms":"–"],
    ["p90",good.length?good[Math.floor(good.length*0.9)]+"ms":"–"],
    ["no reaction",reacts.filter(v=>v==null).length]]);
  histogram($("lagHist"),reacts);
  $("lagTable").tBodies[0].innerHTML=S.lags.slice(0,60).map(l=>
    `<tr><td class="num">${l.sig}</td><td>${name(l.m)}</td><td class="num">${l.gap0_pp}</td>
     <td class="num ${l.react_ms==null?"down":""}">${l.react_ms==null?"no reaction":l.react_ms}</td></tr>`).join("")
    || `<tr><td colspan="4" class="empty">No dislocations yet. Reaction times get measured during live matches only.${nextKick()}<br><a class="btn ghost" style="margin-top:12px" href="#replay">Watch a replay instead</a></td></tr>`;
}
function renderPnl(){
  const st=S.stats, c=S.cfg||{};
  kv($("pnlKv"),[["equity $",st.equity],["trades",st.n_trades],
    ["win rate",st.wr==null?"–":st.wr+"%"],["avg pnl $",st.avg_pnl],
    ["crowd lag p50",st.lag_p50_ms==null?"–":st.lag_p50_ms+"ms"]]);
  spark($("eqSpark"),S.eq,[]);
  $("assump").textContent=`honest model: latency ${c.latency_s??1.5}s · spread ${((c.spread??0.02)*100).toFixed(1)}pp paid both ways · stake $${c.stake??100} · min gap ${((c.min_gap_pp??0.03)*100).toFixed(0)}pp · paper only`;
  $("pnlTable").tBodies[0].innerHTML=S.closed.slice(0,60).map(t=>
    `<tr><td class="num">${t.id}</td><td>${name(t.m)}</td><td>${t.o}</td><td>${t.side}</td>
     <td class="num">${t.entry}</td><td class="num">${t.exit}</td>
     <td class="num ${t.pnl>=0?"up":"down"}">${t.pnl>=0?"+":""}${t.pnl}</td><td>${t.reason}</td></tr>`).join("")
    || `<tr><td colspan="8" class="empty">No closed trades yet. Paper trades open and close during live play.${nextKick()}<br><a class="btn ghost" style="margin-top:12px" href="#verify">Prove a real goal on Verify meanwhile</a></td></tr>`;
}
async function renderReplay(){
  if(S.replay.loaded) return;
  S.replay.loaded=true;
  try{
    const r=await fetch("/api/replay/list"); const list=await r.json();
    $("replayList").innerHTML = Array.isArray(list)&&list.length ?
      list.map(f=>`<div class="rp-item"><div><b>${f.home} vs ${f.away}</b>
        <span class="muted">· ${f.comp} · ${new Date(f.start).toLocaleString()}</span></div>
        <button class="btn" onclick="startReplay('${f.fid}',this)">Replay ×30</button></div>`).join("")
      : `<div class="empty">No finished matches in the replay window yet. Tonight's quarterfinals appear here about 6 hours after the final whistle.</div>`;
  }catch(e){ $("replayList").innerHTML=`<div class="empty">${e}</div>`; }
}
window.startReplay=async (fid,btn)=>{
  if(btn){btn.disabled=true;btn.textContent="STARTING…";}
  try{
    const r=await fetch(`/api/replay/start?fid=${fid}&speed=30&cid=${CID}`,{method:"POST"});
    const d=await r.json().catch(()=>({}));
    if(!r.ok){
      if(btn){btn.disabled=false;btn.textContent="Replay ×30";}
      const note=d.error==="replay busy"
        ? "⏳ replay engine is busy with another visitor — try again in a minute"
        : "replay error: "+(d.error||r.status);
      const el=$("rpStatus"); if(el) el.textContent=note;
      pushTape("goal",note); S.dirty=true; return;
    }
    myReplays.add("R"+fid); sessionStorage.myReplays=[...myReplays].join(",");
    if(btn){btn.disabled=false;btn.textContent="Replay ×30";}
    pushTape("signal","▶ REPLAY started · pinned on top of Live, visible only to you");
    location.hash="#live";
  }catch(e){ if(btn){btn.disabled=false;btn.textContent="Replay ×30";} }
};
window.stopReplay=async ()=>{
  try{ await fetch(`/api/replay/stop?cid=${CID}`,{method:"POST"}); }catch(_){}
  pushTape("","■ replay stopped"); S.dirty=true;
};
/* пульс реплей-движка: статус-строка + перерисовка кнопки stop на карточке */
setInterval(async ()=>{
  try{
    const r=await fetch("/api/replay/status"); const d=await r.json();
    const changed=JSON.stringify(d)!==JSON.stringify(S.rp);
    S.rp=d; if(changed) S.dirty=true;
    const el=$("rpStatus");
    if(el){
      el.innerHTML = d.running
        ? (d.cid===CID
           ? `▶ your replay is running — <b>${d.msg||"working"}</b> · <button class="btnstop" onclick="stopReplay()">■ stop</button> · <a href="#live">watch it on Live</a>`
           : `⏳ replay engine is busy with another visitor's run — try again in a minute`)
        : "";
    }
  }catch(_){}
},2500);

/* ---------- verify ---------- */
const V={loaded:false,byFid:new Map()};
async function renderVerify(){
  if(V.loaded) return; V.loaded=true;
  try{
    const r=await fetch("/api/verify/candidates"); const list=await r.json();
    if(!Array.isArray(list)||!list.length){
      $("vSel").innerHTML=`<option value="">no finished matches yet, check back after tonight's games</option>`;
      return;
    }
    list.forEach(f=>V.byFid.set(f.fid,f));
    $("vSel").innerHTML=`<option value="">— pick a match —</option>`+list.map(f=>
      `<option value="${f.fid}">${f.home} vs ${f.away} · ${new Date(f.start).toLocaleDateString()}${f.live_goals?" · caught live":""}</option>`).join("");
    // авто-демо: сразу показываем голы лучшего кандидата и СРАЗУ доказываем последний гол
    const first=(list.find(f=>f.live_goals>0)||list[0]);
    if(first){ $("vSel").value=first.fid; loadGoals(first.fid,true); }
  }catch(e){ $("vSel").innerHTML=`<option value="">${e}</option>`; }
}
window.loadGoals=async (fid,auto)=>{
  if(!fid) return;
  $("vGoals").innerHTML=`<div class="empty">Reading match history.</div>`;
  try{
    const r=await fetch(`/api/verify/goals?fid=${fid}`); const d=await r.json();
    if(d.error){ $("vGoals").innerHTML=`<div class="empty">${d.error}</div>`; return; }
    const f=V.byFid.get(fid)||{home:"P1",away:"P2"};
    $("vGoals").innerHTML = d.goals.length? d.goals.map(g=>
      `<button class="goal-btn" onclick="proveGoal('${fid}',${g.seq},'${g.stat_key}',${g.value},'${g.side}',${g.minute},'${g.score[0]}–${g.score[1]}')">
        <span>⚽ ${g.minute}' · ${g.side==="home"?f.home:f.away}</span>
        <span><span class="gsc">${g.score[0]}–${g.score[1]}</span> · seq ${g.seq}</span>
      </button>`).join("")
      : `<div class="empty">0 to 0. No goals to prove in this match.</div>`;
    if(auto && d.goals.length){
      const g=d.goals[d.goals.length-1];   // самый свежий гол — сразу на on-chain проверку
      proveGoal(fid,g.seq,g.stat_key,g.value,g.side,g.minute,`${g.score[0]}–${g.score[1]}`);
    }
  }catch(e){ $("vGoals").innerHTML=`<div class="empty">${e}</div>`; }
};
window.proveGoal=async (fid,seq,stat,expect,side,minute,score)=>{
  const f=V.byFid.get(fid)||{home:"P1",away:"P2"};
  $("vPanel").innerHTML=`<div class="empty">Fetching the Merkle proof, then running validateStatV2 on Solana mainnet. Up to 30 seconds.</div>`;
  try{
    const r=await fetch(`/api/verify/prove?fid=${fid}&seq=${seq}&stat=${stat}&expect=${expect}`);
    const d=await r.json();
    if(d.error){ $("vPanel").innerHTML=`<div class="empty">${d.error}</div>`; return; }
    const oc=d.onchain||{}, api=d.api||{};
    let badge;
    if(oc.ok===true)  badge=`<div class="vbadge ok">✔ PROVED ON-CHAIN · goals(${side}) = ${expect}</div>`;
    else if(oc.ok===false) badge=`<div class="vbadge bad">✘ REJECTED BY PROGRAM</div>`;
    else if(oc.pdaExists===false) badge=`<div class="vbadge wait">Daily root not on chain yet. Proof package below.</div>`;
    else badge=`<div class="vbadge wait">Proof package fetched. On chain view unavailable: ${oc.error||"unknown"}</div>`;
    const row=(k,v)=>`<div class="vrow"><span class="k">${k}</span><span class="v">${v}</span></div>`;
    $("vPanel").innerHTML = badge
      + row("goal", `${minute}' · ${side==="home"?f.home:f.away} · ${score}`)
      + row("fixture / seq", `${fid} / ${seq}`)
      + row("stat", `key ${stat} (total goals) = ${expect}`)
      + row("proof nodes", api.proofNodes?`stat ${ (api.proofNodes.stat||[]).join("+") } · fixture ${api.proofNodes.subTree} · day ${api.proofNodes.mainTree}`:"–")
      + row("event-stat root", api.eventStatRoot? `<span class="hex">${api.eventStatRoot.slice(0,20)}…${api.eventStatRoot.slice(-8)}</span>`:"–")
      + row("epoch day", api.epochDay??"–")
      + (oc.pda? row("root account (PDA)", `<a class="mono" href="${oc.solscan}" target="_blank" rel="noopener">${oc.pda}</a>`):"")
      + `<p class="muted" style="font-family:var(--mono);font-size:11px;margin-top:12px">
         validateStatV2 rebuilds the Merkle path from this stat leaf up to the daily root stored
         in the account above and compares hashes inside the Solana runtime. The verdict is the
         program's, not ours.</p>`;
  }catch(e){ $("vPanel").innerHTML=`<div class="empty">${e}</div>`; }
};

setInterval(()=>{
  if(!S.dirty) return; S.dirty=false;
  const t=location.hash.replace("#","")||"home";
  if(t==="live")renderLive(); else if(t==="agent")renderAgent();
  else if(t==="lag")renderLag(); else if(t==="pnl")renderPnl();
  else if(t==="replay")renderReplay(); else if(t==="verify")renderVerify();
},350);
setInterval(()=>{S.dirty=true;},1500); // countdown/pulse обновления
