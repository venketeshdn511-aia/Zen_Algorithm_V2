import { useState, useEffect, useRef, useCallback, useMemo } from "react";
import {
  AreaChart, Area, LineChart, Line, BarChart, Bar,
  XAxis, YAxis, Tooltip, ResponsiveContainer, ReferenceLine
} from "recharts";

/* ═══════════════════════════════════════════════════════════════════
   STYLES
═══════════════════════════════════════════════════════════════════ */
const STYLES = `
@import url('https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@300;400;500;600;700&family=Rajdhani:wght@400;500;600;700&display=swap');

*,*::before,*::after{box-sizing:border-box;margin:0;padding:0}

:root {
  /* Surface */
  --s0:#050609; --s1:#080b12; --s2:#0b0f18; --s3:#0f141f; --s4:#131a27;
  --s5:#17202f;
  /* Border */
  --b1:#182030; --b2:#1e2b40; --b3:#253352; --b4:#2e3f60;
  /* Accent */
  --teal:#00d4b0;    --teal-lo:rgba(0,212,176,.07);   --teal-md:rgba(0,212,176,.15); --teal-hi:rgba(0,212,176,.35);
  --green:#00d97a;   --green-lo:rgba(0,217,122,.07);  --green-md:rgba(0,217,122,.18); --green-hi:rgba(0,217,122,.4);
  --red:#f02f2f;     --red-lo:rgba(240,47,47,.07);    --red-md:rgba(240,47,47,.18);   --red-hi:rgba(240,47,47,.4);
  --amber:#f5a623;   --amber-lo:rgba(245,166,35,.07); --amber-md:rgba(245,166,35,.18);
  --blue:#4d9fff;    --blue-lo:rgba(77,159,255,.07);
  --purple:#c084fc;  --purple-lo:rgba(192,132,252,.07);
  --cyan:#22d3ee;    --cyan-lo:rgba(34,211,238,.07);
  /* Text */
  --t1:#d0ddf0; --t2:#6b7fa0; --t3:#2e3c56; --t4:#182030;
  /* Fonts */
  --mono:'JetBrains Mono',monospace;
  --ui:'Rajdhani',sans-serif;
  --r:3px; --r2:6px; --r3:10px;
  --shadow:0 8px 40px rgba(0,0,0,.7);
}

html,body,#root{height:100%;background:var(--s0);overflow:hidden}
body{font-family:var(--mono);font-size:11px;line-height:1.5;color:var(--t1)}
button{cursor:pointer;font-family:var(--mono)}
::-webkit-scrollbar{width:3px;height:3px}
::-webkit-scrollbar-thumb{background:var(--b3);border-radius:2px}
::-webkit-scrollbar-track{background:transparent}

/* ── SHELL ── */
.shell{display:flex;flex-direction:column;height:100vh;overflow:hidden;position:relative}
.shell::before{
  content:'';position:fixed;inset:0;pointer-events:none;z-index:0;
  background:
    radial-gradient(ellipse 60% 30% at 50% 0%,rgba(0,212,176,.04),transparent),
    radial-gradient(ellipse 30% 50% at 100% 50%,rgba(77,159,255,.03),transparent);
}
.shell>*{position:relative;z-index:1}

/* ── TOPBAR ── */
.tb{
  height:52px;display:flex;align-items:stretch;gap:0;
  background:var(--s1);border-bottom:1px solid var(--b2);flex-shrink:0;
  position:relative;z-index:100;
}
.tb::after{
  content:'';position:absolute;bottom:-1px;left:0;width:40%;height:1px;
  background:linear-gradient(90deg,var(--teal-hi),transparent);
}

.tb-logo{
  display:flex;align-items:center;gap:10px;padding:0 18px;
  border-right:1px solid var(--b1);min-width:160px;flex-shrink:0;
}
.tb-logo-mark{
  width:26px;height:26px;border:1.5px solid var(--teal);border-radius:var(--r2);
  display:grid;place-items:center;
  box-shadow:0 0 10px var(--teal-hi),inset 0 0 8px rgba(0,212,176,.08);
}
.tb-logo-mark svg{width:14px;height:14px;fill:var(--teal)}
.tb-logo-text{font-family:var(--ui);font-size:16px;font-weight:700;letter-spacing:2px;color:var(--teal);text-transform:uppercase}
.tb-logo-sub{font-size:9px;color:var(--t2);letter-spacing:.5px;margin-top:-2px}

/* Stat cells */
.tbs{display:flex;align-items:stretch;flex:1;overflow:hidden}
.tbc{
  display:flex;flex-direction:column;justify-content:center;
  padding:0 14px;border-right:1px solid var(--b1);gap:1px;flex-shrink:0;
}
.tbc-l{font-size:9px;color:var(--t3);text-transform:uppercase;letter-spacing:.8px;font-weight:500}
.tbc-v{font-size:14px;font-weight:700;letter-spacing:-.3px;line-height:1}
.tbc-v.up{color:var(--green)}
.tbc-v.dn{color:var(--red)}
.tbc-v.warn{color:var(--amber)}
.tbc-v.teal{color:var(--teal)}
.tbc-v.dim{color:var(--t2)}
.tbc-s{font-size:9px;color:var(--t2);margin-top:1px}

/* Latency cell */
.lat-cell{display:flex;flex-direction:column;gap:2px;padding:6px 14px;border-right:1px solid var(--b1);flex-shrink:0}
.lat-bars{display:flex;align-items:flex-end;gap:1.5px;height:18px}
.lat-bar{width:3px;border-radius:1px;transition:height .4s ease,background .4s}
.lat-nums{display:flex;gap:8px;font-size:9px}
.lat-num{display:flex;flex-direction:column;gap:0}
.lat-num span:first-child{color:var(--t3);font-size:8px;text-transform:uppercase;letter-spacing:.5px}
.lat-num span:last-child{font-weight:600;color:var(--t1)}

/* Feed health */
.feed-cell{display:flex;align-items:center;gap:8px;padding:0 14px;border-right:1px solid var(--b1);flex-shrink:0}
.feed-indicator{display:flex;flex-direction:column;gap:2px}
.feed-dot{width:8px;height:8px;border-radius:50%;flex-shrink:0}
.feed-dot.live{background:var(--green);box-shadow:0 0 6px var(--green-hi);animation:pulse-green 2s infinite}
.feed-dot.stale{background:var(--amber);box-shadow:0 0 6px var(--amber-md);animation:pulse-amber 1s infinite}
.feed-dot.dead{background:var(--red);box-shadow:0 0 6px var(--red-hi);animation:pulse-red .5s infinite}
@keyframes pulse-green{0%,100%{opacity:1}50%{opacity:.5}}
@keyframes pulse-amber{0%,100%{box-shadow:0 0 6px rgba(245,166,35,.4)}50%{box-shadow:0 0 14px rgba(245,166,35,.8)}}
@keyframes pulse-red{0%,100%{box-shadow:0 0 6px rgba(240,47,47,.4)}50%{box-shadow:0 0 14px rgba(240,47,47,.9)}}

.tb-right{display:flex;align-items:center;gap:10px;padding:0 16px;margin-left:auto;flex-shrink:0}
.market-badge{
  font-family:var(--ui);font-size:12px;font-weight:600;letter-spacing:1px;
  padding:3px 10px;border-radius:var(--r);border:1px solid;text-transform:uppercase;
}
.market-badge.open{color:var(--green);border-color:rgba(0,217,122,.3);background:var(--green-lo)}
.market-badge.closed{color:var(--t2);border-color:var(--b2);background:var(--s2)}
.clock{font-size:16px;font-weight:600;letter-spacing:.5px;color:var(--t1)}
.clock-date{font-size:9px;color:var(--t2);text-align:right}

/* ── KILL SWITCH ── */
.kill-btn{
  display:flex;align-items:center;gap:7px;padding:7px 18px;
  border-radius:var(--r2);border:1px solid rgba(240,47,47,.35);
  background:rgba(240,47,47,.08);color:var(--red);
  font-family:var(--ui);font-size:13px;font-weight:700;letter-spacing:1.5px;
  text-transform:uppercase;transition:all .15s;
}
.kill-btn:hover{background:rgba(240,47,47,.16);border-color:var(--red);box-shadow:0 0 20px rgba(240,47,47,.2)}
.kill-btn.armed{
  background:var(--red);color:#fff;border-color:var(--red);
  animation:kill-pulse 1.2s ease-in-out infinite;
}
@keyframes kill-pulse{
  0%,100%{box-shadow:0 0 20px rgba(240,47,47,.5)}
  50%{box-shadow:0 0 50px rgba(240,47,47,.9),0 0 80px rgba(240,47,47,.3)}
}

/* ── EXPOSURE RIBBON ── */
.exp-ribbon{
  height:26px;display:flex;align-items:center;
  background:var(--s2);border-bottom:1px solid var(--b1);
  padding:0 16px;gap:0;flex-shrink:0;overflow:hidden;
}
.exp-ribbon.warn{background:rgba(245,166,35,.05);border-color:rgba(245,166,35,.2)}
.exp-ribbon.danger{background:rgba(240,47,47,.07);border-color:rgba(240,47,47,.25);animation:danger-flash 2s ease-in-out infinite}
@keyframes danger-flash{0%,100%{background:rgba(240,47,47,.07)}50%{background:rgba(240,47,47,.13)}}

.er-item{display:flex;align-items:center;gap:6px;padding:0 12px;border-right:1px solid var(--b1);font-size:10px}
.er-label{color:var(--t3);text-transform:uppercase;letter-spacing:.6px;font-weight:500}
.er-val{font-weight:600;color:var(--t1)}
.er-bar{width:44px;height:2px;background:var(--b2);border-radius:1px;overflow:hidden;margin-left:3px}
.er-fill{height:100%;border-radius:1px;transition:width .8s ease}
.er-fill.ok{background:var(--green)}
.er-fill.warn{background:var(--amber)}
.er-fill.danger{background:var(--red)}

.cb-group{display:flex;gap:4px;margin-left:auto}
.cb-tag{
  padding:1px 6px;border-radius:2px;font-size:9px;font-weight:700;
  border:1px solid;text-transform:uppercase;letter-spacing:.5px;
}
.cb-tag.closed{color:var(--green);border-color:rgba(0,217,122,.25);background:var(--green-lo)}
.cb-tag.open{color:var(--red);border-color:rgba(240,47,47,.3);background:var(--red-lo);animation:blink-anim 1s infinite}
.cb-tag.half_open{color:var(--amber);border-color:rgba(245,166,35,.3);background:var(--amber-lo)}
@keyframes blink-anim{0%,100%{opacity:1}50%{opacity:.4}}

.delta-badge{
  display:flex;align-items:center;gap:5px;padding:0 10px;margin-left:6px;
  border-left:1px solid var(--b1);font-size:10px;
}
.delta-dir{font-weight:700;font-family:var(--ui);font-size:12px;letter-spacing:.5px}
.delta-dir.bull{color:var(--green)} .delta-dir.bear{color:var(--red)} .delta-dir.neutral{color:var(--t2)}

/* ── MAIN LAYOUT ── */
.main{display:flex;flex:1;overflow:hidden}

/* ── SIDEBAR ── */
.sidebar{
  width:216px;flex-shrink:0;background:var(--s1);
  border-right:1px solid var(--b2);display:flex;flex-direction:column;overflow:hidden;
}
.sb-nav{border-bottom:1px solid var(--b1)}
.sb-nav-title{padding:8px 12px 4px;font-size:9px;color:var(--t4);text-transform:uppercase;letter-spacing:1px;font-weight:600}
.nav-row{
  display:flex;align-items:center;gap:8px;padding:7px 12px;
  font-size:11px;color:var(--t2);cursor:pointer;transition:all .1s;
  border-left:2px solid transparent;
}
.nav-row:hover{color:var(--t1);background:rgba(255,255,255,.02)}
.nav-row.active{color:var(--teal);border-color:var(--teal);background:var(--teal-lo)}
.nav-icon{font-size:12px;width:14px;text-align:center;opacity:.7}
.nav-row.active .nav-icon{opacity:1}
.nav-badge{margin-left:auto;font-size:9px;font-weight:700;padding:1px 5px;border-radius:2px}
.nav-badge.red{background:var(--red-lo);color:var(--red)}
.nav-badge.green{background:var(--green-lo);color:var(--green)}
.nav-badge.amber{background:var(--amber-lo);color:var(--amber)}

/* Infra block */
.infra-block{padding:10px 12px;border-bottom:1px solid var(--b1)}
.infra-title{font-size:9px;color:var(--t3);text-transform:uppercase;letter-spacing:.8px;margin-bottom:8px;display:flex;align-items:center;justify-content:space-between}
.infra-status-dot{width:5px;height:5px;border-radius:50%;background:var(--green);box-shadow:0 0 4px var(--green-hi)}
.infra-row{display:flex;align-items:center;justify-content:space-between;margin-bottom:5px}
.infra-row:last-child{margin-bottom:0}
.infra-key{font-size:9px;color:var(--t2);display:flex;align-items:center;gap:4px}
.infra-val{font-size:10px;font-weight:600;color:var(--t1)}
.infra-bar-wrap{display:flex;align-items:center;gap:4px}
.infra-bar{width:36px;height:2px;background:var(--b2);border-radius:1px;overflow:hidden}
.infra-bar-fill{height:100%;border-radius:1px;transition:width .8s}

/* Strategy summary sidebar */
.sb-stats{padding:10px 12px;display:flex;flex-direction:column;gap:8px;overflow-y:auto;flex:1}
.sb-card{background:var(--s3);border:1px solid var(--b1);border-radius:var(--r2);padding:9px}
.sb-card-title{font-size:9px;color:var(--t3);text-transform:uppercase;letter-spacing:.7px;margin-bottom:7px}
.sb-row{display:flex;justify-content:space-between;align-items:center;margin-bottom:3px}
.sb-row:last-child{margin-bottom:0}
.sb-k{font-size:10px;color:var(--t2)}
.sb-v{font-size:11px;font-weight:600;color:var(--t1)}

.exposure-block{background:var(--s3);border:1px solid var(--b1);border-radius:var(--r2);padding:9px}
.exposure-title{font-size:9px;color:var(--t3);text-transform:uppercase;letter-spacing:.7px;margin-bottom:7px}
.exp-meter{margin-bottom:6px}
.exp-meter-header{display:flex;justify-content:space-between;font-size:9px;margin-bottom:2px}
.exp-meter-key{color:var(--t2)}
.exp-meter-val{font-weight:600}
.exp-meter-bar{height:3px;background:var(--b2);border-radius:2px;overflow:hidden}
.exp-meter-fill{height:100%;border-radius:2px;transition:width .8s}

/* ── CENTER ── */
.center{flex:1;display:flex;flex-direction:column;overflow:hidden}

/* Strategy grid */
.grid-wrapper{flex:1;overflow-y:auto;padding:12px}
.strat-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(196px,1fr));gap:8px}

/* Strategy card */
.sc{
  background:var(--s2);border:1px solid var(--b1);border-radius:var(--r3);
  padding:11px;cursor:pointer;transition:all .15s;position:relative;overflow:hidden;
}
.sc::before{content:'';position:absolute;top:0;left:0;right:0;height:2px;transition:all .15s}
.sc.running::before{background:linear-gradient(90deg,var(--green),transparent)}
.sc.paused::before{background:linear-gradient(90deg,var(--amber),transparent)}
.sc.error::before{background:linear-gradient(90deg,var(--red),transparent);animation:blink-anim .8s infinite}
.sc.stopped::before{background:linear-gradient(90deg,var(--t3),transparent)}
.sc:hover{border-color:var(--b3);background:var(--s3);transform:translateY(-1px);box-shadow:0 6px 20px rgba(0,0,0,.4)}
.sc.selected{border-color:var(--teal);box-shadow:0 0 0 1px var(--teal-md),0 6px 24px rgba(0,212,176,.12)}
.sc.error{border-color:rgba(240,47,47,.2)}
.sc.error:hover{border-color:rgba(240,47,47,.4)}

.sc-head{display:flex;align-items:flex-start;justify-content:space-between;gap:4px;margin-bottom:6px}
.sc-name{font-family:var(--ui);font-size:13px;font-weight:700;color:var(--t1);line-height:1.2;letter-spacing:.2px}
.sc-pill{
  font-size:8px;font-weight:700;padding:2px 5px;border-radius:2px;
  text-transform:uppercase;letter-spacing:.5px;flex-shrink:0;display:flex;align-items:center;gap:2px;
}
.sc-pill.running{color:var(--green);background:var(--green-lo)}
.sc-pill.paused{color:var(--amber);background:var(--amber-lo)}
.sc-pill.error{color:var(--red);background:var(--red-lo);animation:blink-anim 1s infinite}
.sc-pill.stopped{color:var(--t2);background:rgba(107,127,160,.08)}
.sc-pill-dot{width:3px;height:3px;border-radius:50%;background:currentColor}

.sc-sym{font-size:9px;color:var(--t2);margin-bottom:7px;display:flex;align-items:center;gap:5px}
.type-tag{font-size:8px;padding:1px 4px;border-radius:2px;font-weight:600}
.type-tag.ce{color:var(--blue);background:var(--blue-lo)}
.type-tag.pe{color:var(--purple);background:var(--purple-lo)}
.type-tag.straddle,.type-tag.condor{color:var(--teal);background:var(--teal-lo)}
.type-tag.other{color:var(--amber);background:var(--amber-lo)}

.sc-pnl{font-size:20px;font-weight:700;letter-spacing:-.5px;line-height:1}
.sc-pnl.up{color:var(--green)} .sc-pnl.dn{color:var(--red)} .sc-pnl.zero{color:var(--t2)}
.sc-pnl-sub{font-size:9px;color:var(--t2);margin-top:1px}

.sc-metrics{display:grid;grid-template-columns:1fr 1fr;gap:3px 8px;margin-top:7px}
.sc-m{display:flex;flex-direction:column}
.sc-m-l{font-size:8px;color:var(--t3);text-transform:uppercase;letter-spacing:.5px}
.sc-m-v{font-size:10px;font-weight:600;color:var(--t2)}

.sc-chart{height:26px;margin-top:7px;opacity:.65}

.sc-btns{display:flex;gap:3px;margin-top:7px;padding-top:7px;border-top:1px solid var(--b1)}
.sc-btn{
  flex:1;padding:4px;border:1px solid var(--b2);background:transparent;
  border-radius:var(--r);color:var(--t2);font-size:9px;font-weight:600;
  letter-spacing:.5px;text-transform:uppercase;transition:all .1s;
}
.sc-btn:hover.pause-btn{border-color:var(--amber);color:var(--amber);background:var(--amber-lo)}
.sc-btn:hover.resume-btn{border-color:var(--green);color:var(--green);background:var(--green-lo)}
.sc-btn:hover.stop-btn{border-color:var(--red);color:var(--red);background:var(--red-lo)}

/* Error overlay on card */
.sc-error-badge{
  display:flex;align-items:center;gap:4px;margin-top:5px;
  padding:4px 6px;background:var(--red-lo);border-radius:var(--r);
  border:1px solid rgba(240,47,47,.15);font-size:9px;color:var(--red);
}

/* ── HEATMAP ── */
.heatmap-grid{display:grid;grid-template-columns:repeat(7,1fr);gap:4px;padding:12px}
.hm-cell{
  aspect-ratio:1.2;border-radius:var(--r2);display:flex;flex-direction:column;
  align-items:center;justify-content:center;cursor:pointer;
  transition:transform .1s;position:relative;border:1px solid transparent;
}
.hm-cell:hover{transform:scale(1.04);z-index:10;border-color:rgba(255,255,255,.15)}
.hm-id{font-size:10px;font-weight:700;opacity:.8;font-family:var(--ui)}
.hm-pnl{font-size:9px;font-weight:600;opacity:.9}
.hm-tt{
  position:absolute;bottom:calc(100% + 6px);left:50%;transform:translateX(-50%);
  background:var(--s0);border:1px solid var(--b3);border-radius:var(--r2);
  padding:7px 10px;white-space:nowrap;font-size:10px;z-index:50;
  box-shadow:var(--shadow);pointer-events:none;
}

/* ── DRAWER ── */
.drawer-bg{
  position:fixed;inset:0;background:rgba(0,0,0,.65);z-index:500;
  backdrop-filter:blur(6px);display:flex;justify-content:flex-end;
}
.drawer{
  width:580px;height:100%;background:var(--s1);border-left:1px solid var(--b2);
  display:flex;flex-direction:column;overflow:hidden;
  animation:slide-in .2s cubic-bezier(.16,1,.3,1);
}
@keyframes slide-in{from{transform:translateX(30px);opacity:0}to{transform:translateX(0);opacity:1}}
.dh{
  display:flex;align-items:center;justify-content:space-between;
  padding:14px 18px;border-bottom:1px solid var(--b2);background:var(--s2);flex-shrink:0;
}
.dh-name{font-family:var(--ui);font-size:22px;font-weight:700;letter-spacing:.3px}
.dh-close{background:transparent;border:1px solid var(--b2);color:var(--t2);
  border-radius:var(--r);padding:5px 10px;font-size:13px;transition:all .1s}
.dh-close:hover{border-color:var(--red);color:var(--red);background:var(--red-lo)}
.db{flex:1;overflow-y:auto;padding:14px 18px;display:flex;flex-direction:column;gap:12px}
.dc{background:var(--s2);border:1px solid var(--b1);border-radius:var(--r2);padding:12px}
.dc-title{font-size:9px;color:var(--t2);text-transform:uppercase;letter-spacing:.8px;margin-bottom:9px;display:flex;align-items:center;gap:6px}
.dc-grid{display:grid;grid-template-columns:repeat(3,1fr);gap:8px}
.dc-stat{display:flex;flex-direction:column;gap:2px}
.dc-stat-l{font-size:8px;color:var(--t3);text-transform:uppercase;letter-spacing:.5px}
.dc-stat-v{font-size:14px;font-weight:700}

/* Error drilldown */
.err-trace{
  background:rgba(240,47,47,.05);border:1px solid rgba(240,47,47,.15);
  border-radius:var(--r2);padding:10px;font-size:10px;color:var(--red);
  font-family:var(--mono);line-height:1.6;max-height:120px;overflow-y:auto;
  white-space:pre-wrap;
}
.err-meta{display:grid;grid-template-columns:1fr 1fr;gap:6px;margin-top:8px}
.err-m-item{background:var(--s3);border-radius:var(--r);padding:6px 8px}
.err-m-l{font-size:8px;color:var(--t3);text-transform:uppercase;letter-spacing:.5px;margin-bottom:2px}
.err-m-v{font-size:11px;font-weight:600;color:var(--t1)}

.da{
  display:flex;gap:8px;padding:12px 18px;border-top:1px solid var(--b2);
  background:var(--s2);flex-shrink:0;
}
.da-btn{
  flex:1;padding:9px;border-radius:var(--r2);font-family:var(--ui);
  font-size:12px;font-weight:700;letter-spacing:.8px;text-transform:uppercase;
  border:1px solid;transition:all .15s;
}
.da-btn.pause{color:var(--amber);border-color:rgba(245,166,35,.3);background:var(--amber-lo)}
.da-btn.pause:hover{background:var(--amber-md);box-shadow:0 0 14px rgba(245,166,35,.2)}
.da-btn.resume{color:var(--green);border-color:rgba(0,217,122,.3);background:var(--green-lo)}
.da-btn.resume:hover{background:var(--green-md);box-shadow:0 0 14px rgba(0,217,122,.2)}
.da-btn.stop{color:var(--red);border-color:rgba(240,47,47,.3);background:var(--red-lo)}
.da-btn.stop:hover{background:var(--red-md);box-shadow:0 0 14px rgba(240,47,47,.2)}

/* ── BOTTOM STRIP ── */
.strip{height:210px;flex-shrink:0;background:var(--s1);border-top:1px solid var(--b2);display:flex}
.strip-pane{flex:1;display:flex;flex-direction:column;overflow:hidden;border-right:1px solid var(--b1)}
.strip-pane:last-child{border-right:none;flex:0 0 260px}
.strip-tabs{display:flex;height:30px;background:var(--s2);border-bottom:1px solid var(--b1);flex-shrink:0}
.strip-tab{
  display:flex;align-items:center;gap:4px;padding:0 12px;
  font-size:10px;color:var(--t2);cursor:pointer;border-bottom:2px solid transparent;transition:all .1s;
}
.strip-tab:hover{color:var(--t1)}
.strip-tab.active{color:var(--teal);border-color:var(--teal)}
.strip-body{flex:1;overflow-y:auto}

/* ── TABLES ── */
.dt{width:100%;border-collapse:collapse}
.dt th{
  padding:5px 9px;text-align:left;color:var(--t3);font-size:8px;
  font-weight:600;text-transform:uppercase;letter-spacing:.6px;
  border-bottom:1px solid var(--b1);position:sticky;top:0;background:var(--s2);z-index:5;
}
.dt th.r,.dt td.r{text-align:right}
.dt td{padding:5px 9px;border-bottom:1px solid var(--b1);color:var(--t1);font-size:10px;vertical-align:middle;white-space:nowrap}
.dt tr:hover td{background:rgba(255,255,255,.015)}

/* ── PILLS / TAGS ── */
.pill{display:inline-flex;align-items:center;padding:1px 5px;border-radius:2px;font-size:8px;font-weight:700;letter-spacing:.4px;text-transform:uppercase}
.pill.buy{background:var(--green-lo);color:var(--green)}
.pill.sell{background:var(--red-lo);color:var(--red)}
.pill.filled,.pill.complete{background:var(--green-lo);color:var(--green)}
.pill.pending{background:var(--amber-lo);color:var(--amber)}
.pill.rejected{background:var(--red-lo);color:var(--red)}
.pill.cancelled{background:rgba(107,127,160,.08);color:var(--t2)}
.pill.risk_approved{background:var(--blue-lo);color:var(--blue)}
.pill.risk_rejected{background:var(--red-lo);color:var(--red)}
.pill.sent,.pill.acknowledged{background:var(--cyan-lo);color:var(--cyan)}

/* ── MODAL ── */
.modal-bg{position:fixed;inset:0;background:rgba(0,0,0,.8);z-index:800;display:flex;align-items:center;justify-content:center;backdrop-filter:blur(6px)}
.modal{background:var(--s2);border:1px solid var(--b3);border-radius:var(--r3);padding:26px;min-width:400px;max-width:500px;box-shadow:var(--shadow);animation:modal-in .18s ease}
@keyframes modal-in{from{transform:scale(.96);opacity:0}to{transform:scale(1);opacity:1}}
.modal-title{font-family:var(--ui);font-size:22px;font-weight:700;margin-bottom:10px}
.modal-title.danger{color:var(--red)}
.modal-body{color:var(--t2);font-size:11px;line-height:1.7;margin-bottom:16px}
.modal-summary{background:var(--s3);border-radius:var(--r2);padding:10px 12px;display:flex;flex-direction:column;gap:5px;margin:10px 0}
.ms-row{display:flex;justify-content:space-between;font-size:11px}
.ms-l{color:var(--t2)} .ms-v{font-weight:600}
.modal-warn{font-size:10px;color:var(--amber);padding:7px 10px;background:var(--amber-lo);border-radius:var(--r);margin-bottom:14px;border:1px solid rgba(245,166,35,.15)}
.modal-actions{display:flex;gap:8px;justify-content:flex-end}
.btn-cancel{background:transparent;border:1px solid var(--b2);color:var(--t2);border-radius:var(--r);padding:7px 16px;font-size:11px;transition:all .1s}
.btn-cancel:hover{border-color:var(--t2);color:var(--t1)}
.btn-danger{background:var(--red);border:none;color:#fff;border-radius:var(--r);padding:7px 16px;font-size:11px;font-family:var(--ui);font-weight:700;letter-spacing:.5px;transition:all .1s}
.btn-danger:hover{box-shadow:0 0 20px rgba(240,47,47,.5)}

/* ── ALERT TOASTS ── */
.alert-stack{position:fixed;bottom:14px;right:14px;z-index:900;display:flex;flex-direction:column;gap:6px;width:320px}
.alert-toast{
  background:var(--s2);border-radius:var(--r2);padding:9px 12px;
  border-left:3px solid;box-shadow:var(--shadow);
  animation:toast-in .25s cubic-bezier(.16,1,.3,1);
  display:flex;align-items:flex-start;gap:9px;
}
@keyframes toast-in{from{transform:translateX(120%);opacity:0}to{transform:translateX(0);opacity:1}}
.alert-toast.critical{border-color:var(--red)}
.alert-toast.warning{border-color:var(--amber)}
.alert-toast.info{border-color:var(--teal)}
.alert-toast.success{border-color:var(--green)}
.at-body{flex:1}
.at-title{font-size:11px;font-weight:700;margin-bottom:2px}
.at-msg{font-size:10px;color:var(--t2);line-height:1.4}
.at-time{font-size:8px;color:var(--t3);margin-top:3px}
.at-close{background:none;border:none;color:var(--t2);font-size:14px;padding:0;line-height:1;margin-left:auto;flex-shrink:0}

/* ── KBD HINTS ── */
.kbd{display:inline-block;padding:1px 4px;background:var(--s3);border:1px solid var(--b2);border-radius:2px;font-size:8px;color:var(--t2);letter-spacing:.3px}

/* ── MINI RISK PANEL ── */
.mrp{padding:8px 10px;display:flex;flex-direction:column;gap:8px;overflow-y:auto}
.mrp-meter{display:flex;flex-direction:column;gap:2px}
.mrp-header{display:flex;justify-content:space-between;font-size:9px}
.mrp-key{color:var(--t2)} .mrp-val{font-weight:600}
.mrp-bar{height:3px;background:var(--b1);border-radius:2px;overflow:hidden}
.mrp-fill{height:100%;border-radius:2px;transition:width .8s ease}
.mrp-sep{height:1px;background:var(--b1);margin:2px 0}
.mrp-title{font-size:8px;color:var(--t3);text-transform:uppercase;letter-spacing:.7px;margin-bottom:4px}
.cb-row{display:flex;justify-content:space-between;font-size:9px;margin-bottom:3px}
.cb-row-k{color:var(--t2)} .cb-row-v{font-weight:700}

/* ── ORDER FLOW ── */
.of-item{display:flex;align-items:center;gap:7px;padding:5px 9px;border-bottom:1px solid var(--b1);font-size:10px}
.of-time{color:var(--t3);font-size:9px;width:52px;flex-shrink:0}
.of-latency{font-size:9px;color:var(--t2);width:44px;text-align:right;flex-shrink:0}

/* ── LOGS ── */
.log-row{display:flex;align-items:baseline;gap:7px;padding:4px 9px;border-bottom:1px solid var(--b1);font-size:9px}
.log-time{color:var(--t3);width:52px;flex-shrink:0}
.log-level{font-weight:700;width:36px;flex-shrink:0}
.log-module{color:var(--t3);width:76px;overflow:hidden;text-overflow:ellipsis;flex-shrink:0}
.log-msg{color:var(--t2)}
.log-row.ERROR .log-level,.log-row.ERROR .log-msg{color:var(--red)}
.log-row.WARN .log-level{color:var(--amber)}
.log-row.INFO .log-level{color:var(--teal)}

/* ── EMPTY STATE ── */
.empty{display:flex;flex-direction:column;align-items:center;justify-content:center;gap:6px;height:100%;color:var(--t3);font-size:11px}
.empty-icon{font-size:24px;opacity:.25}

/* ── SHORTCUT BAR ── */
.shortcut-bar{
  position:fixed;bottom:8px;left:50%;transform:translateX(-50%);
  display:flex;gap:12px;align-items:center;
  background:rgba(8,11,18,.9);border:1px solid var(--b2);border-radius:20px;
  padding:4px 14px;backdrop-filter:blur(8px);z-index:400;font-size:9px;color:var(--t2);
}
.shortcut-bar span{display:flex;align-items:center;gap:4px}
`;

