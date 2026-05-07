import os
import re
import json
import base64
import traceback
import requests
from flask import Flask, render_template, redirect, url_for, session, request, jsonify
from google.oauth2 import id_token
from google_auth_oauthlib.flow import Flow
from google.auth.transport import requests as google_requests
from werkzeug.middleware.proxy_fix import ProxyFix
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET_KEY", "dev-secret-key-cambiar-en-produccion")

app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1, x_host=1)

app.config.update(
    SESSION_COOKIE_SECURE=True,
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    PERMANENT_SESSION_LIFETIME=3600,
)

# ---------------------------------------------------------------------------
# CONFIG
# ---------------------------------------------------------------------------
GOOGLE_CLIENT_ID     = os.getenv("GOOGLE_CLIENT_ID", "")
GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET", "")
FRESHDESK_API_KEY    = os.getenv("FRESHDESK_API_KEY", "")
FRESHDESK_SUBDOMAIN  = os.getenv("FRESHDESK_SUBDOMAIN", "sip")
ALLOWED_DOMAIN       = "sip.cl"

IS_PRODUCTION = os.getenv("K_SERVICE") is not None

SCOPES = [
    "openid",
    "https://www.googleapis.com/auth/userinfo.email",
    "https://www.googleapis.com/auth/userinfo.profile",
]

# ---------------------------------------------------------------------------
# MAPEO OU → COLEGIO(S)
# ---------------------------------------------------------------------------
OU_TO_COLEGIO = {
    "AML":      ["Arturo Matte Larrain Basica", "Arturo Matte Larrain Media"],
    "ATA":      ["Arturo Toro Amor"],
    "CM":       ["Claudio Matte Perez"],
    "EHM":      ["Elvira Hurtado de Matte"],
    "EMO":      ["Eliodoro Matte Ossa"],
    "FA":       ["Francisco Arriaran"],
    "FO":       ["Francisco Olea"],
    "GM":       ["Guillermo Matta"],
    "IHM":      ["Instituto Hermanos Matte"],
    "JAA":      ["Jose Agustin Alfonso"],
    "JAR":      ["Jorge Alessandri Rodriguez"],
    "JJP":      ["Jose Joaquin Prieto"],
    "LBI":      ["Liceo Bicentenario Italia"],
    "LN":       ["Los Nogales"],
    "PA":       ["Presidente Alessandri"],
    "REM":      ["Rosa Elvira Matte"],
    "RSL":      ["Rafael Sanhueza Lizardi"],
    "Phillips": ["Phillips"],
}

TODOS_LOS_COLEGIOS = [
    "Arturo Matte Larrain Basica", "Arturo Matte Larrain Media",
    "Arturo Toro Amor", "Claudio Matte Perez", "Elvira Hurtado de Matte",
    "Eliodoro Matte Ossa", "Francisco Arriaran", "Francisco Olea",
    "Guillermo Matta", "Instituto Hermanos Matte", "Jose Agustin Alfonso",
    "Jorge Alessandri Rodriguez", "Jose Joaquin Prieto", "Liceo Bicentenario Italia",
    "Los Nogales", "Presidente Alessandri", "Rosa Elvira Matte",
    "Rafael Sanhueza Lizardi", "Phillips",
]

POLITICAS_NAVEGACION = [
    "Colegio - Auxiliares", "Colegio - Visitas", "Colegio - Comunicaciones",
    "Colegio - Tablet", "Colegio - Soporte", "Colegio - Bibliotecas",
    "Colegio - Directores", "Colegio - Docentes", "Colegio - Movil",
    "Colegio - Administradores", "Colegio - Secretarias", "Colegio - Inspectores",
    "Colegio - Laboratorios", "Colegio - Docentes Chromebook",
    "SIP - Central", "SIP - Asesores", "Colegio - Alumnos", "Colegio - Administrativos",
]

