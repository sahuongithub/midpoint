(function(){
"use strict";
var $ = function(id){ return document.getElementById(id); };
var money = function(v){ return (v<0?"-$":"$") + Math.abs(v).toFixed(Math.abs(v)<10?2:0); };
var money0 = function(v){ return (v<0?"-$":"$") + Math.round(Math.abs(v)); };

/* ---------- 1. spread dragger: the gap is a fee, paid twice ---------- */
(function(){
  var BID=2.21, ASK=2.35, MID=2.28, host=$("sp"), h=$("sphandle"), zone=$("spzone");
  if(!host) return;
  function render(pct){
    pct = Math.max(12, Math.min(88, pct));
    var price = BID + (ASK-BID)*((pct-12)/76);
    h.style.left = pct+"%";
    h.setAttribute("aria-valuenow", price.toFixed(2));
    var lo = Math.min(pct,50), hi = Math.max(pct,50);
    zone.style.left = lo+"%"; zone.style.width = (hi-lo)+"%";
    var loss = (price-MID)*100;
    $("spPay").textContent = "$"+price.toFixed(2);
    $("spLoss").textContent = money0(loss);
    $("spLoss").className = "v " + (loss>0.5?"cost":(loss<-0.5?"good":"neu"));
    $("spRound").textContent = money0(Math.abs(loss)*2);
  }
  function fromEvent(e){
    var r = host.getBoundingClientRect();
    var x = (e.touches ? e.touches[0].clientX : e.clientX) - r.left;
    render(x/r.width*100);
  }
  var dragging=false;
  h.addEventListener("mousedown",function(){dragging=true;});
  h.addEventListener("touchstart",function(){dragging=true;},{passive:true});
  window.addEventListener("mousemove",function(e){ if(dragging) fromEvent(e); });
  window.addEventListener("touchmove",function(e){ if(dragging) fromEvent(e); },{passive:true});
  window.addEventListener("mouseup",function(){dragging=false;});
  window.addEventListener("touchend",function(){dragging=false;});
  host.addEventListener("click",fromEvent);
  h.addEventListener("keydown",function(e){
    var cur = parseFloat(h.style.left)||88;
    if(e.key==="ArrowLeft"){ render(cur-4); e.preventDefault(); }
    if(e.key==="ArrowRight"){ render(cur+4); e.preventDefault(); }
  });
  render(88);
})();

/* ---------- 2. the trade grader: the only part a stranger can point at their own trade ----------
   Bands are not invented. They are the measured cost of the 65 contracts we probed,
   grouped by the quoted width a trader can see BEFORE trading. Quoted width is an
   unreliable estimate of the true spread -- we measured that -- but it ranked
   contracts almost perfectly, which is exactly what a screening rule needs. ------- */
(function(){
  var B=$("tgB"), A=$("tgA"), N=$("tgN"), host=$("tgBands");
  if(!B||!A) return;

  var BANDS = [
    {lo:0,    hi:0.05, label:"under $0.05", n:25, med:1,   worst:10,  cls:"good"},
    {lo:0.05, hi:0.10, label:"$0.05–0.10",  n:12, med:2,   worst:10,  cls:"good"},
    {lo:0.10, hi:0.20, label:"$0.10–0.20",  n:10, med:20,  worst:30,  cls:"warn"},
    {lo:0.20, hi:0.50, label:"$0.20–0.50",  n:12, med:25,  worst:58,  cls:"bad"},
    {lo:0.50, hi:1e9,  label:"over $0.50",  n:6,  med:318, worst:347, cls:"bad"}
  ];
  var VERDICT = {
    good:{t:"CHEAP TO TRADE",  d:"This is the liquid end of the market. The toll here is small enough to ignore."},
    warn:{t:"GETTING EXPENSIVE", d:"You are at the edge of our measured rule. Check whether a nearby strike is tighter."},
    bad :{t:"EXPENSIVE — CHECK A NEARBY STRIKE", d:"Contracts quoted this wide were, in our measurements, where the real damage lived."}
  };

  function bandFor(w){
    for(var i=0;i<BANDS.length;i++) if(w>=BANDS[i].lo && w<BANDS[i].hi) return BANDS[i];
    return BANDS[BANDS.length-1];
  }

  function drawBands(active){
    var html = "";
    for(var i=0;i<BANDS.length;i++){
      var b=BANDS[i], on = (b===active);
      html += '<div class="band '+b.cls+(on?" on":"")+'">'
            +   '<div class="bl">'+b.label+'</div>'
            +   '<div class="bv">'+(b.med<0?"-$":"$")+Math.abs(b.med)+'</div>'
            +   '<div class="bn">worst $'+b.worst+'</div>'
            + '</div>';
    }
    host.innerHTML = html;
  }

  function calc(){
    var bid=parseFloat(B.value), ask=parseFloat(A.value), n=Math.max(1,parseInt(N.value,10)||1);
    var v=$("tgVerdict");
    if(!(bid>0)||!(ask>0)||ask<bid){
      v.className="verdict"; v.innerHTML="<b>Enter a bid and an ask.</b> The ask is the higher number.";
      $("tgCost").textContent="—"; $("tgPct").textContent="—";
      $("tgBE").textContent="—"; $("tgGate").textContent="—";
      $("tgNote").textContent=""; drawBands(null); return;
    }
    var w = ask-bid, mid=(ask+bid)/2;
    var perContract = w*100, total = perContract*n;
    var pct = w/mid*100;              /* the toll as a share of what you are paying */
    var be  = w/mid*100;              /* buy at ask, sell at bid: same number, stated as a move */
    var band = bandFor(w), vd = VERDICT[band.cls];

    v.className = "verdict "+band.cls;
    v.innerHTML = '<span class="vt">'+vd.t+'</span><span class="vd">'+vd.d+'</span>';

    $("tgCost").textContent = (total<10? "$"+total.toFixed(2) : "$"+Math.round(total));
    $("tgCost").className = "v "+(band.cls==="good"?"good":"cost");
    $("tgPct").textContent = pct.toFixed(1)+"%";
    $("tgBE").textContent  = be.toFixed(1)+"%";
    var passes = w<=0.20;
    $("tgGate").textContent = passes ? "TRADE" : "SKIP";
    $("tgGate").className = "v "+(passes?"good":"cost");

    drawBands(band);

    var note = "A gap of <b>$"+w.toFixed(2)+"</b> costs <b>$"+perContract.toFixed(2)+
      "</b> per contract to get in and back out"+(n>1? ", or <b>$"+Math.round(total)+"</b> for "+n+" contracts":"")+
      ". In our measurements, contracts quoted <b>"+band.label+"</b> wide cost a median of <b>$"+band.med+
      "</b> and as much as <b>$"+band.worst+"</b> in real money.";
    if(!passes) note += " This one is <b>outside the $0.20 rule we pre-registered</b> — the same rule that, "+
      "tested on 48 contracts it had never seen, never let through a trap costing over $50.";
    else note += " It is <b>inside the $0.20 rule we pre-registered</b>.";
    $("tgNote").innerHTML = note;
  }

  [B,A,N].forEach(function(i){ i.addEventListener("input",calc); });
  calc();
})();

/* ---------- 2. strike explorer: the 347x range on one underlying ---------- */
(function(){
  var rows = DATA.contracts, U=$("fU"), E=$("fE"), M=$("fM"), bars=$("bars");
  if(!bars) return;
  function opts(sel, vals, labels){
    sel.innerHTML = "";
    vals.forEach(function(v,i){
      var o=document.createElement("option"); o.value=v; o.textContent=labels[i]; sel.appendChild(o);
    });
  }
  var uniqU = ["all"].concat(Array.from(new Set(rows.map(function(r){return r.u;}))).sort());
  opts(U, uniqU, uniqU.map(function(v){return v==="all"?"All":v;}));
  var uniqE = ["all"].concat(Array.from(new Set(rows.map(function(r){return r.dte;}))));
  opts(E, uniqE, uniqE.map(function(v){return v==="all"?"All expiries":v;}));
  opts(M, ["all","itm","atm","otm"],
      ["All strikes","Deep in-the-money","Near the money","Out of the money"]);

  function bucket(r){ return r.mny<0.98?"itm":(r.mny<=1.02?"atm":"otm"); }
  function filtered(){
    return rows.filter(function(r){
      return (U.value==="all"||r.u===U.value) &&
             (E.value==="all"||r.dte===E.value) &&
             (M.value==="all"||bucket(r)===M.value);
    }).sort(function(a,b){ return a.cost-b.cost; });
  }
  function median(a){ if(!a.length) return 0; var s=a.slice().sort(function(x,y){return x-y;});
    return s[Math.floor(s.length/2)]; }
  function draw(){
    var f = filtered();
    bars.innerHTML = "";
    if(!f.length){ $("exN").textContent="0"; $("exNote").textContent="No contracts match that combination.";
      ["exMed","exMax","exMin"].forEach(function(i){$(i).textContent="—";}); $("axN").textContent="—"; return; }
    var max = Math.max.apply(null, f.map(function(r){return Math.max(r.cost,1);}));
    f.forEach(function(r,i){
      var b=document.createElement("div");
      b.className="bar"; b.style.height = Math.max(2,(Math.max(r.cost,0)/max)*100)+"%";
      b.title = r.c+" — "+money0(r.cost);
      b.setAttribute("role","button"); b.setAttribute("tabindex","0");
      b.addEventListener("click",function(){ select(i,f); });
      b.addEventListener("keydown",function(e){ if(e.key==="Enter"||e.key===" "){select(i,f);e.preventDefault();} });
      bars.appendChild(b);
    });
    var costs=f.map(function(r){return r.cost;});
    $("exN").textContent = f.length;
    $("exMed").textContent = money0(median(costs));
    $("exMax").textContent = money0(Math.max.apply(null,costs));
    $("exMin").textContent = money0(Math.min.apply(null,costs));
    $("axN").textContent = f.length+" contracts";
    var ratio = Math.max.apply(null,costs) / Math.max(1, median(costs));
    $("exNote").innerHTML = f.length>3
      ? "The most expensive contract here costs <b>"+Math.round(ratio)+"×</b> the typical one. Click any bar."
      : "Click any bar to see which contract it is.";
  }
  function select(i,f){
    Array.prototype.forEach.call(bars.children,function(b,j){ b.className = "bar" + (i===j?" sel":""); });
    var r=f[i];
    $("exNote").innerHTML = "<b>"+r.c+"</b> — "+r.u+", "+r.dte+", strike is "+
      ((r.mny-1)*100).toFixed(1)+"% from the share price. Real cost to get in and out: <b>"+
      money0(r.cost)+"</b> per contract.";
  }
  [U,E,M].forEach(function(s){ s.addEventListener("change",draw); });
  draw();
})();

/* ---------- 3. stock replacement: same exposure, 116x the fee ---------- */
(function(){
  var bs=$("wShares"), bo=$("wOption");
  if(!bs) return;
  var worst = DATA.contracts.filter(function(r){return r.mny<0.98;})
                .sort(function(a,b){return b.cost-a.cost;})[0];
  var oc = worst ? worst.cost : 347;
  function set(isOption){
    bs.setAttribute("aria-pressed", String(!isOption));
    bo.setAttribute("aria-pressed", String(isOption));
    $("twWhat").textContent = isOption ? "1 deep-ITM call" : "100 SPY shares";
    $("twCost").textContent = isOption ? money0(oc) : "$3";
    $("twCost").className = "v " + (isOption?"cost":"good");
    $("twRatio").textContent = isOption ? Math.round(oc/3)+"× more" : "baseline";
    $("twRatio").className = "v " + (isOption?"cost":"neu");
    $("twNote").innerHTML = isOption
      ? "Almost identical exposure — a deep in-the-money call moves nearly one-for-one with the shares. <b>"+
        Math.round(oc/3)+" times the hidden fee.</b> You are paying for the wrapper, not the position."
      : "SPY's own spread that afternoon was three cents. A hundred shares cost <b>$3</b> to get in and out of.";
  }
  bs.addEventListener("click",function(){set(false);});
  bo.addEventListener("click",function(){set(true);});
  set(false);
})();

/* ---------- 4. required win rate: the credit is priced, not free ---------- */
(function(){
  var W=$("wW"), C=$("wC");
  if(!W) return;
  function calc(){
    var w=Math.max(0.01,parseFloat(W.value)||1), c=Math.max(0.01,parseFloat(C.value)||0.01);
    if(c>=w) c = w-0.01;
    var win=c*100, lose=(w-c)*100, need=lose/(lose+win);
    $("wWin").textContent = money0(win);
    $("wLose").textContent = money0(lose);
    $("wNeed").textContent = (need*100).toFixed(1)+"%";
    $("wBar").style.width = (need*100)+"%";
  }
  [W,C].forEach(function(i){ i.addEventListener("input",calc); });
  calc();
})();

/* ---------- 5. oracle stepper: the price is the price ---------- */
(function(){
  var tb=$("orTb"), btn=$("orNext");
  if(!tb) return;
  var probes = DATA.oracle, i=0;
  btn.addEventListener("click",function(){
    if(i>=probes.length) return;
    var p=probes[i++];
    var tr=document.createElement("tr");
    tr.innerHTML = '<td class="n">$'+p.limit.toFixed(2)+'</td>'+
                   '<td class="n" style="color:var(--indigo);font-weight:600">$'+p.fill.toFixed(2)+'</td>'+
                   '<td class="n" style="color:var(--green)">'+(p.fill-p.limit>=0?"+":"")+
                   (p.fill-p.limit).toFixed(2)+'</td>';
    tb.appendChild(tr);
    if(i>=probes.length){
      btn.disabled=true; btn.textContent="All six sent";
      $("orHint").textContent="";
      $("orNote").style.display="block";
    } else {
      $("orHint").textContent = (probes.length-i)+" to go — watch the middle column barely move.";
    }
  });
})();

/* ---------- 6. paired trials, frequency framing rather than a CI ---------- */
(function(){
  var host=$("pdots");
  if(!host) return;
  var t=DATA.paired, better=0, worse=0, sum=0;
  t.forEach(function(p,i){
    var d=document.createElement("div");
    var cheaper = p.diff < 0;
    if(cheaper) better++; else worse++;
    sum += p.diff;
    d.className = "dot " + (cheaper?"dn":"up");
    d.textContent = (p.diff>=0?"+":"")+(p.diff*100).toFixed(0);
    d.title = "Pair "+(i+1)+": waiting was "+(cheaper?"cheaper":"dearer")+
              " by "+money(Math.abs(p.diff*100))+" per contract";
    host.appendChild(d);
  });
  $("pWin").textContent = better+" of "+t.length;
  $("pLose").textContent = worse+" of "+t.length;
  var avg=(sum/t.length)*100;
  $("pDiff").textContent = (avg>=0?"+":"")+money(Math.abs(avg)).replace("$","$");
  $("pDiff").className = "v " + (Math.abs(avg)<0.5 ? "neu" : (avg<0?"good":"cost"));
})();

/* ---------- 7. the gate: one measured rule does the work ---------- */
(function(){
  var thr=$("gThr"), bars=$("gBars");
  if(!thr) return;
  var rows = DATA.contracts.slice().sort(function(a,b){ return a.indW-b.indW; });
  function draw(){
    var t = parseInt(thr.value,10)/100;
    $("gVal").textContent = "$"+t.toFixed(2);
    bars.innerHTML="";
    var pass=[], rej=[];
    var max = Math.max.apply(null, rows.map(function(r){return Math.max(r.cost,1);}));
    rows.forEach(function(r){
      var ok = r.indW <= t;
      (ok?pass:rej).push(r.cost);
      var b=document.createElement("div");
      b.className="bar "+(ok?"pass":"rej");
      b.style.height = Math.max(2,(Math.max(r.cost,0)/max)*100)+"%";
      b.title = r.c+" — quoted $"+r.indW.toFixed(2)+" wide,true cost "+money0(r.cost);
      bars.appendChild(b);
    });
    function mean(a){ return a.length ? a.reduce(function(x,y){return x+y;},0)/a.length : 0; }
    $("gPass").textContent = pass.length;
    $("gRej").textContent = rej.length;
    $("gPassC").textContent = pass.length?money(mean(pass)):"—";
    $("gRejC").textContent = rej.length?money0(mean(rej)):"—";
    var slipped = pass.filter(function(c){return c>50;}).length;
    var avoided = rej.reduce(function(x,y){return x+y;},0);
    $("gNote").innerHTML = "At this line you avoid <b>"+money0(avoided)+"</b> of cost across "+rej.length+
      " contracts, and <b>"+slipped+"</b> expensive ones slip through." +
      (Math.abs(t-0.20)<0.001 ? " <b>This is the rule we pre-registered.</b>" : "");
  }
  thr.addEventListener("input",draw);
  draw();
})();
})();
