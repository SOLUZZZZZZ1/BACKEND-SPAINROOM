
# SpainRoom — Voice Backend (ConversationRelay) — ES STABLE (Franquicia Routing)
# Start Command: uvicorn codigo_flask:app --host 0.0.0.0 --port $PORT --proxy-headers
#
# Ruteo franquicia:
#  - Detecta "franquicia/franquiciado" en cualquier momento.
#  - Pregunta si es FRANQUICIADO ACTUAL o INTERESADO en franquiciarse.
#  - Genera lead_type = 'franchise_prospect' | 'franchisee_support' y envía a:
#      ASSIGN_URL_EXPANSION (prospect)  |  ASSIGN_URL_SUPPORT (soporte)  |  fallback ASSIGN_URL.
#
import os, json, re, time, contextlib, hashlib
from typing import Dict, Any
from fastapi import FastAPI, Request, WebSocket, Header
from fastapi.responses import Response, JSONResponse, HTMLResponse
from xml.sax.saxutils import quoteattr

app = FastAPI(title="SpainRoom Voice — ConversationRelay (ES, Franquicia Routing)")

def _twiml(xml: str) -> Response: return Response(content=xml, media_type="application/xml")
def _env(k: str, default: str = "") -> str: return os.getenv(k, default)
def _host(req: Request) -> str: return req.headers.get("host") or req.url.hostname or "localhost"

