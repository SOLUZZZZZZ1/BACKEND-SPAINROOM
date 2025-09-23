# codigo_flask.py — VOZ (Flask) + TwiML
import os
from flask import Flask, request, jsonify, Response
from flask_cors import CORS

def env(k, d=""): return os.getenv(k, d)

def _twiml_cr():
    ws_url     = env("VOICE_WS_URL", "").strip() or "wss://INVALID-WS-URL"
    lang       = env("CR_LANGUAGE", "es-ES").strip()
    trans_lang = env("CR_TRANSCRIPTION_LANGUAGE", lang).strip()
    tts        = env("CR_TTS_PROVIDER", "Google").strip()
    voice      = env("CR_VOICE", "").strip()
    welcome    = env("CR_WELCOME", "").strip()

    attrs = [
        f'url="{ws_url}"',
        f'language="{lang}"',
        f'transcriptionLanguage="{trans_lang}"',
        f'ttsProvider="{tts}"',
        'interruptible="speech"',
        'reportInputDuringAgentSpeech="none"'
    ]
    if welcome: attrs.append(f'welcomeGreeting="{welcome}"')
    if voice:   attrs.append(f'voice="{voice}"')

    return f'''<?xml version="1.0" encoding="UTF-8"?>
<Response>
  <Connect>
    <ConversationRelay {' '.join(attrs)} />
  </Connect>
</Response>'''

def create_app():
    app = Flask(__name__)
    CORS(app, resources={r"/*": {"origins": ["*"]}}, supports_credentials=True)

    @app.get("/health")
    def health(): 
        return jsonify(ok=True)

    @app.get("/diag_runtime")
    def diag(): 
        keys = ["VOICE_WS_URL","CR_LANGUAGE","CR_TRANSCRIPTION_LANGUAGE","CR_TTS_PROVIDER","CR_VOICE","CR_WELCOME"]
        return jsonify({k: env(k, "") for k in keys})

    @app.route("/voice/answer_cr", methods=["GET","POST"])
    def voice_answer(): 
        return Response(_twiml_cr(), mimetype="application/xml")

    @app.route("/voice/fallback", methods=["GET","POST"])
    def voice_fallback(): 
        return Response(_twiml_cr(), mimetype="application/xml")

    @app.post("/webhooks/stripe")
    def stripe_webhook():
        secret = env("STRIPE_WEBHOOK_SECRET", "")
        if not secret: return jsonify(ok=False, error="missing_webhook_secret"), 500
        try: import stripe
        except Exception: return jsonify(ok=False, error="stripe_sdk_not_installed"), 500

        payload = request.get_data(cache=False, as_text=False)
        sig_hdr = request.headers.get("Stripe-Signature", "")
        try:
            event = stripe.Webhook.construct_event(payload=payload, sig_header=sig_hdr, secret=secret)
        except Exception as e:
            return jsonify(ok=False, error="invalid_signature", message=str(e)), 400

        app.logger.info("Stripe event: %s id=%s", event.get("type",""), event.get("id",""))
        return jsonify(ok=True)

    return app

if __name__ == "__main__":
    app = create_app()
    app.run(host="127.0.0.1", port=int(os.getenv("PORT", "5005")), debug=True)