function injectStyles() {
  if (!document.getElementById("ops-css")) {
    const el = document.createElement("style");
    el.id = "ops-css";
    el.textContent = STYLES;
    document.head.appendChild(el);
  }
}

/* ═══════════════════════════════════════════════════════════════════
   API CONFIG & HELPERS
═══════════════════════════════════════════════════════════════════ */
const API_BASE = "/api/v1/observe";

const f = {
  pnl: (n) => `${n >= 0 ? "+" : ""}₹${Math.abs(n).toLocaleString("en-IN", { maximumFractionDigits: 0 })}`,
  num: (n, d = 2) => Number(n).toLocaleString("en-IN", { minimumFractionDigits: d, maximumFractionDigits: d }),
};

async function apiFetch(endpoint, method = "GET", body = null) {
  const options = {
    method,
    headers: { "Content-Type": "application/json", "X-Auth-Token": "local-dev" },
  };
  if (body) options.body = JSON.stringify(body);
  const res = await fetch(`${API_BASE}${endpoint}`, options);
  if (!res.ok) {
    let msg = res.statusText;
    try {
      const data = await res.json();
      // FastAPI/Pydantic errors often in detail.message or just detail
      msg = data.detail?.message || data.detail || msg || "Server Error";
    } catch (e) {
      msg = msg || `HTTP ${res.status}`;
    }
    throw new Error(msg);
  }
  return res.json();
}