SISTEMAS = {
    "Aplicaciones": {
        "Corrector SIP": ["Problema en el proceso", "Reportería", "Reproceso de información", "Otro"],
        "OYRSIP": ["Consulta de procesos", "Cursos incorrectos", "Falta docente", "Falta Lider",
                   "Graficos datastudio incorrecto", "No envía PDF a docente",
                   "No guarda observacion y/o retroalimentación", "Problema al cerrar observación", "Otro"],
        "PTD": ["Duda Proceso", "Problema Plataforma", "Otro"],
        "DL interno": [], "Alexia": [], "Anotate en la lista": [],
        "Softland": [], "Imagestion": [], "Registro de visitas": [], "Gestión Classroom": [],
    },
    "Compras e Insumos": {
        "Hardware": ["Solicitud de compra", "Cotización", "Reposición de insumos", "Estado de compra", "Otro"],
        "Software": ["Solicitud de compra", "Cotización", "Estado de compra", "Otro"],
        "Impresoras": ["Solicitud de compra", "Cotización", "Reposición de insumos", "Estado de compra", "Otro"],
    },
    "Datos y Reportes": {
        "Solicitud de reporte": [], "Error en datos": [],
        "Actualización de información": [], "Validación de datos": [], "Otro": [],
    },
    "Soporte TI": {
        "Impresoras": [], "Mantención de infraestructuras": [],
        "Conectividad y Redes": [],
        "Google": ["Cuentas", "Movimiento Classroom", "Permisos"],
        "Anexos": [], "Equipamiento": [],
    },
    "Operaciones": {"Soporte General": []},
    "Otro": {"Consulta general": [], "Solicitud no clasificada": [], "Otro": []},
}

# ---------------------------------------------------------------------------
# HELPERS
# ---------------------------------------------------------------------------
def freshdesk_headers():
    token = base64.b64encode(f"{FRESHDESK_API_KEY}:X".encode()).decode()
    return {"Authorization": f"Basic {token}", "Content-Type": "application/json"}

def get_callback_url():
    if IS_PRODUCTION:
        return url_for("callback", _external=True, _scheme="https")
    return url_for("callback", _external=True)

def make_flow(state=None, code_verifier=None):
    kwargs = {"scopes": SCOPES}
    if state:
        kwargs["state"] = state

    flow = Flow.from_client_config(
        {
            "web": {
                "client_id": GOOGLE_CLIENT_ID,
                "client_secret": GOOGLE_CLIENT_SECRET,
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
                "redirect_uris": [get_callback_url()],
            }
        },
        **kwargs,
    )

    # Si se pasa un code_verifier existente, restaurarlo (callback)
    # Si no, el flow generará uno nuevo automáticamente (login)
    if code_verifier is not None:
        flow.code_verifier = code_verifier

    return flow

# ---------------------------------------------------------------------------
# AUTH
# ---------------------------------------------------------------------------
@app.route("/")
def index():
    if "user" in session:
        return redirect(url_for("categories"))
    return redirect(url_for("login"))

@app.route("/login")
def login():
    if "user" in session:
        return redirect(url_for("categories"))
    return render_template("login.html")

@app.route("/login/google")
def login_google():
    flow = make_flow()
    flow.redirect_uri = get_callback_url()
    authorization_url, state = flow.authorization_url(
        access_type="offline",
        hd=ALLOWED_DOMAIN,
        prompt="select_account",
    )
    # Guardar state Y code_verifier en sesión para usarlos en el callback
    session.permanent = True
    session["oauth_state"]    = state
    session["code_verifier"]  = flow.code_verifier
    print(f"[LOGIN] state={state}")
    print(f"[LOGIN] code_verifier={'presente' if flow.code_verifier else 'ausente'}")
    return redirect(authorization_url)

