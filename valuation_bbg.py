"""Snapshot Bloomberg para o summary de valuation — roda SO no seu PC, com o terminal aberto.

POR QUE EXISTE: o Yahoo cobre quase tudo, mas nao tem EBITDA 26E/27E nem ROIC de consenso.
Este script consulta a sua conta Bloomberg (Desktop API/blpapi, que so funciona na maquina
onde o terminal esta logado), grava bbg_snapshot.json e sobe para a pasta do Drive do
clipping. O robo diario usa o snapshot por ate 5 dias — depois volta a mostrar so o Yahoo.
Se voce nunca rodar isto, NADA quebra: o e-mail sai com a cadeia Yahoo+cache.

REQUISITOS (uma vez): terminal Bloomberg logado nesta maquina + `pip install blpapi
--index-url=https://blpapi.bloomberg.com/repository/releases/python/simple/`
Para subir ao Drive: variavel de ambiente GOOGLE_CREDENTIALS_JSON (o mesmo JSON do robo)
e DRIVE_FOLDER_ID. Sem elas, o arquivo fica so local (e voce sobe na mao se quiser).

USO:  python valuation_bbg.py            # consulta, grava e tenta subir ao Drive

ATENCAO: dados da sua licenca Bloomberg — o snapshot vai para o SEU Drive privado,
nunca para o repositorio publico (bbg_snapshot.json esta no .gitignore).

NAO TESTADO COM TERMINAL REAL — escrito contra a documentacao do blpapi //blp/refdata.
Se algum campo vier vazio no seu terminal, me chame com o log que ajusto os fields.
"""
import io
import json
import os
import sys
from datetime import date

import valuation

# ticker Yahoo -> ticker Bloomberg
def _bbg_ticker(tk):
    if tk.endswith(".SA"):
        return tk.replace(".SA", "") + " BZ Equity"
    return tk + " US Equity"


# campo nosso -> (field Bloomberg, override de periodo quando aplicavel)
FIELDS_SIMPLES = {
    "preco": "PX_LAST",
    "alvo": "BEST_TARGET_PRICE",
    "mktcap": "CUR_MKT_CAP",
    "adtv": "INTERVAL_AVG",                    # fallback: VOLUME_AVG_3M x preco
    "dl_ebitda": "NET_DEBT_TO_EBITDA",
    "roe": "RETURN_COM_EQY",
    "roic": "RETURN_ON_INV_CAPITAL",
    "ev_ebitda": "BEST_EV_TO_BEST_EBITDA",     # consenso forward
}
FIELDS_ESTIMATIVA = {                          # via BEST_FPERIOD_OVERRIDE
    "receita": "BEST_SALES",
    "ebitda": "BEST_EBITDA",
    "lucro": "BEST_NET_INCOME",
    "eps": "BEST_EPS",
}


def consultar(tickers):
    import blpapi
    opts = blpapi.SessionOptions()
    opts.setServerHost("localhost")
    opts.setServerPort(8194)
    ses = blpapi.Session(opts)
    if not ses.start() or not ses.openService("//blp/refdata"):
        raise RuntimeError("terminal Bloomberg nao esta acessivel (blpapi em localhost:8194)")
    svc = ses.getService("//blp/refdata")

    def _pedir(fields, override_fperiod=None):
        req = svc.createRequest("ReferenceDataRequest")
        for tk in tickers:
            req.getElement("securities").appendValue(_bbg_ticker(tk))
        for f in fields:
            req.getElement("fields").appendValue(f)
        if override_fperiod:
            ov = req.getElement("overrides").appendElement()
            ov.setElement("fieldId", "BEST_FPERIOD_OVERRIDE")
            ov.setElement("value", override_fperiod)
        ses.sendRequest(req)
        out = {}
        while True:
            ev = ses.nextEvent(30000)
            for msg in ev:
                if not msg.hasElement("securityData"):
                    continue
                sd = msg.getElement("securityData")
                for i in range(sd.numValues()):
                    row = sd.getValueAsElement(i)
                    sec = row.getElementAsString("security")
                    fd = row.getElement("fieldData")
                    vals = {}
                    for f in fields:
                        if fd.hasElement(f):
                            try:
                                vals[f] = fd.getElementAsFloat(f)
                            except Exception:
                                pass
                    out[sec] = vals
            if ev.eventType() == blpapi.Event.RESPONSE:
                break
        return out

    simples = _pedir(list(FIELDS_SIMPLES.values()))
    ano1 = _pedir(list(FIELDS_ESTIMATIVA.values()), "1FY")   # 2026E
    ano2 = _pedir(list(FIELDS_ESTIMATIVA.values()), "2FY")   # 2027E

    dados = {}
    for tk in tickers:
        sec = _bbg_ticker(tk)
        d = {}
        for nosso, bbg in FIELDS_SIMPLES.items():
            v = simples.get(sec, {}).get(bbg)
            if v is not None:
                d[nosso] = v
        for nosso, bbg in FIELDS_ESTIMATIVA.items():
            v1 = ano1.get(sec, {}).get(bbg)
            v2 = ano2.get(sec, {}).get(bbg)
            if v1 is not None:
                d[f"{nosso}_26e"] = v1 * (1e6 if nosso != "eps" else 1)  # BBG devolve em mi
            if v2 is not None:
                d[f"{nosso}_27e"] = v2 * (1e6 if nosso != "eps" else 1)
        if d.get("alvo") and d.get("preco"):
            d["total_return"] = (d["alvo"] / d["preco"] - 1) * 100
        if d.get("preco") and d.get("eps_26e"):
            d["pe_26e"] = d["preco"] / d["eps_26e"]
        dados[tk] = d
    ses.stop()
    return dados


def subir_drive(caminho):
    cred = os.environ.get("GOOGLE_CREDENTIALS_JSON")
    pasta = os.environ.get("DRIVE_FOLDER_ID")
    if not cred or not pasta:
        print("[bbg] GOOGLE_CREDENTIALS_JSON/DRIVE_FOLDER_ID ausentes — arquivo ficou so local")
        return
    from google.oauth2 import service_account
    from googleapiclient.discovery import build
    from googleapiclient.http import MediaFileUpload
    creds = service_account.Credentials.from_service_account_info(
        json.loads(cred), scopes=["https://www.googleapis.com/auth/drive"])
    svc = build("drive", "v3", credentials=creds, cache_discovery=False)
    q = (f"name='bbg_snapshot.json' and trashed=false")
    achados = svc.files().list(q=q, fields="files(id)").execute().get("files", [])
    media = MediaFileUpload(caminho, mimetype="application/json")
    if achados:
        svc.files().update(fileId=achados[0]["id"], media_body=media).execute()
        print("[bbg] snapshot atualizado no Drive")
    else:
        print("[bbg] crie um bbg_snapshot.json vazio na pasta do Drive uma unica vez "
              "(service account nao pode criar arquivos)")


if __name__ == "__main__":
    tickers = valuation.empresas_da_vertical("saude_educacao")
    print(f"[bbg] consultando {len(tickers)} tickers no terminal...")
    dados = consultar(tickers)
    snap = {"quando": date.today().isoformat(), "dados": dados}
    with io.open("bbg_snapshot.json", "w", encoding="utf-8") as fh:
        json.dump(snap, fh, ensure_ascii=False)
    ok = sum(1 for d in dados.values() if d)
    print(f"[bbg] {ok}/{len(tickers)} tickers com dados -> bbg_snapshot.json")
    subir_drive("bbg_snapshot.json")