/* ═══════════════════════════════════════════════════════════════════
   HOOKS
═══════════════════════════════════════════════════════════════════ */
function useAlerts() {
  const [alerts, set] = useState([]);
  const add = useCallback((type, title, msg) => {
    const id = Date.now();
    const time = new Date().toLocaleTimeString("en-IN", { hour12: false });
    set(p => [{ id, type, title, msg, time }, ...p.slice(0, 5)]);
    setTimeout(() => set(p => p.filter(a => a.id !== id)), 9000);
  }, []);
  const remove = useCallback((id) => set(p => p.filter(a => a.id !== id)), []);
  return { alerts, addAlert: add, removeAlert: remove };
}

function useClock() {
  const [t, setT] = useState(new Date());
  useEffect(() => { const id = setInterval(() => setT(new Date()), 1000); return () => clearInterval(id); }, []);
  return t;
}

/* ═══════════════════════════════════════════════════════════════════
   COMPONENTS
═══════════════════════════════════════════════════════════════════ */

function MiniLine({ data, up }) {
  return (
    <ResponsiveContainer width="100%" height="100%">
      <LineChart data={data}>
        <Line type="monotone" dataKey="v" stroke={up ? "var(--green)" : "var(--red)"} strokeWidth={1.2} dot={false} />
      </LineChart>
    </ResponsiveContainer>
  );
}