@app.route("/callback")
def callback():
    try:
        os.environ["OAUTHLIB_RELAX_TOKEN_SCOPE"] = "1"

        state         = request.args.get("state")
        code          = request.args.get("code")
        code_verifier = session.get("code_verifier")

        print(f"[CALLBACK] state={state}")
        print(f"[CALLBACK] code presente={'si' if code else 'NO'}")
        print(f"[CALLBACK] code_verifier en sesión={'presente' if code_verifier else 'AUSENTE'}")
        print(f"[CALLBACK] IS_PRODUCTION={IS_PRODUCTION}")
        print(f"[CALLBACK] callback_url={get_callback_url()}")

        # Restaurar el flow con el state y code_verifier de la sesión
        flow = make_flow(state=state, code_verifier=code_verifier)
        flow.redirect_uri = get_callback_url()

        # Reconstruir auth_response con query_string exacto
        auth_response = get_callback_url() + "?" + request.query_string.decode("utf-8")
        print(f"[CALLBACK] auth_response={auth_response[:200]}")

        try:
            flow.fetch_token(authorization_response=auth_response)
            print("[CALLBACK] fetch_token OK")
        except Exception as token_err:
            print(f"[ERROR] fetch_token falló: {token_err}")
            print(traceback.format_exc())
            raise

        credentials = flow.credentials
        if not credentials or not credentials.token:
            raise Exception("credentials vacías después de fetch_token")

        print("[CALLBACK] token obtenido OK")

        id_info = id_token.verify_oauth2_token(
            credentials.id_token,
            google_requests.Request(),
            GOOGLE_CLIENT_ID,
        )

        email = id_info.get("email", "")
        print(f"[CALLBACK] email={email}")

        if not email.endswith(f"@{ALLOWED_DOMAIN}"):
            return render_template("login.html", error="Solo se permiten cuentas @sip.cl")

        session.permanent = True
        session["user"] = {
            "email": email,
            "name": id_info.get("name", email.split("@")[0]),
            "picture": id_info.get("picture", ""),
            "colegios": [],
        }
        # Limpiar datos OAuth de la sesión
        session.pop("oauth_state", None)
        session.pop("code_verifier", None)

        print(f"[OK] Sesión creada para {email}")
        return redirect(url_for("categories"))

    except Exception as e:
        print(f"[ERROR] OAuth callback falló: {e}")
        print(traceback.format_exc())
        return render_template("login.html", error="Error al iniciar sesión. Intenta nuevamente.")

@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))

# ---------------------------------------------------------------------------
# PORTAL
# ---------------------------------------------------------------------------
@app.route("/categories")
def categories():
    if "user" not in session:
        print("[WARN] /categories sin sesión")
        return redirect(url_for("login"))
    return render_template("categories.html", user=session["user"])

@app.route("/form/navegacion")
def form_navegacion():
    if "user" not in session:
        return redirect(url_for("login"))
    user = session["user"]
    colegios = user.get("colegios") or TODOS_LOS_COLEGIOS
    return render_template("form_navegacion.html", user=user,
                           colegios=colegios, politicas=POLITICAS_NAVEGACION)

@app.route("/form/soporte")
def form_soporte():
    if "user" not in session:
        return redirect(url_for("login"))
    filtrados = {k: SISTEMAS[k] for k in ["Soporte TI", "Compras e Insumos", "Otro"]}
    return render_template("form_general.html", user=session["user"],
        categoria="soporte", titulo="Soporte Técnico",
        subtitulo="Complete los detalles para informar un problema técnico.",
        sistemas_filtrados=list(filtrados.keys()),
        sistemas_json=json.dumps(filtrados))

@app.route("/form/datos")
def form_datos():
    if "user" not in session:
        return redirect(url_for("login"))
    filtrados = {k: SISTEMAS[k] for k in ["Datos y Reportes"]}
    return render_template("form_general.html", user=session["user"],
        categoria="datos", titulo="Datos y Reportes",
        subtitulo="Solicita acceso a datos, reportes personalizados o corrección de información.",
        sistemas_filtrados=list(filtrados.keys()),
        sistemas_json=json.dumps(filtrados))

@app.route("/form/sistemas")
def form_sistemas():
    if "user" not in session:
        return redirect(url_for("login"))
    filtrados = {k: SISTEMAS[k] for k in ["Aplicaciones", "Operaciones", "Otro"]}
    return render_template("form_general.html", user=session["user"],
        categoria="sistemas", titulo="Aplicaciones y Sistemas",
        subtitulo="Soporte para software institucional, errores en plataformas y solicitudes de acceso.",
        sistemas_filtrados=list(filtrados.keys()),
        sistemas_json=json.dumps(filtrados))

