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

# Necesario para que Flask detecte HTTPS correctamente detrás de Cloud Run
app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1, x_host=1)

# Configuración de cookies de sesión para Cloud Run
app.config.update(
    SESSION_COOKIE_SECURE=True,        # Solo HTTPS en producción
    SESSION_COOKIE_HTTPONLY=True,      # No accesible desde JS
    SESSION_COOKIE_SAMESITE="Lax",     # Protección CSRF básica
    PERMANENT_SESSION_LIFETIME=3600,   # Sesión dura 1 hora
)

# ---------------------------------------------------------------------------
# CONFIG
# ---------------------------------------------------------------------------
GOOGLE_CLIENT_ID     = os.getenv("GOOGLE_CLIENT_ID", "")
GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET", "")
FRESHDESK_API_KEY    = os.getenv("FRESHDESK_API_KEY", "")
FRESHDESK_SUBDOMAIN  = os.getenv("FRESHDESK_SUBDOMAIN", "sip")
ALLOWED_DOMAIN       = "sip.cl"

# Cloud Run siempre define K_SERVICE — usamos esto para detectar producción
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
def get_ou_from_path(ou_path: str) -> str:
    if not ou_path:
        return ""
    parts = [p for p in ou_path.strip("/").split("/") if p]
    for part in parts:
        if part in OU_TO_COLEGIO:
            return part
    return ""

def get_colegios_for_user(ou_path: str) -> list:
    ou = get_ou_from_path(ou_path)
    return OU_TO_COLEGIO.get(ou, [])

def freshdesk_headers():
    token = base64.b64encode(f"{FRESHDESK_API_KEY}:X".encode()).decode()
    return {"Authorization": f"Basic {token}", "Content-Type": "application/json"}

def get_callback_url():
    """
    Retorna la URL de callback correcta según el entorno.
    En producción (Cloud Run) siempre usa https://.
    En local usa http://.
    """
    if IS_PRODUCTION:
        return url_for("callback", _external=True, _scheme="https")
    return url_for("callback", _external=True)

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
        scopes=SCOPES,
    )
    flow.redirect_uri = get_callback_url()
    authorization_url, state = flow.authorization_url(
        access_type="offline",
        hd=ALLOWED_DOMAIN,  # ← sacamos include_granted_scopes
    )
    session["oauth_state"] = state
    print(f"[LOGIN] Redirigiendo a Google OAuth, state={state}")
    return redirect(authorization_url)

@app.route("/callback")
def callback():
    try:
        os.environ["OAUTHLIB_RELAX_TOKEN_SCOPE"] = "1"

        state_recibido = request.args.get("state")
        print(f"[CALLBACK] state recibido={state_recibido}")
        print(f"[CALLBACK] IS_PRODUCTION={IS_PRODUCTION}")
        print(f"[CALLBACK] callback_url={get_callback_url()}")

        # Tomar el state directamente de la URL del callback
        # Resuelve el problema de múltiples instancias en Cloud Run
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
            scopes=SCOPES,
            state=state_recibido,
        )
        flow.redirect_uri = get_callback_url()

        # En Cloud Run el request llega como http:// por el proxy interno
        # pero el redirect_uri registrado en Google es https://
        auth_response = request.url
        print(f"[CALLBACK] auth_response original={auth_response[:80]}")
        if IS_PRODUCTION and auth_response.startswith("http://"):
            auth_response = auth_response.replace("http://", "https://", 1)
            print(f"[CALLBACK] auth_response corregido={auth_response[:80]}")

        flow.fetch_token(authorization_response=auth_response)
        print("[CALLBACK] Token obtenido correctamente")

        credentials = flow.credentials
        id_info = id_token.verify_oauth2_token(
            credentials.id_token,
            google_requests.Request(),
            GOOGLE_CLIENT_ID,
        )
        print(f"[CALLBACK] id_info obtenido, email={id_info.get('email')}")

        email = id_info.get("email", "")
        if not email.endswith(f"@{ALLOWED_DOMAIN}"):
            print(f"[CALLBACK] Dominio no permitido: {email}")
            return render_template("login.html", error="Solo se permiten cuentas @sip.cl")

        # Sesión permanente para que persista entre requests
        session.permanent = True
        session["user"] = {
            "email": email,
            "name": id_info.get("name", email.split("@")[0]),
            "picture": id_info.get("picture", ""),
            "colegios": [],
        }
        print(f"[OK] Sesión creada para {email}, redirigiendo a /categories")
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
        print("[WARN] /categories sin sesión, redirigiendo a login")
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
        "custom_fields": {
            "cf_sistema": sistema,
            "cf_tipo_de_requerimiento": tipo,
            "cf_detalle": detalle,
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

# ---------------------------------------------------------------------------
# ARRANQUE LOCAL
# En Cloud Run este bloque es ignorado automáticamente.
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    os.environ["OAUTHLIB_INSECURE_TRANSPORT"] = "1"  # solo desarrollo local
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=True)