function LatencySparkbar({ history }) {
  const data = Array.isArray(history) ? history : [];
  const max = Math.max(...data, 1);
  return (
    <div className="lat-bars">
      {data.map((v, i) => {
        const h = Math.max(4, Math.round((v / max) * 18));
        const col = v > 80 ? "var(--red)" : v > 50 ? "var(--amber)" : "var(--green)";
        return <div key={i} className="lat-bar" style={{ height: h, background: col }} />;
      })}
    </div>
  );
}

function StratCard({ s, selected, onClick, onAction }) {
  const isUp = s.pnl >= 0;
  const t = s.type || "";
  const typeClass = t.includes("CE") ? "ce" : t.includes("PE") ? "pe" : t.includes("STRADDLE") || t.includes("CONDOR") || t.includes("BUTTERFLY") ? "straddle" : "other";
  return (
    <div className={`sc ${s.status} ${selected ? "selected" : ""}`} onClick={onClick}>
      <div className="sc-head">
        <div className="sc-name">{(s.name || "").replace(/_/g, " ")}</div>
        <div className={`sc-pill ${s.status}`}><div className="sc-pill-dot" />{s.status}</div>
      </div>
      <div className="sc-sym">
        {s.sym}
        <span className={`type-tag ${typeClass}`}>{t.replace(/_/g, " ")}</span>
        <span style={{ marginLeft: "auto", fontSize: 9, color: s.direction === "BULL" ? "var(--green)" : s.direction === "BEAR" ? "var(--red)" : "var(--t2)", fontWeight: 700 }}>
          {s.direction === "BULL" ? "▲" : s.direction === "BEAR" ? "▼" : "◆"} {s.direction}
        </span>
      </div>

      <div className={`sc-pnl ${s.pnl > 0 ? "up" : s.pnl < 0 ? "dn" : "zero"}`}>{f.pnl(s.pnl)}</div>
      <div className="sc-pnl-sub" style={{ color: s.pnl > 0 ? "var(--green-lo)" : s.pnl < 0 ? "var(--red-lo)" : "var(--t3)" }}>
        Δ {s.delta > 0 ? "+" : ""}{s.delta} · Risk {s.riskPct}%
      </div>

      {s.status === "error" && s.errorMsg && (
        <div className="sc-error-badge">
          ⚠ {s.errorMsg}
        </div>
      )}

      <div className="sc-metrics">
        <div className="sc-m"><div className="sc-m-l">Win%</div><div className="sc-m-v">{s.winRate}%</div></div>
        <div className="sc-m"><div className="sc-m-l">Trades</div><div className="sc-m-v">{s.trades}</div></div>
        <div className="sc-m"><div className="sc-m-l">Open Qty</div><div className="sc-m-v">{s.openQty || "—"}</div></div>
        <div className="sc-m"><div className="sc-m-l">Last</div><div className="sc-m-v">{s.lastTrade}</div></div>
      </div>
      <div className="sc-chart"><MiniLine data={s.equity || []} up={isUp} /></div>
      <div className="sc-btns" onClick={e => e.stopPropagation()}>
        {s.status === "running" && <button className="sc-btn pause-btn" onClick={() => onAction(s, "pause")}>Pause</button>}
        {(s.status === "paused" || s.status === "error" || s.status === "stopped") && (
          <button className="sc-btn resume-btn" onClick={() => onAction(s, s.status === "stopped" ? "start" : "resume")}>
            {s.status === "stopped" ? "Start" : "Resume"}
          </button>
        )}
        <button className="sc-btn stop-btn" onClick={() => onAction(s, "stop")}>Stop</button>
      </div>
    </div>
  );
}

