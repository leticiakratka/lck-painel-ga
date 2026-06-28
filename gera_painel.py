#!/usr/bin/env python3
"""Gera o painel HTML da LCK puxando dados do GA4, com seletor de periodo.
Uso:
  python3 gera_painel.py                      -> periodos padrao (30/60/90/365/todo)
  python3 gera_painel.py 2026-03-01 2026-04-15 -> adiciona um periodo personalizado
Saida: ~/.config/lck-ga/painel.html
"""
import json, sys, os, urllib.request, urllib.error, datetime
import google.auth.transport.requests
from google.oauth2 import service_account

PID = "396197354"
OUT = os.environ.get("PAINEL_OUT", "/Users/leticiakratka/.config/lck-ga/painel.html")
SCOPES = ["https://www.googleapis.com/auth/analytics.readonly"]

# Credencial: do cofre (env GA_SA_JSON, usado no GitHub Actions) ou do arquivo local.
SA_JSON = os.environ.get("GA_SA_JSON")
if SA_JSON:
    creds = service_account.Credentials.from_service_account_info(json.loads(SA_JSON), scopes=SCOPES)
else:
    creds = service_account.Credentials.from_service_account_file(
        "/Users/leticiakratka/.config/lck-ga/service-account.json", scopes=SCOPES)
creds.refresh(google.auth.transport.requests.Request())
TOKEN = creds.token

