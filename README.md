# Portal de Requerimientos SIP

Portal institucional de requerimientos para la Red de Colegios SIP, que
reemplaza el formulario único que ofrece el plan de Freshdesk por
**formularios dinámicos según el tipo de requerimiento**, autenticado
con cuentas Google institucionales y con creación automática de
tickets en Freshdesk vía API.

## El problema que resuelve

Freshdesk, en el plan contratado, solo permite un formulario de ticket
genérico para todos los tipos de solicitud. En una organización con
19 colegios y roles muy distintos (docentes, administrativos,
soporte técnico, direcciones), eso significa pedirle a todos que
llenen los mismos campos genéricos sin importar si el problema es una
impresora, un cambio de política de red o un error en una plataforma
interna. Este portal separa el flujo por categoría, muestra solo los
campos relevantes a cada una, y arma el ticket en Freshdesk con los
custom fields ya completos y consistentes — reduciendo el ida y vuelta
de "faltan datos" que generaba el formulario único.

## Autenticación

- Login exclusivo vía Google OAuth2 (Authorization Code flow + PKCE,
  usando `code_verifier`), restringido al dominio `@sip.cl` mediante
  el parámetro `hd` de Google y una verificación explícita del email
  devuelto por el ID token.
- Cookies de sesión `Secure`, `HttpOnly` y `SameSite=Lax`, con
  `ProxyFix` para que Flask reconozca correctamente el esquema HTTPS
  detrás del proxy de Cloud Run.
- Detección de entorno (`K_SERVICE`) para forzar HTTPS en la URL de
  callback solo en producción, sin romper el flujo en desarrollo local.

## Formularios dinámicos

Cuatro categorías, cada una con su propio formulario y set de campos:

- **Políticas de Navegación** — colegio, dirección MAC (validada con
  regex antes de enviar) y política de red a aplicar.
- **Soporte TI** — impresoras, conectividad, cuentas Google, equipamiento.
- **Datos y Reportes** — solicitud de reportes, errores en datos,
  actualización de información.
- **Aplicaciones y Sistemas** — soporte para plataformas internas
  (Corrector SIP, OYRSIP, PTD, Alexia, Softland, entre otras), cada
  una con sus propios sub-tipos de incidencia.

El árbol de sistemas y sub-tipos vive en un diccionario en el propio
código (`SISTEMAS`), lo que hace trivial agregar una plataforma o
categoría nueva sin tocar las plantillas HTML.

## Integración con Freshdesk

Cada envío arma un ticket vía `POST /api/v2/tickets` con:
- `type` distinto según la categoría (Incidencia, Solicitud de Mejora,
  Nueva Aplicación, Políticas de Navegación).
- `custom_fields` específicos por categoría (sede, MAC, política de
  navegación, sistema, tipo de requerimiento).
- Redirección a una página de confirmación con el **ID real del
  ticket** devuelto por Freshdesk, no un mensaje genérico de "enviado".

## Stack

- Flask + `google-auth-oauthlib` + `google-auth`
- Freshdesk API v2 (autenticación Basic con API key)
- Cloud Run (detección de entorno vía `K_SERVICE`, `ProxyFix` para
  HTTPS detrás de proxy)
- `python-dotenv` para configuración local

## Variables de entorno

```
FLASK_SECRET_KEY=
GOOGLE_CLIENT_ID=
GOOGLE_CLIENT_SECRET=
FRESHDESK_API_KEY=
FRESHDESK_SUBDOMAIN=sip
PORT=5000
```

## Correr local

```bash
pip install -r requirements.txt
python app.py
```

## Estado conocido / pendiente

- El mapeo `OU_TO_COLEGIO` (unidad organizacional → colegio) está
  definido pero no conectado todavía al flujo de login: hoy el
  formulario de Políticas de Navegación muestra los 19 colegios a
  todos los usuarios en vez de filtrar por el colegio del usuario
  autenticado. Queda como mejora pendiente conectar ese mapeo al
  resultado del login de Google.