function StratDrawer({ s, onClose, onAction }) {
  if (!s) return null;
  const isUp = s.pnl >= 0;
  const pct = s.drawdown / s.maxDD * 100;
  return (
    <div className="drawer-bg" onClick={onClose}>
      <div className="drawer" onClick={e => e.stopPropagation()}>
        <div className="dh">
          <div>
            <div className="dh-name">{(s.name || "").replace(/_/g, " ")}</div>
            <div style={{ fontSize: 10, color: "var(--t2)", marginTop: 2 }}>
              {s.sym} · {(s.type || "").replace(/_/g, " ")} · Signal:&nbsp;
              <span style={{ color: s.signal === "LONG" ? "var(--green)" : s.signal === "SHORT" ? "var(--red)" : "var(--teal)", fontWeight: 700 }}>{s.signal}</span>
              &nbsp;· Δ <span style={{ color: s.delta > 0 ? "var(--green)" : "var(--red)", fontWeight: 700 }}>{s.delta > 0 ? "+" : ""}{s.delta}</span>
            </div>
          </div>
          <button className="dh-close" onClick={onClose}>✕</button>
        </div>

        <div className="db">
          {/* ERROR DRILLDOWN */}
          {s.status === "error" && (
            <div className="dc" style={{ borderColor: "rgba(240,47,47,.2)" }}>
              <div className="dc-title" style={{ color: "var(--red)" }}>
                <span>⚠ Error Drilldown</span>
                <span style={{ color: "var(--t2)", fontSize: 9 }}>restarts: {s.restartCount}</span>
              </div>
              <div className="err-trace">{s.errorTrace}</div>
              <div className="err-meta">
                <div className="err-m-item">
                  <div className="err-m-l">Last Exception</div>
                  <div className="err-m-v" style={{ color: "var(--red)", fontSize: 10 }}>{s.errorMsg}</div>
                </div>
                <div className="err-m-item">
                  <div className="err-m-l">Last Good Trade</div>
                  <div className="err-m-v">{s.lastGoodTrade}</div>
                </div>
                <div className="err-m-item">
                  <div className="err-m-l">Restart Attempts</div>
                  <div className="err-m-v" style={{ color: s.restartCount >= 3 ? "var(--red)" : "var(--t1)" }}>{s.restartCount}/5</div>
                </div>
                <div className="err-m-item">
                  <div className="err-m-l">Auto-Restart</div>
                  <div className="err-m-v" style={{ color: s.restartCount < 5 ? "var(--teal)" : "var(--red)" }}>{s.restartCount < 5 ? "ENABLED" : "DISABLED"}</div>
                </div>
              </div>
            </div>
          )}

          {/* PERFORMANCE */}
          <div className="dc">
            <div className="dc-title">Performance</div>
            <div className="dc-grid">
              {[
                ["Day P&L", f.pnl(s.pnl), isUp ? "var(--green)" : "var(--red)"],
                ["Win Rate", `${s.winRate}%`, "var(--t1)"],
                ["Trades", s.trades, "var(--t1)"],
                ["Drawdown", `${s.drawdown}%`, pct > 80 ? "var(--red)" : pct > 60 ? "var(--amber)" : "var(--t1)"],
                ["Max DD Limit", `${s.maxDD}%`, "var(--t1)"],
                ["Allocation", `₹${(s.alloc / 100000).toFixed(1)}L`, "var(--teal)"],
              ].map(([l, v, c]) => (
                <div key={l} className="dc-stat">
                  <div className="dc-stat-l">{l}</div>
                  <div className="dc-stat-v" style={{ color: c }}>{v}</div>
                </div>
              ))}
            </div>
          </div>

          {/* EQUITY CURVE */}
          <div className="dc">
            <div className="dc-title">Equity Curve (Today)</div>
            <div style={{ height: 90 }}>
              <ResponsiveContainer width="100%" height="100%">
                <AreaChart data={s.equity || []}>
                  <defs>
                    <linearGradient id={`eg${s.id}`} x1="0" y1="0" x2="0" y2="1">
                      <stop offset="5%" stopColor={isUp ? "#00d97a" : "#f02f2f"} stopOpacity={.3} />
                      <stop offset="95%" stopColor={isUp ? "#00d97a" : "#f02f2f"} stopOpacity={0} />
                    </linearGradient>
                  </defs>
                  <XAxis dataKey="i" hide /><YAxis hide domain={["auto", "auto"]} />
                  <ReferenceLine y={0} stroke="var(--b2)" strokeDasharray="3 3" />
                  <Tooltip contentStyle={{ background: "var(--s1)", border: "1px solid var(--b2)", borderRadius: 4, fontSize: 10 }}
                    formatter={(v) => [f.pnl(v), "P&L"]} />
                  <Area type="monotone" dataKey="v" stroke={isUp ? "var(--green)" : "var(--red)"}
                    fill={`url(#eg${s.id})`} strokeWidth={1.5} dot={false} />
                </AreaChart>
              </ResponsiveContainer>
            </div>
          </div>

          {/* RISK EXPOSURE */}
          <div className="dc">
            <div className="dc-title">Risk Exposure</div>
            {[
              { label: "Daily Loss Budget", used: Math.abs(Math.min(0, s.pnl || 0)), max: 8000 },
              { label: "Drawdown Used", used: s.drawdown || 0, max: s.maxDD || 1, suffix: "%" },
              { label: "Capital at Risk", used: s.riskPct || 0, max: 10, suffix: "%" },
            ].map(({ label, used, max, suffix = "" }) => {
              const pct = Math.min((used / max) * 100, 100);
              return (
                <div key={label} style={{ marginBottom: 7 }}>
                  <div style={{ display: "flex", justifyContent: "space-between", fontSize: 9, marginBottom: 2 }}>
                    <span style={{ color: "var(--t2)" }}>{label}</span>
                    <span style={{ fontWeight: 700, color: pct >= 80 ? "var(--red)" : pct >= 60 ? "var(--amber)" : "var(--t1)" }}>
                      {suffix ? `${used.toFixed(1)}${suffix}` : `₹${Math.round(used).toLocaleString("en-IN")}`} / {suffix ? `${max}${suffix}` : `₹${max.toLocaleString("en-IN")}`}
                    </span>
                  </div>
                  <div style={{ height: 4, background: "var(--b1)", borderRadius: 2, overflow: "hidden" }}>
                    <div style={{ width: `${pct}%`, height: "100%", borderRadius: 2, background: pct >= 80 ? "var(--red)" : pct >= 60 ? "var(--amber)" : "var(--green)", transition: "width .6s ease" }} />
                  </div>
                </div>
              );
            })}
          </div>

          {/* POSITION */}
          <div className="dc">
            <div className="dc-title">Current Position</div>
            {s.openQty > 0 ? (
              <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "5px 14px" }}>
                {[["Open Qty", s.openQty], ["Avg Entry", `₹${f.num(s.avgEntry)}`], ["LTP", `₹${f.num(s.ltp)}`], ["MTM", f.pnl((s.ltp - s.avgEntry) * s.openQty)], ["Direction", s.direction], ["Delta", s.delta > 0 ? `+${s.delta}` : s.delta]].map(([l, v]) => (
                  <div key={l}>
                    <div style={{ fontSize: 8, color: "var(--t3)", textTransform: "uppercase", letterSpacing: ".5px" }}>{l}</div>
                    <div style={{ fontSize: 13, fontWeight: 700, color: "var(--t1)" }}>{v}</div>
                  </div>
                ))}
              </div>
            ) : <div style={{ color: "var(--t3)", fontSize: 11 }}>No open position — FLAT</div>}
          </div>
        </div>

        <div className="da">
          {s.status === "running" && <button className="da-btn pause" onClick={() => { onAction(s, "pause"); onClose() }}>⏸ Pause</button>}
          {(s.status === "paused" || s.status === "error" || s.status === "stopped") && (
            <button className="da-btn resume" onClick={() => { onAction(s, s.status === "stopped" ? "start" : "resume"); onClose() }}>
              {s.status === "stopped" ? "▶ Start" : "▶ Resume"}
            </button>
          )}
          <button className="da-btn stop" onClick={() => { onAction(s, "stop"); onClose() }}>⛔ Stop</button>
        </div>
      </div>
    </div>
  );
}