@app.route("/success")
def success():
    if "user" not in session:
        return redirect(url_for("login"))
    return render_template("success.html", user=session["user"],
                           ticket_id=request.args.get("ticket_id", ""))

# ---------------------------------------------------------------------------
# SUBMIT → FRESHDESK
# ---------------------------------------------------------------------------
@app.route("/submit/navegacion", methods=["POST"])
def submit_navegacion():
    if "user" not in session:
        return jsonify({"error": "no autenticado"}), 401

    user     = session["user"]
    colegio  = request.form.get("colegio", "").strip()
    mac      = request.form.get("mac", "").strip()
    politica = request.form.get("politica", "").strip()
    desc     = request.form.get("descripcion", "").strip()

    mac_re = re.compile(r"^([0-9A-Fa-f]{2}[:\-]){5}[0-9A-Fa-f]{2}$")
    if not mac_re.match(mac):
        return jsonify({"error": "MAC inválida"}), 400

    mac = mac.lower().replace("-", ":")

    payload = {
        "subject": f"Cambio de Política de Navegación - {colegio}",
        "description": desc or f"Solicitud de cambio de política de navegación para dispositivo {mac} en {colegio}.",
        "email": user["email"],
        "type": "Politicas de Navegación",
        "priority": 1, "status": 2,
        "custom_fields": {
            "cf_sede": colegio,
            "cf_mac_address": mac,
            "cf_politicas_de_navegacin": politica,
            "cf_sistema": "Soporte TI",
            "cf_tipo_de_requerimiento": "Conectividad y Redes",
        },
    }

    resp = requests.post(
        f"https://{FRESHDESK_SUBDOMAIN}.freshdesk.com/api/v2/tickets",
        headers=freshdesk_headers(), json=payload
    )
    if resp.status_code in (200, 201):
        return redirect(url_for("success", ticket_id=resp.json().get("id")))
    print(f"[ERROR] Freshdesk: {resp.status_code} {resp.text}")
    return jsonify({"error": "Error al crear ticket", "detalle": resp.text}), 502

@app.route("/submit/general", methods=["POST"])
def submit_general():
    if "user" not in session:
        return jsonify({"error": "no autenticado"}), 401

    user      = session["user"]
    asunto    = request.form.get("asunto", "").strip()
    sistema   = request.form.get("sistema", "").strip()
    tipo      = request.form.get("tipo", "").strip()
    detalle   = request.form.get("detalle", "").strip()
    desc      = request.form.get("descripcion", "").strip()
    categoria = request.form.get("categoria", "soporte")

    tipo_map = {"soporte": "Incidencia", "datos": "Solicitud de Mejora", "sistemas": "Nueva Aplicación"}

    payload = {
        "subject": asunto or f"Requerimiento {sistema} - {tipo}",
        "description": desc or f"Sistema: {sistema}\nTipo: {tipo}\nDetalle: {detalle}",
        "email": user["email"],
        "type": tipo_map.get(categoria, "Incidencia"),
        "priority": 1, "status": 2,
        "custom_fields": {k: v for k, v in {
            "cf_sistema": sistema,
            "cf_tipo_de_requerimiento": tipo,
            "cf_detalle": detalle,
        }.items() if v},
    }

    resp = requests.post(
        f"https://{FRESHDESK_SUBDOMAIN}.freshdesk.com/api/v2/tickets",
        headers=freshdesk_headers(), json=payload
    )
    if resp.status_code in (200, 201):
        return redirect(url_for("success", ticket_id=resp.json().get("id")))
    print(f"[ERROR] Freshdesk: {resp.status_code} {resp.text}")
    return jsonify({"error": "Error al crear ticket", "detalle": resp.text}), 502

# ---------------------------------------------------------------------------
# ARRANQUE LOCAL
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    os.environ["OAUTHLIB_INSECURE_TRANSPORT"] = "1"
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=True)