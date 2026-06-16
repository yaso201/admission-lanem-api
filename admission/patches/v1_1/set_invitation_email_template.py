"""Repointe les e-mails d'invitation/réinitialisation vers la page management LaNEM.

Frappe (send_welcome_mail_to_user / password_reset_mail) construit un `link` =
<host_api>/update-password?key=KEY et l'injecte dans le `custom_template`. On crée deux
Email Template LaNEM (FR, habillés) qui RECONSTRUISENT le lien vers le front management
(staff_portal_url)/update-password?key=KEY — donc la page habillée, pilotée par la clé,
sans le piège « connecté → ancien mot de passe » de la page Frappe brute. On les pose sur
System Settings.welcome_email_template / reset_password_template.

URL du front management : conf `staff_portal_url` (site_config), fallback recette. Le lien
est rebâti en Jinja à partir de `link` (extraction de la clé), donc indépendant du host API.
Config-as-code idempotente."""

import frappe

INVITATION_NAME = "LaNEM Invitation"
RESET_NAME = "LaNEM Reset"

# Bloc Jinja commun : extrait la clé de `link` (gère &password_expired=true) et bâtit l'URL management.
_KEY = ("{% set _k = (link.split('key=')[1].split('&')[0]) "
        "if link and 'key=' in link else '' %}")


def _shell(staff_url, title, intro, cta_label, outro):
    href = staff_url + "/update-password?key={{ _k }}"
    return f"""{_KEY}<!-- LaNEM -->
<div style="margin:0;padding:0;background:#F4F2EC;">
  <div style="max-width:480px;margin:0 auto;padding:24px 16px;font-family:Arial,Helvetica,sans-serif;color:#2B2A26;">
    <div style="text-align:center;padding-bottom:20px;">
      <img src="{staff_url}/lanem-logo.webp" alt="LaNEM" height="40"
           style="height:40px;width:auto;display:inline-block;border:0;" />
    </div>
    <div style="background:#FFFFFF;border:1px solid #E4E0D6;border-radius:14px;padding:28px 24px;">
      <h1 style="margin:0 0 14px;font-size:20px;line-height:1.3;color:#1A1830;font-weight:700;">{title}</h1>
      <p style="margin:0 0 18px;font-size:15px;line-height:1.6;color:#46443D;">Bonjour {{{{ first_name }}}},</p>
      <p style="margin:0 0 24px;font-size:15px;line-height:1.6;color:#46443D;">{intro}</p>
      <table role="presentation" cellpadding="0" cellspacing="0" style="margin:0 auto 24px;">
        <tr><td style="border-radius:9px;background:#1A1830;">
          <a href="{href}"
             style="display:inline-block;padding:13px 26px;font-size:15px;font-weight:700;color:#FFFFFF;text-decoration:none;border-radius:9px;">{cta_label}</a>
        </td></tr>
      </table>
      <p style="margin:0 0 8px;font-size:13px;line-height:1.55;color:#6B675D;">{outro}</p>
      <p style="margin:0;font-size:12px;line-height:1.55;color:#9A958A;word-break:break-all;">
        Si le bouton ne fonctionne pas, copiez ce lien dans votre navigateur :<br />
        <span style="color:#574F40;">{href}</span>
      </p>
    </div>
    <p style="text-align:center;margin:18px 0 0;font-size:11px;color:#9A958A;">
      LaNEM — Service des admissions · Ce message est automatique, merci de ne pas y répondre.
    </p>
  </div>
</div>"""


def execute():
    staff_url = (frappe.conf.get("staff_portal_url") or "https://staff-rec.lanem.bj").rstrip("/")

    templates = {
        INVITATION_NAME: dict(
            subject="Activez votre accès LaNEM",
            html=_shell(
                staff_url,
                "Bienvenue chez LaNEM",
                "Un accès à l'espace de gestion des admissions LaNEM a été créé pour vous. "
                "Pour l'activer, définissez votre mot de passe en cliquant ci-dessous.",
                "Définir mon mot de passe",
                "Ce lien est valable 24 heures. Passé ce délai, demandez un nouveau lien depuis la page de connexion.",
            ),
        ),
        RESET_NAME: dict(
            subject="Réinitialisation de votre mot de passe LaNEM",
            html=_shell(
                staff_url,
                "Réinitialisation du mot de passe",
                "Une réinitialisation de votre mot de passe LaNEM a été demandée. "
                "Choisissez un nouveau mot de passe en cliquant ci-dessous. "
                "Si vous n'êtes pas à l'origine de cette demande, ignorez ce message.",
                "Choisir un nouveau mot de passe",
                "Ce lien est valable 24 heures et ne peut être utilisé qu'une seule fois.",
            ),
        ),
    }

    for name, t in templates.items():
        if frappe.db.exists("Email Template", name):
            doc = frappe.get_doc("Email Template", name)
        else:
            doc = frappe.new_doc("Email Template")
            doc.name = name
        doc.subject = t["subject"]
        doc.use_html = 1
        doc.response_html = t["html"]
        doc.save(ignore_permissions=True)

    ss = frappe.get_single("System Settings")
    ss.welcome_email_template = INVITATION_NAME
    ss.reset_password_template = RESET_NAME
    ss.flags.ignore_mandatory = True
    ss.save(ignore_permissions=True)
    frappe.db.commit()