def run(body, quiet=False):
    url = f"https://analyticsdata.googleapis.com/v1beta/properties/{PID}:runReport"
    req = urllib.request.Request(url, data=json.dumps(body).encode(),
        headers={"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        if not quiet:
            print("ERRO", e.code, e.read().decode()[:300])
        return {"rows": []}

# ---------- consultas escopadas por intervalo (dr) ----------
def totals(dr):
    d = run({"dateRanges": dr, "metrics": [
        {"name": "activeUsers"}, {"name": "newUsers"}, {"name": "sessions"},
        {"name": "screenPageViews"}, {"name": "engagementRate"},
        {"name": "averageSessionDuration"}]})
    r = d.get("rows", [])
    if not r: return [0]*6
    return [float(m["value"]) for m in r[0]["metricValues"]]

def ev_count(dr, name):
    d = run({"dateRanges": dr, "dimensions": [{"name": "eventName"}],
        "metrics": [{"name": "eventCount"}],
        "dimensionFilter": {"filter": {"fieldName": "eventName",
            "stringFilter": {"value": name}}}})
    r = d.get("rows", [])
    return int(r[0]["metricValues"][0]["value"]) if r else 0

def daily_series(dr, gran):
    dimname = {"date": "date", "week": "yearWeek", "month": "yearMonth"}[gran]
    d = run({"dateRanges": dr, "dimensions": [{"name": dimname}],
        "metrics": [{"name": "activeUsers"}, {"name": "sessions"}],
        "orderBys": [{"dimension": {"dimensionName": dimname}}]})
    labels, users, sess = [], [], []
    for rw in d.get("rows", []):
        v = rw["dimensionValues"][0]["value"]
        if gran == "date":
            lab = f"{v[6:8]}/{v[4:6]}"
        elif gran == "week":
            lab = f"S{v[4:6]}/{v[2:4]}"
        else:
            lab = f"{v[4:6]}/{v[2:4]}"
        labels.append(lab)
        users.append(int(rw["metricValues"][0]["value"]))
        sess.append(int(rw["metricValues"][1]["value"]))
    return {"labels": labels, "users": users, "sess": sess}

def table_for(dr, dims, metric, n=10):
    d = run({"dateRanges": dr, "dimensions": [{"name": x} for x in dims],
        "metrics": [{"name": metric}],
        "orderBys": [{"metric": {"metricName": metric}, "desc": True}], "limit": n})
    out = []
    for rw in d.get("rows", [])[:n]:
        out.append(([dv["value"] for dv in rw["dimensionValues"]],
                    int(rw["metricValues"][0]["value"])))
    return out

NOMES_BIO = {
    "leticiakratka.com.br/": "Site principal (home)",
    "leticiakratka.com.br/consultoria": "Consultoria",
    "cxlvoomp.leticiakratka.com.br/": "CXL Voomp",
    "jantarfinanceiro.leticiakratka.com.br/": "Jantar Financeiro",
}

def bio_for(dr):
    bv = run({"dateRanges": dr, "dimensions": [{"name": "hostName"}],
        "metrics": [{"name": "screenPageViews"}],
        "dimensionFilter": {"filter": {"fieldName": "hostName",
            "stringFilter": {"matchType": "CONTAINS", "value": "bio.leticiakratka"}}}})
    visits = int(bv["rows"][0]["metricValues"][0]["value"]) if bv.get("rows") else 0

    # 1) tenta a medicao EXATA pelo evento bio_click (precisa da custom dimension link_name registrada no GA4)
    ex = run({"dateRanges": dr, "dimensions": [{"name": "customEvent:link_name"}],
        "metrics": [{"name": "eventCount"}],
        "dimensionFilter": {"filter": {"fieldName": "eventName", "stringFilter": {"value": "bio_click"}}},
        "orderBys": [{"metric": {"metricName": "eventCount"}, "desc": True}]}, quiet=True)
    rows_ex = [([r["dimensionValues"][0]["value"]], int(r["metricValues"][0]["value"]))
               for r in ex.get("rows", [])
               if r["dimensionValues"][0]["value"] not in ("(not set)", "(not provided)")]
    if rows_ex:
        return {"visits": visits, "total": sum(v for _, v in rows_ex), "rows": rows_ex, "exato": True}

    # 2) fallback: metodo inferido pelo destino
    raw = run({"dateRanges": dr,
        "dimensions": [{"name": "hostName"}, {"name": "landingPage"}],
        "metrics": [{"name": "sessions"}],
        "dimensionFilter": {"filter": {"fieldName": "pageReferrer",
            "stringFilter": {"matchType": "CONTAINS", "value": "bio.leticiakratka"}}},
        "orderBys": [{"metric": {"metricName": "sessions"}, "desc": True}]})
    agg = {}
    for rw in raw.get("rows", []):
        h = rw["dimensionValues"][0]["value"]; p = rw["dimensionValues"][1]["value"]
        if h == "bio.leticiakratka.com.br":
            continue
        key = h + "/" if p in ("(not set)", "", "/") else h + p
        agg[key] = agg.get(key, 0) + int(rw["metricValues"][0]["value"])
    links = sorted(agg.items(), key=lambda x: -x[1])
    rows = [([NOMES_BIO.get(k, k)], v) for k, v in links]
    return {"visits": visits, "total": sum(v for _, v in links), "rows": rows, "exato": False}

def consult_for(dr):
    d = run({"dateRanges": dr, "dimensions": [{"name": "pagePath"}],
        "metrics": [{"name": "eventCount"}],
        "dimensionFilter": {"andGroup": {"expressions": [
            {"filter": {"fieldName": "eventName", "stringFilter": {"value": "generate_lead"}}},
            {"filter": {"fieldName": "pagePath", "stringFilter": {"matchType": "CONTAINS", "value": "/consultoria/"}}}]}}})
    leads = sum(int(r["metricValues"][0]["value"]) for r in d.get("rows", []))
    ct = run({"dateRanges": dr, "dimensions": [{"name": "pagePath"}],
        "metrics": [{"name": "sessions"}, {"name": "activeUsers"}],
        "dimensionFilter": {"filter": {"fieldName": "pagePath",
            "stringFilter": {"matchType": "EXACT", "value": "/consultoria/"}}}})
    ctr = ct.get("rows", [])
    users = int(ctr[0]["metricValues"][1]["value"]) if ctr else 0
    rate = round(leads / users * 100, 1) if users else 0
    # quebra por faixa de renda (lead_consultoria) - precisa custom dimension renda registrada
    rb = run({"dateRanges": dr, "dimensions": [{"name": "customEvent:renda"}],
        "metrics": [{"name": "eventCount"}],
        "dimensionFilter": {"filter": {"fieldName": "eventName", "stringFilter": {"value": "lead_consultoria"}}},
        "orderBys": [{"metric": {"metricName": "eventCount"}, "desc": True}]}, quiet=True)
    renda = [([r["dimensionValues"][0]["value"]], int(r["metricValues"][0]["value"]))
             for r in rb.get("rows", [])
             if r["dimensionValues"][0]["value"] not in ("(not set)", "(not provided)")]
    return {"users": users, "leads": leads, "rate": rate, "renda": renda}

PRODUTOS = [
    ("Jantar Financeiro", ["jantarfinanceiro.leticiakratka.com.br"]),
    ("Caixa Livre", ["cxlvoomp.leticiakratka.com.br", "cxl.leticiakratka.com.br",
                     "cxl01.leticiakratka.com.br", "planovoomp.leticiakratka.com.br",
                     "planofinanceiro.leticiakratka.com.br"]),
]

def produtos_for(dr):
    out = []
    for name, hosts in PRODUTOS:
        inlist = {"fieldName": "hostName", "inListFilter": {"values": hosts}}
        tot = run({"dateRanges": dr, "dimensions": [{"name": "hostName"}],
            "metrics": [{"name": "activeUsers"}, {"name": "sessions"}],
            "dimensionFilter": {"filter": inlist},
            "orderBys": [{"metric": {"metricName": "activeUsers"}, "desc": True}]})
        pages, users, sessions = [], 0, 0
        for r in tot.get("rows", []):
            h = r["dimensionValues"][0]["value"]
            u = int(r["metricValues"][0]["value"]); s = int(r["metricValues"][1]["value"])
            pages.append(([h], u)); users += u; sessions += s
        co = run({"dateRanges": dr, "metrics": [{"name": "eventCount"}],
            "dimensionFilter": {"andGroup": {"expressions": [
                {"filter": {"fieldName": "eventName", "stringFilter": {"value": "begin_checkout"}}},
                {"filter": inlist}]}}})
        checkout = int(co["rows"][0]["metricValues"][0]["value"]) if co.get("rows") else 0
        out.append({"name": name, "users": users, "sessions": sessions,
                    "checkout": checkout, "pages": pages})
    return out

def vendas_for(dr):
    ec = run({"dateRanges": dr, "metrics": [
        {"name": "transactions"}, {"name": "totalRevenue"}, {"name": "ecommercePurchases"}]})
    m = ec["rows"][0]["metricValues"] if ec.get("rows") else None
    total = {
        "transactions": int(float(m[0]["value"])) if m else 0,
        "revenue": round(float(m[1]["value"])) if m else 0,
        "purchases": int(float(m[2]["value"])) if m else 0,
    }
    it = run({"dateRanges": dr, "dimensions": [{"name": "itemName"}],
        "metrics": [{"name": "itemRevenue"}, {"name": "itemsPurchased"}],
        "orderBys": [{"metric": {"metricName": "itemRevenue"}, "desc": True}]})
    items = [([r["dimensionValues"][0]["value"]], round(float(r["metricValues"][0]["value"])),
              int(float(r["metricValues"][1]["value"]))) for r in it.get("rows", [])]
    bh = run({"dateRanges": dr, "dimensions": [{"name": "hostName"}],
        "metrics": [{"name": "eventCount"}, {"name": "totalRevenue"}],
        "dimensionFilter": {"filter": {"fieldName": "eventName", "stringFilter": {"value": "purchase"}}},
        "orderBys": [{"metric": {"metricName": "eventCount"}, "desc": True}]})
    hosts = [([r["dimensionValues"][0]["value"]], int(float(r["metricValues"][0]["value"])),
              round(float(r["metricValues"][1]["value"]))) for r in bh.get("rows", [])]
    return {"total": total, "items": items, "hosts": hosts}

def pct(c, p):
    if p in (0, None): return None
    return round((c - p) / p * 100, 1)

def build_period(cur, prev, gran):
    t_cur = totals(cur)
    t_prev = totals(prev) if prev else None
    co_c = ev_count(cur, "begin_checkout"); co_p = ev_count(prev, "begin_checkout") if prev else None
    le_c = ev_count(cur, "generate_lead"); le_p = ev_count(prev, "generate_lead") if prev else None
    def D(c, p): return pct(c, p) if prev else None
    kpis = [
        {"label": "Usuarios ativos", "v": int(t_cur[0]), "d": D(t_cur[0], t_prev[0] if t_prev else 0)},
        {"label": "Novos usuarios", "v": int(t_cur[1]), "d": D(t_cur[1], t_prev[1] if t_prev else 0)},
        {"label": "Sessoes", "v": int(t_cur[2]), "d": D(t_cur[2], t_prev[2] if t_prev else 0)},
        {"label": "Pageviews", "v": int(t_cur[3]), "d": D(t_cur[3], t_prev[3] if t_prev else 0)},
        {"label": "Taxa engajamento", "v": round(t_cur[4]*100, 1), "suf": "%", "d": D(t_cur[4], t_prev[4] if t_prev else 0)},
        {"label": "Tempo medio sessao", "v": int(t_cur[5]), "suf": "s", "d": D(t_cur[5], t_prev[5] if t_prev else 0)},
        {"label": "Begin checkout", "v": co_c, "d": D(co_c, co_p or 0), "hot": True},
        {"label": "Leads gerados", "v": le_c, "d": D(le_c, le_p or 0), "hot": True},
    ]
    return {
        "kpis": kpis,
        "daily": daily_series(cur, gran),
        "canais": table_for(cur, ["sessionDefaultChannelGroup"], "sessions"),
        "paginas": table_for(cur, ["pagePath"], "screenPageViews"),
        "fontes": table_for(cur, ["sessionSource", "sessionMedium"], "sessions"),
        "devices": table_for(cur, ["deviceCategory"], "sessions"),
        "bio": bio_for(cur),
        "consult": consult_for(cur),
        "produtos": produtos_for(cur),
        "vendas": vendas_for(cur),
    }

# ---------- periodos ----------
def dr(s, e): return [{"startDate": s, "endDate": e}]

CFG = [
    ("30 dias", dr("30daysAgo", "today"), dr("60daysAgo", "31daysAgo"), "date"),
    ("60 dias", dr("60daysAgo", "today"), dr("120daysAgo", "61daysAgo"), "date"),
    ("90 dias", dr("90daysAgo", "today"), dr("180daysAgo", "91daysAgo"), "week"),
    ("365 dias", dr("365daysAgo", "today"), dr("730daysAgo", "366daysAgo"), "month"),
    ("Todo o periodo", dr("2020-01-01", "today"), None, "month"),
]
# periodo personalizado via argumentos: gera_painel.py AAAA-MM-DD AAAA-MM-DD
if len(sys.argv) == 3:
    s, e = sys.argv[1], sys.argv[2]
    CFG.insert(0, (f"{s} a {e}", dr(s, e), None, "date"))

print("Gerando periodos...", flush=True)
PERIODS = {}
ORDER = []
for label, cur, prev, gran in CFG:
    print(" -", label, flush=True)
    PERIODS[label] = build_period(cur, prev, gran)
    ORDER.append(label)

agora = datetime.datetime.now().strftime("%d/%m/%Y %H:%M")
PAYLOAD = {"order": ORDER, "periods": PERIODS, "agora": agora}

HTML = """<!DOCTYPE html>
<html lang="pt-BR"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Painel LCK - Google Analytics</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.1/dist/chart.umd.min.js"></script>
<style>
:root{--bg:#0f1115;--card:#1a1d24;--line:#272b34;--txt:#e8eaed;--mut:#9aa0aa;--ac:#c8a96a;--up:#4ade80;--dn:#f87171}
*{box-sizing:border-box;margin:0;padding:0}
body{background:var(--bg);color:var(--txt);font-family:-apple-system,Segoe UI,Roboto,sans-serif;padding:28px;max-width:1200px;margin:0 auto}
.head{display:flex;justify-content:space-between;align-items:flex-end;flex-wrap:wrap;gap:12px}
h1{font-size:22px;font-weight:600;letter-spacing:.3px}
.sub{color:var(--mut);font-size:13px;margin-top:4px}
.period{display:flex;align-items:center;gap:8px}
.period label{color:var(--mut);font-size:12px;text-transform:uppercase;letter-spacing:.5px}
select{background:var(--card);color:var(--txt);border:1px solid var(--ac);border-radius:10px;padding:9px 12px;font-size:14px;font-family:inherit;font-weight:600;cursor:pointer}
.grid{display:grid;gap:14px;margin-top:22px}
.kpis{grid-template-columns:repeat(4,1fr)}
.card{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:18px}
.kpi .lab{color:var(--mut);font-size:12px;text-transform:uppercase;letter-spacing:.5px}
.kpi .val{font-size:28px;font-weight:700;margin-top:8px}
.kpi.hot{border-color:var(--ac)}.kpi.hot .val{color:var(--ac)}
.delta{font-size:12px;margin-top:6px;font-weight:600}
.delta.up{color:var(--up)}.delta.dn{color:var(--dn)}.delta.na{color:var(--mut)}
.two{grid-template-columns:2fr 1fr}.full{grid-template-columns:1fr}.row2{grid-template-columns:1fr 1fr}
h2{font-size:14px;font-weight:600;margin-bottom:14px;color:var(--txt)}
table{width:100%;border-collapse:collapse;font-size:13px}
td{padding:8px 4px;border-bottom:1px solid var(--line);color:var(--mut)}
td.v{text-align:right;color:var(--txt);font-weight:600;white-space:nowrap}
td.k{color:var(--txt);max-width:340px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.foot{color:var(--mut);font-size:12px;margin-top:24px;text-align:center}
canvas{max-height:300px}
.tabs{display:flex;gap:6px;margin-top:22px;border-bottom:1px solid var(--line);flex-wrap:wrap}
.tab{background:none;border:none;color:var(--mut);font-size:14px;font-weight:600;padding:12px 18px;cursor:pointer;border-bottom:2px solid transparent;font-family:inherit}
.tab:hover{color:var(--txt)}.tab.active{color:var(--ac);border-bottom-color:var(--ac)}
.tabpane{display:none}.tabpane.active{display:block}
.mrow{padding:13px 0;border-bottom:1px solid var(--line)}
.mrow .top{display:flex;justify-content:space-between;align-items:baseline;font-size:14px;margin-bottom:7px}
.mrow .nm{color:var(--txt);font-weight:600}.mrow .vl{color:var(--mut);white-space:nowrap}.mrow .vl b{color:var(--ac)}
.barwrap{background:var(--line);border-radius:6px;height:9px;overflow:hidden}
.bar{height:100%;background:var(--ac);border-radius:6px}
.bigstat{display:flex;gap:44px;flex-wrap:wrap;align-items:baseline;margin-top:6px}
.bigstat .n{font-size:40px;font-weight:700}.bigstat .n.ac{color:var(--ac)}
.bigstat .l{color:var(--mut);font-size:12px;text-transform:uppercase;letter-spacing:.5px;margin-top:4px}
.funnel{margin-top:8px}
.fstep{display:flex;justify-content:space-between;align-items:center;background:rgba(255,255,255,.03);border:1px solid var(--line);border-radius:10px;padding:14px 16px;margin-bottom:8px}
.fstep .fn{font-weight:600}.fstep .fv{font-weight:700;font-size:18px}
.fstep.gold{border-color:var(--ac)}.fstep.gold .fv{color:var(--ac)}
.farrow{text-align:center;color:var(--mut);font-size:12px;margin:2px 0 8px}
@media(max-width:820px){.kpis{grid-template-columns:repeat(2,1fr)}.two,.row2{grid-template-columns:1fr}}
</style></head><body>
<div class="head">
  <div>
    <h1>Painel LCK &middot; Google Analytics</h1>
    <div class="sub" id="subhdr"></div>
  </div>
  <div class="period">
    <label for="period">Periodo</label>
    <select id="period"></select>
  </div>
</div>

<div class="tabs">
  <button class="tab active" data-pane="p-geral">Visao geral</button>
  <button class="tab" data-pane="p-bio">&#128279; Link da bio</button>
  <button class="tab" data-pane="p-prod">&#128722; Produtos</button>
  <button class="tab" data-pane="p-conv">&#127919; Conversao</button>
</div>

<div class="tabpane active" id="p-geral">
  <div class="grid kpis" id="kpis"></div>
  <div class="grid two">
    <div class="card"><h2>Trafego no periodo</h2><canvas id="lc"></canvas></div>
    <div class="card"><h2>Canais</h2><canvas id="dc"></canvas></div>
  </div>
  <div class="grid row2">
    <div class="card"><h2>Paginas mais vistas</h2><table id="tp"></table></div>
    <div class="card"><h2>Origem do trafego (fonte / midia)</h2><table id="tf"></table></div>
  </div>
  <div class="grid row2">
    <div class="card"><h2>Dispositivos</h2><table id="td"></table></div>
    <div class="card"><h2>Sessoes por canal</h2><table id="tc"></table></div>
  </div>
</div>

<div class="tabpane" id="p-bio">
  <div class="grid full">
    <div class="card" style="border-color:var(--ac);background:linear-gradient(180deg,rgba(200,169,106,.07),var(--card))">
      <h2 style="color:var(--ac)">Resumo &middot; bio.leticiakratka.com.br</h2>
      <div class="bigstat" id="bio-stats"></div>
    </div>
  </div>
  <div class="grid two">
    <div class="card"><h2>Cliques por link (% do total)</h2><div id="bio-bars"></div></div>
    <div class="card"><h2>Distribuicao</h2><canvas id="bio-chart"></canvas></div>
  </div>
  <div class="sub" id="bio-note" style="line-height:1.5;margin-top:14px"></div>
</div>

<div class="tabpane" id="p-prod">
  <div class="grid full">
    <div class="card" style="border-color:var(--ac);background:linear-gradient(180deg,rgba(200,169,106,.07),var(--card))">
      <h2 style="color:var(--ac)">&#128176; Vendas no periodo (checkout)</h2>
      <div class="bigstat" id="vendas-stats"></div>
      <div style="margin-top:16px"><div class="sub" style="margin-bottom:6px">Receita por produto (item)</div><div id="vendas-items"></div></div>
      <div style="margin-top:16px"><div class="sub" style="margin-bottom:6px">Compras por checkout</div><div id="vendas-hosts"></div></div>
      <div class="sub" id="voomp-flag" style="margin-top:12px;line-height:1.5"></div>
    </div>
  </div>
  <h2 style="margin:24px 0 0">Interesse nas paginas de produto</h2>
  <div id="prod-cards"></div>
  <div class="sub" style="line-height:1.5;margin-top:14px">Visitantes e sessoes das paginas. O <b>begin_checkout</b> mostra intencao de compra. A venda final vem da secao de vendas acima (pixel do checkout).</div>
</div>

<div class="tabpane" id="p-conv">
  <div class="grid full">
    <div class="card" style="border-color:var(--ac);background:linear-gradient(180deg,rgba(200,169,106,.07),var(--card))">
      <h2 style="color:var(--ac)">Pagina da Consultoria &middot; /consultoria/</h2>
      <div class="bigstat" id="conv-stats"></div>
    </div>
  </div>
  <div class="grid full">
    <div class="card"><h2>Funil da pagina</h2><div class="funnel" id="conv-funnel"></div></div>
  </div>
  <div class="grid full" id="renda-card" style="display:none">
    <div class="card"><h2>Leads por faixa de renda (% do total)</h2><div id="renda-bars"></div></div>
  </div>
  <div class="sub" style="line-height:1.5;margin-top:14px">Taxa de conversao = formularios enviados (generate_lead) sobre visitantes unicos de /consultoria/. A quebra por <b>faixa de renda</b> (lead_consultoria) aparece sozinha aqui quando comecar a registrar.</div>
</div>

<div class="foot">GA4 propriedade __PID__ &middot; atualiza sozinho 1x/dia &middot; gerado em __AGORA__</div>
<script>
const ALL = __DATA__;
const fmt = n => n.toLocaleString('pt-BR');
let charts = {};

/* seletor de periodo */
const sel = document.getElementById('period');
ALL.order.forEach((k,i)=>{ const o=document.createElement('option'); o.value=k; o.textContent='Ultimos '+k; if(k==='Todo o periodo')o.textContent=k; if(k.includes(' a '))o.textContent=k; sel.appendChild(o); });
sel.value = ALL.order[0];
sel.addEventListener('change', ()=>render(sel.value));

/* abas */
document.querySelectorAll('.tab').forEach(t=>t.addEventListener('click',()=>{
  document.querySelectorAll('.tab').forEach(x=>x.classList.remove('active'));
  document.querySelectorAll('.tabpane').forEach(x=>x.classList.remove('active'));
  t.classList.add('active');
  document.getElementById(t.dataset.pane).classList.add('active');
}));

function barRows(id, rows, total){
  const max = total || rows.reduce((s,r)=>s+r[1],0) || 1;
  document.getElementById(id).innerHTML = rows.map(r=>{
    const name = Array.isArray(r[0])?r[0][0]:r[0]; const p=r[1]/max*100;
    return `<div class="mrow"><div class="top"><span class="nm">${name}</span>`+
      `<span class="vl">${fmt(r[1])} &middot; <b>${p.toFixed(1)}%</b></span></div>`+
      `<div class="barwrap"><div class="bar" style="width:${p.toFixed(1)}%"></div></div></div>`;
  }).join('');
}
function tbl(id,rows,join){document.getElementById(id).innerHTML=rows.map(r=>
  `<tr><td class="k">${(Array.isArray(r[0])?r[0].join(join||' / '):r[0])}</td><td class="v">${fmt(r[1])}</td></tr>`).join('')}

const cc=['#c8a96a','#6aa9c8','#9c7ed6','#7ed68f','#d67e9c','#d6b67e','#7ed6d0','#b0b0b0'];

function render(key){
  const D = ALL.periods[key];
  document.getElementById('subhdr').innerHTML = (key.includes(' a ')||key==='Todo o periodo'?key:'Ultimos '+key)+' &middot; atualizado em '+ALL.agora;

  /* KPIs */
  document.getElementById('kpis').innerHTML = D.kpis.map(k=>{
    let d='', cls='na', arrow='';
    if(k.d===null){d='sem comparativo'} else {cls=k.d>=0?'up':'dn';arrow=k.d>=0?'\\u25B2':'\\u25BC';d=arrow+' '+Math.abs(k.d)+'%'}
    return `<div class="card kpi ${k.hot?'hot':''}"><div class="lab">${k.label}</div>`+
      `<div class="val">${fmt(k.v)}${k.suf||''}</div><div class="delta ${cls}">${d}</div></div>`;
  }).join('');

  /* charts (destroi antes de recriar) */
  Object.values(charts).forEach(c=>c&&c.destroy());
  charts.lc = new Chart(document.getElementById('lc'),{type:'line',data:{labels:D.daily.labels,datasets:[
    {label:'Usuarios',data:D.daily.users,borderColor:'#c8a96a',backgroundColor:'rgba(200,169,106,.12)',fill:true,tension:.35,pointRadius:0,borderWidth:2},
    {label:'Sessoes',data:D.daily.sess,borderColor:'#6aa9c8',fill:false,tension:.35,pointRadius:0,borderWidth:2}]},
    options:{plugins:{legend:{labels:{color:'#9aa0aa'}}},scales:{x:{ticks:{color:'#9aa0aa',maxTicksLimit:12},grid:{display:false}},y:{ticks:{color:'#9aa0aa'},grid:{color:'#272b34'}}}}});
  charts.dc = new Chart(document.getElementById('dc'),{type:'doughnut',data:{labels:D.canais.map(r=>r[0][0]),
    datasets:[{data:D.canais.map(r=>r[1]),backgroundColor:cc,borderColor:'#1a1d24',borderWidth:2}]},
    options:{plugins:{legend:{position:'bottom',labels:{color:'#9aa0aa',boxWidth:12,font:{size:11}}}}}});

  tbl('tp',D.paginas);tbl('tf',D.fontes,' / ');tbl('td',D.devices);tbl('tc',D.canais);

  /* BIO */
  const bioCtr = D.bio.visits ? (D.bio.total/D.bio.visits*100) : 0;
  document.getElementById('bio-stats').innerHTML =
    `<div><div class="n">${fmt(D.bio.visits)}</div><div class="l">Visitas na bio</div></div>`+
    `<div><div class="n">${fmt(D.bio.total)}</div><div class="l">Cliques nos links</div></div>`+
    `<div><div class="n ac">${bioCtr.toFixed(1)}%</div><div class="l">Taxa de clique</div></div>`;
  barRows('bio-bars', D.bio.rows, D.bio.total);
  document.getElementById('bio-note').innerHTML = D.bio.exato
    ? '&#9989; <b>Medicao exata</b> por botao (evento bio_click). Cada clique e contado de verdade na pagina da bio.'
    : 'Cliques <b>inferidos pelo destino</b> (sessoes que sairam da bio pra cada pagina) &mdash; numero aproximado. A medicao exata por botao (bio_click) ja foi instalada e troca pra ca sozinha quando acumular dados.';
  charts.bio = new Chart(document.getElementById('bio-chart'),{type:'doughnut',data:{labels:D.bio.rows.map(r=>r[0][0]),
    datasets:[{data:D.bio.rows.map(r=>r[1]),backgroundColor:cc,borderColor:'#1a1d24',borderWidth:2}]},
    options:{plugins:{legend:{position:'bottom',labels:{color:'#9aa0aa',boxWidth:12,font:{size:11}}}}}});

  /* VENDAS */
  const brl = n => 'R$ ' + fmt(n);
  document.getElementById('vendas-stats').innerHTML =
    `<div><div class="n ac">${brl(D.vendas.total.revenue)}</div><div class="l">Receita</div></div>`+
    `<div><div class="n">${fmt(D.vendas.total.purchases)}</div><div class="l">Compras</div></div>`+
    `<div><div class="n">${fmt(D.vendas.total.transactions)}</div><div class="l">Transacoes</div></div>`;
  document.getElementById('vendas-items').innerHTML = D.vendas.items.length
    ? D.vendas.items.map(r=>`<div class="mrow"><div class="top"><span class="nm">${r[0][0]}</span><span class="vl"><b>${brl(r[1])}</b> &middot; ${fmt(r[2])} vendas</span></div></div>`).join('')
    : '<div class="sub">Os checkouts nao enviam o nome do produto, entao a receita nao se separa por item aqui.</div>';
  document.getElementById('vendas-hosts').innerHTML = D.vendas.hosts.length
    ? D.vendas.hosts.map(r=>`<div class="mrow"><div class="top"><span class="nm">${r[0][0]}</span><span class="vl">${fmt(r[1])} compras &middot; <b>${brl(r[2])}</b></span></div></div>`).join('')
    : '<div class="sub">Sem compras registradas no periodo.</div>';
  document.getElementById('voomp-flag').innerHTML =
    '&#9888;&#65039; <b>Atencao:</b> a Voomp (pay.voompcreators) registra o begin_checkout mas <b>nao dispara o evento de compra</b>, entao vendas feitas pela Voomp nao entram nessa receita. So a Eduzz esta medindo venda. Vale instalar o pixel de compra na Voomp.';

  /* PRODUTOS */
  document.getElementById('prod-cards').innerHTML = D.produtos.map((p,i)=>{
    const pagesHtml = p.pages.map(pg=>{
      const pp = p.users ? (pg[1]/p.users*100) : 0;
      return `<div class="mrow"><div class="top"><span class="nm">${pg[0][0]}</span>`+
        `<span class="vl">${fmt(pg[1])} &middot; <b>${pp.toFixed(1)}%</b></span></div>`+
        `<div class="barwrap"><div class="bar" style="width:${pp.toFixed(1)}%"></div></div></div>`;
    }).join('');
    const co = p.checkout ? `<div><div class="n ac">${fmt(p.checkout)}</div><div class="l">Begin checkout</div></div>` : '';
    return `<div class="grid full"><div class="card" style="border-color:var(--ac)">`+
      `<h2 style="color:var(--ac)">${p.name}</h2>`+
      `<div class="bigstat"><div><div class="n">${fmt(p.users)}</div><div class="l">Visitantes</div></div>`+
      `<div><div class="n">${fmt(p.sessions)}</div><div class="l">Sessoes</div></div>${co}</div>`+
      (p.pages.length>1?`<div style="margin-top:16px"><div class="sub" style="margin-bottom:6px">Por pagina</div>${pagesHtml}</div>`:'')+
      `</div></div>`;
  }).join('');

  /* CONVERSAO */
  document.getElementById('conv-stats').innerHTML =
    `<div><div class="n ac">${D.consult.rate}%</div><div class="l">Taxa de conversao</div></div>`+
    `<div><div class="n">${fmt(D.consult.leads)}</div><div class="l">Leads (formularios)</div></div>`+
    `<div><div class="n">${fmt(D.consult.users)}</div><div class="l">Visitantes</div></div>`;
  document.getElementById('conv-funnel').innerHTML =
    `<div class="fstep"><span class="fn">Visitantes da pagina</span><span class="fv">${fmt(D.consult.users)}</span></div>`+
    `<div class="farrow">&#8595; ${D.consult.rate}% preenchem o formulario</div>`+
    `<div class="fstep gold"><span class="fn">Leads gerados</span><span class="fv">${fmt(D.consult.leads)}</span></div>`;
  const rc = document.getElementById('renda-card');
  if (D.consult.renda && D.consult.renda.length){
    rc.style.display='';
    barRows('renda-bars', D.consult.renda);
  } else { rc.style.display='none'; }
}
render(sel.value);
</script></body></html>"""

HTML = (HTML.replace("__PID__", PID).replace("__AGORA__", agora)
            .replace("__DATA__", json.dumps(PAYLOAD)))
with open(OUT, "w") as f:
    f.write(HTML)
print("Painel gerado:", OUT)