function PositionsPanel({ positions }) {
  const total = positions.reduce((s, p) => s + p.pnl, 0);
  const totalLots = positions.reduce((s, p) => s + p.lots, 0);
  const maxLoss = positions.reduce((s, p) => s + (p.side === "BUY" ? p.qty * (p.entry - p.stop) : Math.abs(p.qty) * (p.stop - p.entry)), 0);
  return (
    <div style={{ display: "flex", flexDirection: "column", flex: 1, overflow: "hidden" }}>
      <table className="dt">
        <thead><tr>
          <th>Symbol</th><th>Strategy</th><th>Side</th>
          <th className="r">Qty</th><th className="r">Entry</th>
          <th className="r">LTP</th><th className="r">MTM</th>
          <th className="r">Stop</th><th className="r">Target</th>
        </tr></thead>
        <tbody>
          {positions.map(p => {
            const qty = p.qty ?? p.net_qty ?? 0;
            const pnl = p.pnl ?? p.unrealized ?? 0;
            const disp = p.disp ?? p.symbol ?? "Unknown";
            const strat = p.strat ?? "Bot";
            return (
              <tr key={p.id || p.symbol}>
                <td style={{ fontWeight: 600 }}>{disp}</td>
                <td style={{ color: "var(--t2)", fontSize: 9 }}>{strat}</td>
                <td><span className={`pill ${p.side?.toLowerCase()}`}>{p.side}</span></td>
                <td className="r" style={{ color: qty > 0 ? "var(--green)" : "var(--red)", fontWeight: 700 }}>{qty > 0 ? "+" : ""}{qty}</td>
                <td className="r">₹{f.num(p.entry || p.avg_price || 0)}</td>
                <td className="r" style={{ fontWeight: 600 }}>₹{f.num(p.ltp || 0)}</td>
                <td className="r" style={{ color: pnl >= 0 ? "var(--green)" : "var(--red)", fontWeight: 700 }}>{f.pnl(pnl)}</td>
                <td className="r" style={{ color: "var(--red)" }}>₹{f.num(p.stop || 0)}</td>
                <td className="r" style={{ color: "var(--green)" }}>₹{f.num(p.target || 0)}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
      <div style={{ padding: "4px 9px", borderTop: "1px solid var(--b1)", display: "flex", gap: 20, fontSize: 10, flexShrink: 0 }}>
        <span style={{ color: "var(--t2)" }}>MTM: <span style={{ fontWeight: 700, color: total >= 0 ? "var(--green)" : "var(--red)" }}>{f.pnl(total)}</span></span>
        <span style={{ color: "var(--t2)" }}>Lots: <span style={{ fontWeight: 700, color: "var(--t1)" }}>{totalLots}</span></span>
        <span style={{ color: "var(--t2)" }}>Worst Loss (SL fail): <span style={{ fontWeight: 700, color: "var(--red)" }}>₹{Math.abs(maxLoss).toLocaleString("en-IN", { maximumFractionDigits: 0 })}</span></span>
      </div>
    </div>
  );
}

function OrderFlowPanel({ events }) {
  return (
    <div style={{ overflow: "auto", flex: 1 }}>
      {events.map(e => (
        <div key={e.id} className="of-item">
          <span className="of-time">{e.time}</span>
          <span className={`pill ${e.status}`}>{e.event}</span>
          <span style={{ fontWeight: 600, fontSize: 10, marginLeft: 4 }}>{e.sym}</span>
          {e.side && <span className={`pill ${e.side.toLowerCase()}`}>{e.side}</span>}
          <span style={{ color: "var(--t2)", marginLeft: 2 }}>{e.qty}@₹{f.num(e.price)}</span>
          <span style={{ color: "var(--t3)", fontSize: 9, marginLeft: "auto", marginRight: 4 }}>{e.strat}</span>
          {e.latency && <span className="of-latency" style={{ color: e.latency > 80 ? "var(--red)" : e.latency > 50 ? "var(--amber)" : "var(--green)" }}>{e.latency}ms</span>}
          {e.reason && <span style={{ color: "var(--red)", fontSize: 9 }}>⚠ {e.reason}</span>}
        </div>
      ))}
    </div>
  );
}

function LogsPanel({ logs }) {
  return (
    <div style={{ overflow: "auto", flex: 1 }}>
      {logs.map(l => (
        <div key={l.id} className={`log-row ${l.level}`}>
          <span className="log-time">{l.time}</span>
          <span className="log-level">{l.level}</span>
          <span className="log-module">{l.module}</span>
          <span className="log-msg">{l.msg}</span>
        </div>
      ))}
    </div>
  );
}

function MiniRiskPanel({ strategies, positions, marginPct, dailyLossPct, cbStates }) {
  const runningCount = strategies.filter(s => s.status === "running").length;
  const openPos = positions.length;
  const totalLots = positions.reduce((s, p) => s + p.lots, 0);
  const totalMarginAtRisk = totalLots * 25000;
  const maxTheoLoss = positions.reduce((s, p) => s + Math.abs(p.qty * (p.side === "BUY" ? p.entry - p.stop : p.stop - p.entry)), 0);
  const netDelta = strategies.filter(s => s.status === "running").reduce((s, x) => s + x.delta, 0);
  const cbMap = { "CLOSED": "var(--green)", "HALF_OPEN": "var(--amber)", "OPEN": "var(--red)" };
  return (
    <div className="mrp">
      {[
        { key: "Daily Loss", used: dailyLossPct, max: 100, suffix: "%" },
        { key: "Margin Used", used: marginPct, max: 100, suffix: "%" },
        { key: "Open Positions", used: openPos, max: 10, suffix: "" },
        { key: "Active Strategies", used: runningCount, max: 28, suffix: "" },
      ].map(({ key, used, max, suffix }) => {
        const pct = Math.min((used / max) * 100, 100);
        return (
          <div key={key} className="mrp-meter">
            <div className="mrp-header">
              <span className="mrp-key">{key}</span>
              <span className="mrp-val" style={{ color: pct >= 80 ? "var(--red)" : pct >= 65 ? "var(--amber)" : "var(--t1)" }}>{suffix ? `${typeof used === "number" ? used.toFixed(1) : used}${suffix}` : `${used}/${max}`}</span>
            </div>
            <div className="mrp-bar">
              <div className="mrp-fill" style={{ width: `${pct}%`, background: pct >= 80 ? "var(--red)" : pct >= 65 ? "var(--amber)" : "var(--green)" }} />
            </div>
          </div>
        );
      })}
      <div className="mrp-sep" />
      <div className="mrp-title">Global Exposure</div>
      {[
        ["Net Open Lots", `${totalLots}`],
        ["Net Delta", netDelta.toFixed(2), netDelta > 0.5 ? "var(--green)" : netDelta < -0.5 ? "var(--red)" : "var(--t2)"],
        ["Margin at Risk", `₹${(totalMarginAtRisk / 1000).toFixed(0)}k`],
        ["Max Theo Loss", `₹${maxTheoLoss.toLocaleString("en-IN", { maximumFractionDigits: 0 })}`, maxTheoLoss > 20000 ? "var(--red)" : "var(--t1)"],
      ].map(([k, v, c]) => (
        <div key={k} className="cb-row">
          <span className="cb-row-k">{k}</span>
          <span className="cb-row-v" style={{ color: c || "var(--t1)" }}>{v}</span>
        </div>
      ))}
      <div className="mrp-sep" />
      <div className="mrp-title">Circuit Breakers</div>
      {cbStates.map(cb => (
        <div key={cb.service} className="cb-row">
          <span className="cb-row-k">{cb.service}</span>
          <span className="cb-row-v" style={{ color: cbMap[cb.state] || "var(--t2)" }}>{cb.state}</span>
        </div>
      ))}
    </div>
  );
}

function HeatmapView({ strategies, onSelect }) {
  const [hovered, setHovered] = useState(null);
  const maxAbs = Math.max(...strategies.map(s => Math.abs(s.pnl)), 1);
  return (
    <div>
      <div style={{ padding: "10px 12px 4px", fontSize: 10, color: "var(--t2)", display: "flex", alignItems: "center", gap: 12 }}>
        <span>P&L Heatmap — {strategies.length} strategies</span>
        <div style={{ display: "flex", gap: 10, marginLeft: "auto" }}>
          {[["Loss", "var(--red)"], ["Flat", "var(--s5)"], ["Profit", "var(--green)"]].map(([l, c]) => (
            <div key={l} style={{ display: "flex", alignItems: "center", gap: 4, fontSize: 9 }}>
              <div style={{ width: 10, height: 10, borderRadius: 2, background: c }} />{l}
            </div>
          ))}
        </div>
      </div>
      <div className="heatmap-grid">
        {strategies.map(s => {
          const intensity = Math.min(Math.abs(s.pnl) / maxAbs, 1);
          const bg = s.pnl > 0 ? `rgba(0,217,122,${intensity * .65 + .1})` : s.pnl < 0 ? `rgba(240,47,47,${intensity * .65 + .1})` : "var(--s4)";
          return (
            <div key={s.id} className="hm-cell" style={{ background: bg }}
              onMouseEnter={() => setHovered(s)} onMouseLeave={() => setHovered(null)}
              onClick={() => onSelect(s)}>
              <div className="hm-id">{s.id}</div>
              <div className="hm-pnl" style={{ color: intensity > .5 ? "#fff" : "var(--t1)" }}>{f.pnl(s.pnl)}</div>
              {hovered === s && (
                <div className="hm-tt">
                  <div style={{ fontWeight: 700, marginBottom: 2 }}>{s.name.replace(/_/g, " ")}</div>
                  <div style={{ color: s.pnl >= 0 ? "var(--green)" : "var(--red)" }}>{f.pnl(s.pnl)}</div>
                  <div style={{ color: "var(--t2)", fontSize: 9 }}>{s.status.toUpperCase()} · {s.direction}</div>
                  <div style={{ color: "var(--t3)", fontSize: 9 }}>Click to open</div>
                </div>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}

function InfraBlock({ infra }) {
  const cpuPct = infra?.cpu?.usage_pct ?? 0;
  const memPct = infra?.memory?.usage_pct ?? 0;
  const pool = infra?.database?.pool || { checked_out: 0, size: 0 };
  const redisMem = infra?.redis?.memory_mb ?? 0;

  const items = [
    { key: "CPU", val: `${cpuPct}%`, pct: cpuPct },
    { key: "Memory", val: `${memPct}%`, pct: memPct },
    { key: "DB Pool", val: `${pool.checked_out}/${pool.size}`, pct: (pool.checked_out / (pool.size || 1)) * 100 },
    { key: "Redis", val: `${redisMem}MB`, pct: Math.min(redisMem / 2, 100) },
  ];
  return (
    <div className="infra-block">
      <div className="infra-title">
        Infrastructure
        <div className="infra-status-dot" />
      </div>
      {items.map(({ key, val, pct }) => (
        <div key={key} className="infra-row">
          <span className="infra-key">{key}</span>
          <div className="infra-bar-wrap">
            <div className="infra-bar">
              <div className="infra-bar-fill" style={{ width: `${Math.min(pct, 100)}%`, background: pct >= 80 ? "var(--red)" : pct >= 60 ? "var(--amber)" : "var(--teal)" }} />
            </div>
            <span className="infra-val" style={{ color: pct >= 80 ? "var(--red)" : pct >= 60 ? "var(--amber)" : "var(--t1)" }}>{val}</span>
          </div>
        </div>
      ))}
      <div className="infra-row" style={{ marginTop: 4 }}>
        <span className="infra-key">Uptime</span>
        <span className="infra-val" style={{ color: "var(--teal)" }}>{infra?.process?.uptime_human || "0s"}</span>
      </div>
      <div className="infra-row">
        <span className="infra-key">Recon</span>
        <span className="infra-val" style={{ color: "var(--green)" }}>{infra?.recon_last || "—"}</span>
      </div>
    </div>
  );
}

/* ═══════════════════════════════════════════════════════════════════
   MAIN APP
═══════════════════════════════════════════════════════════════════ */
export default function App() {
  injectStyles();

  // ── STATE ──
  const [telemetry, setTelemetry] = useState(null);
  const [strategies, setStrategies] = useState([]);
  const [exposure, setExposure] = useState(null);
  const [infra, setInfra] = useState(null);
  const [orders, setOrders] = useState([]);
  const [logs, setLogs] = useState([]);

  const [selected, setSelected] = useState(null);
  const [view, setView] = useState("strategies");
  const [bottomTab, setBottomTab] = useState("positions");
  const [killModal, setKillModal] = useState(false);
  const [actionModal, setActionModal] = useState(null);
  const { alerts, addAlert, removeAlert } = useAlerts();
  const clock = useClock();

  // ── DATA FETCHING ──
  const fetchData = useCallback(async () => {
    try {
      const [tel, strats, exp, inf, ords, lg] = await Promise.all([
        apiFetch("/telemetry"),
        apiFetch("/strategies"),
        apiFetch("/exposure"),
        apiFetch("/infra"),
        apiFetch("/orders"),
        apiFetch("/logs")
      ]);
      setTelemetry(tel);
      setStrategies(strats.strategies);
      setExposure(exp);
      setInfra(inf);
      setOrders(ords.orders);
      setLogs(lg.logs);
    } catch (err) {
      console.error("Fetch Error:", err);
      // addAlert("error", "API Error", "Could not reach backend services.");
    }
  }, []);

  useEffect(() => {
    fetchData();
    const id = setInterval(fetchData, 2000);
    return () => clearInterval(id);
  }, [fetchData]);

  // ── KEYBOARD SHORTCUTS ──
  useEffect(() => {
    const handler = (e) => {
      if (e.target.tagName === "INPUT" || e.target.tagName === "TEXTAREA") return;
      if (e.key === "Escape") { setSelected(null); setKillModal(false); setActionModal(null); return; }
      if (e.shiftKey && e.key === "K") { e.preventDefault(); setKillModal(true); return; }
      if (e.shiftKey && e.key === "P") { e.preventDefault(); pauseAll(); return; }
      if (e.shiftKey && e.key === "R") { e.preventDefault(); resumeAll(); return; }
      if (e.shiftKey && e.key === "H") { e.preventDefault(); setView(v => v === "heatmap" ? "strategies" : "heatmap"); return; }
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [telemetry]);

  // Derived / Helper vars
  const isKilled = telemetry?.session?.is_killed;
  const totalPnl = telemetry?.session?.day_pnl || 0;
  const runningCount = strategies.filter(s => s.status === "running").length;
  const errorCount = strategies.filter(s => s.status === "error").length;
  const pausedCount = strategies.filter(s => s.status === "paused").length;
  const totalAlloc = strategies.reduce((a, s) => a + (s.alloc || 0), 0) || 1;
  const marginUsed = telemetry?.margin?.used || 0;
  const marginTotal = telemetry?.margin?.total || 1;
  const marginPct = telemetry?.margin?.pct || 0;
  const dailyLossPct = telemetry?.session?.daily_loss_pct || 0;
  const riskLevel = marginPct >= 85 || dailyLossPct >= 80 ? "danger" : marginPct >= 70 || dailyLossPct >= 55 ? "warn" : "ok";
  const feedAge = telemetry?.feed?.age_seconds || 0;
  const feedStatus = telemetry?.feed?.status || "unknown";
  const isMarketOpen = () => { const t = clock.getHours() * 60 + clock.getMinutes(); return t >= 555 && t <= 930 };
  const cbStates = telemetry?.circuit_breakers || [];
  const positions = (exposure?.positions || []).map(p => ({ ...p, lots: p.lots ?? (Math.abs(p.net_qty || 0) / 50) }));
  const netDelta = telemetry?.delta || 0;
  const netDir = netDelta > 0 ? "BULL" : netDelta < 0 ? "BEAR" : "NEUTRAL";

  const latency = telemetry?.latency || { avg_ms: 0, p95_ms: 0, last_ms: 0, history: [] };
  const infraData = infra || {
    cpu: { usage_pct: 0 },
    memory: { usage_pct: 0 },
    process: { uptime_human: "0s" },
    database: { pool: { checked_out: 0, size: 0 } },
    redis: { memory_mb: 0 },
    recon_last: "—",
    recon_status: "unknown",
    ws_heartbeat: 0,
    last_tick: 0
  };

  async function pauseAll() {
    try {
      await apiFetch("/strategies/pause-all", "POST");
      addAlert("warning", "All Strategies Paused", "Pause-all intent sent to backend.");
    } catch (e) { addAlert("error", "Pause All Failed", e.message); }
  }

  async function resumeAll() {
    if (isKilled) { addAlert("critical", "Cannot Resume", "Kill switch is active. Deactivate first."); return; }
    addAlert("info", "Resuming Strategies", "Please resume strategies individually or via bulk control (if implemented).");
  }

  function handleStratAction(s, action) {
    if (action === "pause" || action === "stop") setActionModal({ strat: s, action });
    else applyAction(s, action);
  }

  async function applyAction(s, action) {
    try {
      const endpoint = `/strategies/${s.name}/${action}`;
      const body = action === "stop" ? { strategy_name: s.name, confirm: true } : null;
      const res = await apiFetch(endpoint, "POST", body);
      if (res.success === false) {
        addAlert("warning", "Action Pending", res.message || "Intent queued but not yet confirmed.");
      } else {
        addAlert("success", `Strategy ${action}`, res.message || `Action successful.`);
      }
    } catch (e) {
      addAlert("error", "Action Failed", e.message);
    }
  }

  async function activateKill() {
    try {
      await apiFetch("/strategies/kill", "POST");
      addAlert("critical", "⛔ Kill Switch Activated", "All strategies halting.");
    } catch (e) { addAlert("error", "Kill Failed", e.message); }
  }

  async function deactivateKill() {
    try {
      await apiFetch("/strategies/unkill", "POST");
      addAlert("success", "✅ Kill Switch Deactivated", "Strategies can now be resumed.");
    } catch (e) { addAlert("error", "Unkill Failed", e.message); }
  }

  const navItems = [
    { id: "strategies", label: "Strategy Grid", icon: "⬡", badge: { val: runningCount, type: "green" } },
    { id: "heatmap", label: "P&L Heatmap", icon: "⊞", badge: null },
    { id: "alerts", label: "Alerts", icon: "◈", badge: alerts.length > 0 ? { val: alerts.length, type: "red" } : null },
  ];

  return (
    <div className="shell">
      {/* ── TOPBAR ── */}
      <header className="tb">
        <div className="tb-logo">
          <div className="tb-logo-mark">
            <svg viewBox="0 0 14 14"><polygon points="7,1 13,13 1,13" /></svg>
          </div>
          <div>
            <div className="tb-logo-text">TradeDeck</div>
            <div className="tb-logo-sub">OPS · COMMAND</div>
          </div>
        </div>

        <div className="tbs">
          {/* Capital */}
          <div className="tbc">
            <div className="tbc-l">Net P&L</div>
            <div className={`tbc-v ${totalPnl >= 0 ? "up" : "dn"}`}>{f.pnl(totalPnl)}</div>
            <div className="tbc-s">{((totalPnl / totalAlloc) * 100).toFixed(2)}% on ₹{(totalAlloc / 100000).toFixed(1)}L</div>
          </div>

          {/* Strategies */}
          <div className="tbc">
            <div className="tbc-l">Strategies</div>
            <div className="tbc-v teal">{runningCount}/{strategies.length}</div>
            <div className="tbc-s" style={{ color: errorCount > 0 ? "var(--red)" : "var(--t2)" }}>
              {pausedCount} paused · {errorCount > 0 ? <span style={{ color: "var(--red)" }}>{errorCount} ERR</span> : `${errorCount} err`}
            </div>
          </div>

          <div className="tbc">
            <div className="tbc-l">Margin</div>
            <div className={`tbc-v ${marginPct >= 80 ? "warn" : ""}`}>{marginPct}%</div>
            <div className="tbc-s">₹{(marginUsed / 1000).toFixed(0)}k / ₹{(marginTotal / 1000).toFixed(0)}k</div>
          </div>

          {/* Execution Latency */}
          <div className="lat-cell">
            <div className="tbc-l" style={{ fontSize: 9, color: "var(--t3)", textTransform: "uppercase", letterSpacing: ".8px", marginBottom: 2 }}>Exec Latency</div>
            <LatencySparkbar history={latency.history} />
            <div className="lat-nums">
              <div className="lat-num">
                <span>AVG</span>
                <span style={{ color: latency.avg_ms > 80 ? "var(--red)" : latency.avg_ms > 50 ? "var(--amber)" : "var(--green)" }}>{latency.avg_ms}ms</span>
              </div>
              <div className="lat-num">
                <span>P95</span>
                <span style={{ color: latency.p95_ms > 100 ? "var(--red)" : "var(--t2)" }}>{latency.p95_ms}ms</span>
              </div>
              <div className="lat-num">
                <span>LAST</span>
                <span style={{ color: latency.last_ms > 80 ? "var(--red)" : latency.last_ms > 50 ? "var(--amber)" : "var(--green)" }}>{latency.last_ms}ms</span>
              </div>
            </div>
          </div>

          {/* Feed Health */}
          <div className="feed-cell">
            <div className={`feed-dot ${feedStatus}`} />
            <div className="feed-indicator">
              <div style={{ fontSize: 9, color: "var(--t3)", textTransform: "uppercase", letterSpacing: ".7px", marginBottom: 2 }}>Feed</div>
              <div style={{ fontSize: 11, fontWeight: 700, color: feedStatus === "live" ? "var(--green)" : feedStatus === "stale" ? "var(--amber)" : "var(--red)" }}>
                {feedStatus === "live" ? "LIVE" : feedStatus === "stale" ? "STALE" : "DEAD"}
              </div>
              <div style={{ fontSize: 9, color: feedAge > 2 ? "var(--red)" : feedAge > .8 ? "var(--amber)" : "var(--t2)" }}>
                {feedAge}s ago
              </div>
            </div>
          </div>

          {/* Positions */}
          <div className="tbc">
            <div className="tbc-l">Positions</div>
            <div className="tbc-v">{positions.length}</div>
            <div className="tbc-s">{positions.reduce((s, p) => s + p.lots, 0)} lots open</div>
          </div>
        </div>

        <div className="tb-right">
          <div className={`market-badge ${isMarketOpen() ? "open" : "closed"}`}>
            {isMarketOpen() ? "● LIVE" : "○ CLOSED"}
          </div>
          <div>
            <div className="clock">{clock.toLocaleTimeString("en-IN", { hour12: false })}</div>
            <div className="clock-date">{clock.toLocaleDateString("en-IN", { day: "2-digit", month: "short", year: "numeric" })}</div>
          </div>
          <button className={`kill-btn ${isKilled ? "armed" : ""}`} onClick={() => isKilled ? deactivateKill() : setKillModal(true)}>
            ⛔ {isKilled ? "HALTED" : "KILL ALL"}
          </button>
        </div>
      </header>

      {/* ── EXPOSURE RIBBON ── */}
      <div className={`exp-ribbon ${riskLevel}`}>
        {[
          { label: "Daily Loss", val: `${dailyLossPct}% of ₹50k`, pct: Number(dailyLossPct) },
          { label: "Margin", val: `${marginPct}% / 80%`, pct: marginPct },
          { label: "Open Pos", val: `${positions.length}/10`, pct: (positions.length / 10) * 100 },
          { label: "Recon", val: `${infraData.recon_status} · ${infraData.recon_last}`, pct: null, color: "var(--green)" },
          { label: "WS Heartbeat", val: `${infraData.ws_heartbeat}s`, pct: null, color: infraData.ws_heartbeat < 2 ? "var(--green)" : "var(--red)" },
          { label: "Last Tick", val: `${feedAge}s`, pct: null, color: feedAge < 1 ? "var(--green)" : feedAge < 2 ? "var(--amber)" : "var(--red)" },
        ].map(({ label, val, pct, color }) => (
          <div key={label} className="er-item">
            <span className="er-label">{label}</span>
            <span className="er-val" style={color ? { color } : {}}>{val}</span>
            {pct !== null && (
              <div className="er-bar">
                <div className={`er-fill ${pct >= 85 ? "danger" : pct >= 65 ? "warn" : "ok"}`} style={{ width: `${Math.min(pct, 100)}%` }} />
              </div>
            )}
          </div>
        ))}
        <div className="cb-group">
          {cbStates.map(cb => (
            <div key={cb.service} className={`cb-tag ${cb.state.toLowerCase()}`}>{cb.service}</div>
          ))}
        </div>
        <div className="delta-badge">
          <span style={{ fontSize: 9, color: "var(--t2)" }}>NET DELTA</span>
          <span className={`delta-dir ${netDir.toLowerCase()}`}>{netDir === "BULL" ? "▲" : netDir === "BEAR" ? "▼" : "◆"} {netDelta.toFixed(2)}</span>
        </div>
      </div>

      {/* ── MAIN ── */}
      <div className="main">
        {/* SIDEBAR */}
        <div className="sidebar">
          <div className="sb-nav">
            <div className="sb-nav-title">Views</div>
            {navItems.map(n => (
              <div key={n.id} className={`nav-row ${view === n.id ? "active" : ""}`} onClick={() => setView(n.id)}>
                <span className="nav-icon">{n.icon}</span>
                {n.label}
                {n.badge && <span className={`nav-badge ${n.badge.type}`}>{n.badge.val}</span>}
              </div>
            ))}
          </div>

          <InfraBlock infra={infraData} />

          <div className="sb-stats">
            <div className="sb-card">
              <div className="sb-card-title">Session</div>
              {[
                ["Running", runningCount, "var(--green)"],
                ["Paused", pausedCount, "var(--amber)"],
                ["Error", errorCount, "var(--red)"],
                ["Stopped", strategies.filter(s => s.status === "stopped").length, "var(--t2)"],
              ].map(([l, v, c]) => (
                <div key={l} className="sb-row">
                  <span className="sb-k">{l}</span>
                  <span className="sb-v" style={{ color: c }}>{v}</span>
                </div>
              ))}
            </div>
            <div className="exposure-block">
              <div className="exposure-title">Global Exposure</div>
              {[
                { key: "Net Open Lots", val: positions.reduce((s, p) => s + p.lots, 0), suffix: "", max: 20 },
                { key: "Margin at Risk", val: (positions.reduce((s, p) => s + p.lots, 0) * 25000) / 1000, suffix: "k", max: 500 },
                { key: "Net Delta", val: Math.abs(netDelta).toFixed(2), suffix: "", max: 5 },
                { key: "Daily Loss%", val: dailyLossPct, suffix: "%", max: 100 },
              ].map(({ key, val, suffix, max }) => {
                const pct = Math.min((parseFloat(val) / max) * 100, 100);
                return (
                  <div key={key} className="exp-meter">
                    <div className="exp-meter-header">
                      <span className="exp-meter-key">{key}</span>
                      <span className="exp-meter-val" style={{ color: pct >= 80 ? "var(--red)" : pct >= 60 ? "var(--amber)" : "var(--t1)" }}>{val}{suffix}</span>
                    </div>
                    <div className="exp-meter-bar">
                      <div className="exp-meter-fill" style={{ width: `${pct}%`, background: pct >= 80 ? "var(--red)" : pct >= 60 ? "var(--amber)" : "var(--green)" }} />
                    </div>
                  </div>
                );
              })}
            </div>
            <div className="sb-card">
              <div className="sb-card-title">Risk Limits</div>
              {[["Max Daily Loss", "₹50,000"], ["Max Lots/Strat", "2"], ["Max Margin", "80%"], ["Kill @ Loss", "₹45,000"]].map(([l, v]) => (
                <div key={l} className="sb-row">
                  <span className="sb-k" style={{ fontSize: 9 }}>{l}</span>
                  <span className="sb-v">{v}</span>
                </div>
              ))}
            </div>
          </div>
        </div>

        {/* CENTER */}
        <div className="center">
          <div className="grid-wrapper">
            {view === "strategies" && (
              <div className="strat-grid">
                {strategies.map(s => (
                  <StratCard key={s.id || s.name} s={s} selected={selected?.id === s.id}
                    onClick={() => setSelected(s === selected ? null : s)}
                    onAction={handleStratAction} />
                ))}
              </div>
            )}
            {view === "heatmap" && <HeatmapView strategies={strategies} onSelect={s => { setSelected(s); setView("strategies") }} />}
            {view === "alerts" && (
              <div style={{ maxWidth: 540, display: "flex", flexDirection: "column", gap: 7 }}>
                {alerts.length === 0 && <div className="empty"><div className="empty-icon">🔕</div><div>No active alerts</div></div>}
                {alerts.map(a => (
                  <div key={a.id} className={`alert-toast ${a.type}`}>
                    <div className="at-body">
                      <div className="at-title">{a.title}</div>
                      <div className="at-msg">{a.msg}</div>
                      <div className="at-time">{a.time}</div>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>

          {/* BOTTOM STRIP */}
          <div className="strip">
            <div className="strip-pane" style={{ flex: 3 }}>
              <div className="strip-tabs">
                {[["positions", `Positions (${positions.length})`], ["orders", "Order Flow"], ["logs", "System Logs"]].map(([id, label]) => (
                  <div key={id} className={`strip-tab ${bottomTab === id ? "active" : ""}`} onClick={() => setBottomTab(id)}>{label}</div>
                ))}
              </div>
              <div className="strip-body">
                {bottomTab === "positions" && <PositionsPanel positions={positions} />}
                {bottomTab === "orders" && <OrderFlowPanel events={orders} />}
                {bottomTab === "logs" && <LogsPanel logs={logs} />}
              </div>
            </div>
            <div className="strip-pane">
              <div className="strip-tabs"><div className="strip-tab active">Risk Monitor</div></div>
              <div className="strip-body">
                <MiniRiskPanel strategies={strategies} positions={positions} marginPct={marginPct} dailyLossPct={dailyLossPct} cbStates={cbStates} />
              </div>
            </div>
          </div>
        </div>
      </div>

      {/* DRAWERS + MODALS */}
      {selected && <StratDrawer s={selected} onClose={() => setSelected(null)} onAction={handleStratAction} />}

      {killModal && (
        <div className="modal-bg">
          <div className="modal">
            <div className="modal-title danger">⛔ Global Kill Switch</div>
            <div className="modal-body">
              This will immediately halt all <strong style={{ color: "var(--red)" }}>{runningCount} running strategies</strong> and block all new orders.
              Open positions remain — this is NOT an auto-squareoff. Manage positions manually.
            </div>
            <div className="modal-summary">
              <div className="ms-row"><span className="ms-l">Strategies to halt</span><span className="ms-v" style={{ color: "var(--red)" }}>{runningCount}</span></div>
              <div className="ms-row"><span className="ms-l">Open positions</span><span className="ms-v">{positions.length}</span></div>
              <div className="ms-row"><span className="ms-l">Current P&L</span><span className="ms-v" style={{ color: totalPnl >= 0 ? "var(--green)" : "var(--red)" }}>{f.pnl(totalPnl)}</span></div>
              <div className="ms-row"><span className="ms-l">Margin at risk</span><span className="ms-v">₹{(positions.reduce((s, p) => s + p.lots, 0) * 25000).toLocaleString("en-IN")}</span></div>
            </div>
            <div className="modal-warn">⚠ This state is persisted in PostgreSQL. It survives server restarts and frontend reloads.</div>
            <div className="modal-actions">
              <button className="btn-cancel" onClick={() => setKillModal(false)}>Cancel</button>
              <button className="btn-danger" onClick={() => { activateKill(); setKillModal(false); }}>
                ⛔ ACTIVATE KILL SWITCH
              </button>
            </div>
          </div>
        </div>
      )}

      {actionModal && (
        <div className="modal-bg">
          <div className="modal">
            <div className="modal-title danger">{actionModal.action === "pause" ? "Pause" : "Stop"} {actionModal.strat.name.replace(/_/g, " ")}?</div>
            <div className="modal-body">
              {actionModal.action === "stop" ? "This permanently stops the strategy. It will not auto-restart." : "Strategy will pause signal generation. Open positions unchanged."}
            </div>
            <div className="modal-summary">
              <div className="ms-row"><span className="ms-l">P&L</span><span className="ms-v" style={{ color: actionModal.strat.pnl >= 0 ? "var(--green)" : "var(--red)" }}>{f.pnl(actionModal.strat.pnl)}</span></div>
              <div className="ms-row"><span className="ms-l">Open Qty</span><span className="ms-v">{actionModal.strat.openQty || "—"}</span></div>
              <div className="ms-row"><span className="ms-l">Win Rate</span><span className="ms-v">{actionModal.strat.winRate}%</span></div>
            </div>
            <div className="modal-actions">
              <button className="btn-cancel" onClick={() => setActionModal(null)}>Cancel</button>
              <button className="btn-danger" onClick={() => { applyAction(actionModal.strat, actionModal.action); setActionModal(null) }}>
                Confirm {actionModal.action}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* ALERT TOASTS */}
      <div className="alert-stack">
        {alerts.map(a => (
          <div key={a.id} className={`alert-toast ${a.type}`}>
            <div className="at-body">
              <div className="at-title">{a.title}</div>
              <div className="at-msg">{a.msg}</div>
              <div className="at-time">{a.time}</div>
            </div>
            <button className="at-close" onClick={() => removeAlert(a.id)}>✕</button>
          </div>
        ))}
      </div>

      {/* KEYBOARD SHORTCUT BAR */}
      <div className="shortcut-bar">
        <span><kbd className="kbd">Shift+K</kbd> Kill</span>
        <span><kbd className="kbd">Shift+P</kbd> Pause All</span>
        <span><kbd className="kbd">Shift+R</kbd> Resume All</span>
        <span><kbd className="kbd">Shift+H</kbd> Heatmap</span>
        <span><kbd className="kbd">Esc</kbd> Close</span>
      </div>
    </div>
  );
}