async def _post_json(url: str, payload: dict, timeout: float = 2.0) -> None:
    import urllib.request
    try:
        req = urllib.request.Request(url, data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                                     headers={"Content-Type":"application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            _ = r.read()
    except Exception:
        pass

def _digits(t: str) -> str: return "".join(ch for ch in (t or "") if ch.isdigit())

@app.get("/health")
async def health(): return JSONResponse({"ok": True})

@app.get("/diag_runtime")
async def diag_runtime():
    keys = ["CR_TTS_PROVIDER","CR_LANGUAGE","CR_TRANSCRIPTION_LANGUAGE","CR_VOICE",
            "CR_WELCOME","SPEAK_SLEEP_MS","ASSIGN_URL","ASSIGN_URL_EXPANSION","ASSIGN_URL_SUPPORT"]
    return JSONResponse({k: _env(k) for k in keys})

@app.api_route("/voice/answer_cr", methods=["GET","POST"])
async def answer_cr(request: Request):
    host = _host(request)
    ws = f"wss://{host}/cr"
    lang = _env("CR_LANGUAGE","es-ES"); tr=_env("CR_TRANSCRIPTION_LANGUAGE",lang)
    prov=_env("CR_TTS_PROVIDER","Google"); voice=_env("CR_VOICE","es-ES-Standard-A")
    welcome=_env("CR_WELCOME","")
    attrs = [f"url={quoteattr(ws)}", f"language={quoteattr(lang)}", f"transcriptionLanguage={quoteattr(tr)}",
             f"ttsProvider={quoteattr(prov)}", 'interruptible="speech"', 'reportInputDuringAgentSpeech="none"']
    if welcome.strip(): attrs.append(f"welcomeGreeting={quoteattr(welcome.strip())}")
    if voice.strip():   attrs.append(f"voice={quoteattr(voice.strip())}")
    twiml = '<?xml version="1.0" encoding="UTF-8"?>\n<Response>\n  <Connect>\n    <ConversationRelay %s />\n  </Connect>\n</Response>' % (" ".join(attrs))
    return _twiml(twiml)

@app.post("/voice/fallback")
async def voice_fallback():
    return _twiml('<?xml version="1.0" encoding="UTF-8"?>\n<Response>\n  <Say language="es-ES">Disculpe. Estamos teniendo problemas. Inténtelo más tarde.</Say>\n</Response>')

# ---------------- WebSocket ConversationRelay (ES) ----------------
from fastapi import WebSocket

@app.websocket("/cr")
async def cr(ws: WebSocket):
    await ws.accept()
    COOLDOWN_MS = 1200
    session: Dict[str, Any] = {
        "step": "await_setup",
        "lead": {"role":"","poblacion":"","zona":"","nombre":"","telefono":""},
        "last_q": None, "last_q_ts": 0.0,
        "last_user": None, "last_user_ts": 0.0,
        # Franquicia
        "fr_mode": None,        # None|prospect|support
        "fr_categoria": "",     # pagos|contratos|crm|operativa|incidencia
        "fr_detalle": ""
    }
    def _now(): return time.monotonic()*1000.0
    def _norm(t): return re.sub(r"\s+"," ",(t or "").strip())

    async def speak(txt, interruptible=True):
        await ws.send_json({"type":"text","token":txt,"last":True,"interruptible":bool(interruptible)})
        try:
            import asyncio; await asyncio.sleep(int(_env("SPEAK_SLEEP_MS","0"))/1000.0)
        except Exception: pass

    def _dup_user(t):
        t=_norm(t).lower(); now=_now()
        if session["last_user"]==t and (now-session["last_user_ts"])<COOLDOWN_MS: return True
        session["last_user"]=t; session["last_user_ts"]=now; return False

    async def ask_once(key):
        now=_now()
        if session["last_q"]==key and (now-session["last_q_ts"])<COOLDOWN_MS: return
        session["last_q"]=key; session["last_q_ts"]=now
        prompts={
            "role":"Para atenderle: ¿Es usted propietario o inquilino?",
            "city":"¿En qué población está interesado?",
            "zone":"¿Qué zona o barrio?",
            "name":"¿Su nombre completo?",
            "phone":"¿Su teléfono de contacto, por favor?",
            "post":"¿Desea más información o ayuda?",
            "fr_type":"¿Es franquiciado actual o desea información de franquicia?",
            "fr_city":"¿En qué ciudad o zona desea operar?",
            "fr_exp":"¿Tiene experiencia inmobiliaria u operativa?",
            "fr_cat":"Motivo de soporte: pagos, contratos, CRM, operativa o incidencias?",
            "fr_det":"¿Detalle breve del caso?"
        }
        await speak(prompts.get(key,""))

    async def finish_normal():
        lead=session["lead"].copy()
        await speak("Gracias. Tomamos sus datos. Le contactaremos en breve.", interruptible=False)
        url = _env("ASSIGN_URL","")
        if url:
            try: await _post_json(url, lead, timeout=2.0)
            except Exception: pass
        print("<<LEAD>>"+json.dumps(lead, ensure_ascii=False)+"<<END>>", flush=True)
        session["step"]="post"; await ask_once("post")

    async def finish_franchise():
        # Construye payload según modo
        if session["fr_mode"]=="prospect":
            payload = {
                "lead_type":"franchise_prospect",
                "nombre": session["lead"].get("nombre",""),
                "telefono": session["lead"].get("telefono",""),
                "ciudad": session["lead"].get("poblacion",""),
                "zona": session["lead"].get("zona",""),
                "experiencia": _norm(session.get("fr_detalle",""))
            }
            url = _env("ASSIGN_URL_EXPANSION", _env("ASSIGN_URL",""))
            await speak("Gracias. Expansión le llamará en 24–48 horas.", interruptible=False)
        else:
            payload = {
                "lead_type":"franchisee_support",
                "nombre": session["lead"].get("nombre",""),
                "zona": session["lead"].get("zona",""),
                "email_corp":"",   # opcional por voz
                "telefono": session["lead"].get("telefono",""),
                "categoria": session.get("fr_categoria",""),
                "detalle": _norm(session.get("fr_detalle","")),
                "prioridad":"media"
            }
            url = _env("ASSIGN_URL_SUPPORT", _env("ASSIGN_URL",""))
            await speak("Gracias. Soporte franquiciados registra su caso hoy.", interruptible=False)
        if url:
            try: await _post_json(url, payload, timeout=2.0)
            except Exception: pass
        print("<<LEAD>>"+json.dumps(payload, ensure_ascii=False)+"<<END>>", flush=True)
        session["step"]="post"; await ask_once("post")

    def _is_help(tl:str)->bool:
        return any(w in tl for w in ["ayuda","asesor","llamar","contacto","por favor"])

    def _fr_hit(tl:str)->bool:
        return any(w in tl for w in ["franquic","royalty","licencia","zona","territor","expansion","expansión","soporte franquic","soy franquiciado"])

    async def handle(txt:str):
        if _dup_user(txt): return
        t=_norm(txt); tl=t.lower(); s=session["step"]; lead=session["lead"]

        # FRANQUICIA: detección en cualquier momento
        if _fr_hit(tl) and session["fr_mode"] is None:
            session["fr_mode"]="ask"  # pedir tipo
            await ask_once("fr_type"); return

        # Ayuda / escalado normal
        if _is_help(tl) and session["fr_mode"] is None:
            if not lead.get("telefono"):
                session["step"]="phone"; await speak("Para ayudarle ahora, ¿su teléfono de contacto?"); return
            await speak(f"De acuerdo. Un asesor le llamará al {lead['telefono']} en breve.")
            session["step"]="post"; await ask_once("post"); return

        # Flujo FRANQUICIA
        if session["fr_mode"]:
            if session["fr_mode"]=="ask":
                if "actual" in tl or "soy franquiciado" in tl:
                    session["fr_mode"]="support"; session["step"]="fr_cat"; await ask_once("fr_cat"); return
                if "informaci" in tl or "ser franquiciado" in tl or "abrir" in tl:
                    session["fr_mode"]="prospect"; session["step"]="fr_city"; await ask_once("fr_city"); return
                await ask_once("fr_type"); return
            if session["fr_mode"]=="prospect":
                if s=="fr_city":
                    if len(tl)>=2: lead["poblacion"]=t.title(); session["step"]="zone"; await ask_once("zone"); return
                    await ask_once("fr_city"); return
                if s=="zone":
                    if len(tl)>=2: lead["zona"]=t.title(); session["step"]="name"; await ask_once("name"); return
                    await ask_once("zone"); return
                if s=="name":
                    if len(t.split())>=2: lead["nombre"]=t; session["step"]="phone"; await ask_once("phone"); return
                    await speak("¿Su nombre completo, por favor?"); return
                if s=="phone":
                    d=_digits(t); 
                    if d.startswith("34") and len(d)>=11: d=d[-9:]
                    if len(d)==9 and d[0] in "6789": lead["telefono"]=d; session["step"]="fr_exp"; await ask_once("fr_exp"); return
                    await speak("¿Me facilita un teléfono de nueve dígitos?"); return
                if s=="fr_exp":
                    session["fr_detalle"]=t; await finish_franchise(); return
            if session["fr_mode"]=="support":
                if s=="fr_cat":
                    cats = ["pagos","contratos","crm","operativa","incidenc"]
                    for c in cats:
                        if c in tl: session["fr_categoria"]="pagos" if "pago" in tl else ("contratos" if "contrato" in tl else ("crm" if "crm" in tl else ("operativa" if "operat" in tl else "incidencia")))
                    session["step"]="fr_det"; await ask_once("fr_det"); return
                if s=="fr_det":
                    session["fr_detalle"]=t
                    # Asegurar teléfono
                    if not lead.get("telefono"):
                        session["step"]="phone"; await ask_once("phone"); return
                    await finish_franchise(); return
                if s=="phone":
                    d=_digits(t); 
                    if d.startswith("34") and len(d)>=11: d=d[-9:]
                    if len(d)>=9: lead["telefono"]=d; await finish_franchise(); return
                    await speak("¿Me facilita un teléfono de nueve dígitos?"); return
            # En cualquier otro caso dentro de franquicia, pide tipo
            await ask_once("fr_type"); return

        # Flujo normal de captación 5 campos
        if s=="role":
            if "propiet" in tl: lead["role"]="propietario"; session["step"]="city"; await speak("Gracias."); await ask_once("city"); return
            if "inquil" in tl or "alquil" in tl: lead["role"]="inquilino"; session["step"]="city"; await speak("Gracias."); await ask_once("city"); return
            await ask_once("role"); return
        if s=="city":
            if len(tl)>=2: lead["poblacion"]=t.title(); session["step"]="zone"; await ask_once("zone"); return
            await ask_once("city"); return
        if s=="zone":
            if len(tl)>=2: lead["zona"]=t.title(); session["step"]="name"; await ask_once("name"); return
            await ask_once("zone"); return
        if s=="name":
            if len(t.split())>=2: lead["nombre"]=t; session["step"]="phone"; await ask_once("phone"); return
            await speak("¿Su nombre completo, por favor?"); return
        if s=="phone":
            d=_digits(t); 
            if d.startswith("34") and len(d)>=11: d=d[-9:]
            if len(d)==9 and d[0] in "6789": lead["telefono"]=d; await finish_normal(); return
            await speak("¿Me facilita un teléfono de nueve dígitos?"); return
        if s=="post":
            await speak("¿Necesita algo más?"); return
        # await_setup: ignore

    try:
        while True:
            msg = await ws.receive_json()
            tp = msg.get("type")
            if tp=="setup": session["step"]="role"; await ask_once("role")
            elif tp=="prompt":
                txt = msg.get("voicePrompt","") or ""
                if msg.get("last", True) and txt: await handle(txt)
            elif tp=="interrupt":
                session["last_q_ts"] = _now()
            elif tp=="error":
                await ws.send_json({"type":"text","token":"Disculpe. Estamos teniendo problemas.","last":True,"interruptible":False}); break
    except Exception as e:
        print("CR ws error:", e, flush=True)
    finally:
        with contextlib.suppress(Exception): await ws.close()

# --- Optional: /assign passthrough (sigue disponible) ---
@app.post("/assign")
async def assign(payload: dict):
    zone_key = f"{(payload.get('poblacion') or payload.get('ciudad','') or '').strip().lower()}-{(payload.get('zona','') or '').strip().lower()}"
    fid = hashlib.sha1(zone_key.encode("utf-8")).hexdigest()[:10]
    task = {"title":"Contactar lead","zone_key":zone_key,"franchisee_id":fid,"lead":payload,"created_at":int(time.time())}
    return JSONResponse({"ok": True, "task": task})
