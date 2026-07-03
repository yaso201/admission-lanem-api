app_name = "admission"
app_title = "Admission"
app_publisher = "LaNEM"
app_description = "Socle back admission LaNEM"
app_email = "tech@lanem.local"
app_license = "mit"

# Apps
# ------------------

# required_apps = []

# Each item in the list will be shown as an app in the apps page
# add_to_apps_screen = [
# 	{
# 		"name": "admission",
# 		"logo": "/assets/admission/logo.png",
# 		"title": "Admission",
# 		"route": "/admission",
# 		"has_permission": "admission.api.permission.has_app_permission"
# 	}
# ]

# Includes in <head>
# ------------------

# include js, css files in header of desk.html
# app_include_css = "/assets/admission/css/admission.css"
# app_include_js = "/assets/admission/js/admission.js"

# include js, css files in header of web template
# web_include_css = "/assets/admission/css/admission.css"
# web_include_js = "/assets/admission/js/admission.js"

# include custom scss in every website theme (without file extension ".scss")
# website_theme_scss = "admission/public/scss/website"

# include js, css files in header of web form
# webform_include_js = {"doctype": "public/js/doctype.js"}
# webform_include_css = {"doctype": "public/css/doctype.css"}

# include js in page
# page_js = {"page" : "public/js/file.js"}

# include js in doctype views
# doctype_js = {"doctype" : "public/js/doctype.js"}
# doctype_list_js = {"doctype" : "public/js/doctype_list.js"}
# doctype_tree_js = {"doctype" : "public/js/doctype_tree.js"}
# doctype_calendar_js = {"doctype" : "public/js/doctype_calendar.js"}

# Svg Icons
# ------------------
# include app icons in desk
# app_include_icons = "admission/public/icons.svg"

# Home Pages
# ----------

# application home page (will override Website Settings)
# home_page = "login"

# website user home page (by Role)
# role_home_page = {
# 	"Role": "home_page"
# }

# Generators
# ----------

# automatically create page for each record of this doctype
# website_generators = ["Web Page"]

# Jinja
# ----------

# add methods and filters to jinja environment
# jinja = {
# 	"methods": "admission.utils.jinja_methods",
# 	"filters": "admission.utils.jinja_filters"
# }

# Installation
# ------------

# before_install = "admission.install.before_install"
after_install = "admission.install.after_install"

after_migrate = ["admission.api.legal.seed_legal_placeholders"]

# Uninstallation
# ------------

# before_uninstall = "admission.uninstall.before_uninstall"
# after_uninstall = "admission.uninstall.after_uninstall"

# Integration Setup
# ------------------
# To set up dependencies/integrations with other apps
# Name of the app being installed is passed as an argument

# before_app_install = "admission.utils.before_app_install"
# after_app_install = "admission.utils.after_app_install"

# Integration Cleanup
# -------------------
# To clean up dependencies/integrations with other apps
# Name of the app being uninstalled is passed as an argument

# before_app_uninstall = "admission.utils.before_app_uninstall"
# after_app_uninstall = "admission.utils.after_app_uninstall"

# Desk Notifications
# ------------------
# See frappe.core.notifications.get_notification_config

# notification_config = "admission.notifications.get_notification_config"

# Permissions
# -----------
# Permissions evaluated in scripted ways

permission_query_conditions = {
	"Admission Applicant": "admission.api.permissions.get_permission_query_conditions",
}

has_permission = {
	"Admission Applicant": "admission.api.permissions.has_permission",
}

# DocType Class
# ---------------
# Override standard doctype classes

# override_doctype_class = {
# 	"ToDo": "custom_app.overrides.CustomToDo"
# }

# Document Events
# ---------------

doc_events = {
	"Applicant Fee Payment": {
		"on_update": "admission.api.notify_uf.on_payment_update",
	},
	"Admission Applicant": {
		"on_update": "admission.api.notify_uf.on_applicant_abandon",
	},
	# PERF-1 : édition Desk d'un doctype catalogue/légal/session → invalide le cache catalogue
	# immédiatement (anti-périmé sans attendre la sync ni le TTL).
	"Admission Fee Catalog": {"on_update": "admission.api.public.invalidate_catalog_cache"},
	"Admission Scholarship Mirror": {"on_update": "admission.api.public.invalidate_catalog_cache"},
	"Admission Promotion Mirror": {"on_update": "admission.api.public.invalidate_catalog_cache"},
	"Admission Level Mirror": {"on_update": "admission.api.public.invalidate_catalog_cache"},
	"Admission Legal Document": {"on_update": "admission.api.public.invalidate_catalog_cache"},
	"Admission Session": {"on_update": "admission.api.public.invalidate_catalog_cache"},
}

# Scheduled Tasks
# ---------------

scheduler_events = {
	"daily": [
		"admission.api.fee_catalog_sync.sync_fee_catalog",
		"admission.api.scholarship_sync.sync_scholarship_catalog",
		"admission.api.level_sync.sync_levels",
		# Catalogue (campus = source de vérité ; dormant tant que campus non configuré).
		"admission.api.catalogue_sync.sync_catalogue",
		"admission.api.retention.scheduled_retention_run",
		"admission.api.notify_uf.redrive_uf_notifications",
		"admission.api.public.expire_stale_online_pending",
		# LOT M (M9) : relance SOP J+7 et préavis J-7 avant purge des brouillons.
		"admission.api.notifications.remind_dormant_sop_dossiers",
		"admission.api.retention.notify_expiring_drafts",
		# RAPPELS-J4J6 : rappels candidat J+4 et J+6 après le récap pièces (si non re-soumis).
		"admission.api.notifications.send_pieces_reminders",
		# LOT P4 : rattrapage du pont INS (un étudiant non créé côté campus n'est plus un silence).
		"admission.api.bridge.redrive_bridge_notifications",
	],
}

# Testing
# -------

# before_tests = "admission.install.before_tests"

# Overriding Methods
# ------------------------------
#
# override_whitelisted_methods = {
# 	"frappe.desk.doctype.event.event.get_events": "admission.event.get_events"
# }
#
# each overriding function accepts a `data` argument;
# generated from the base implementation of the doctype dashboard,
# along with any modifications made in other Frappe apps
# override_doctype_dashboards = {
# 	"Task": "admission.task.get_dashboard_data"
# }

# exempt linked doctypes from being automatically cancelled
#
# auto_cancel_exempted_doctypes = ["Auto Repeat"]

# Ignore links to specified DocTypes when deleting documents
# -----------------------------------------------------------

# ignore_links_on_delete = ["Communication", "ToDo"]

# Request Events
# ----------------
# before_request = ["admission.utils.before_request"]
# HEAD-1 : headers de sécurité (défense en profondeur, valeurs configurables via site_config
# admission_security_headers ; CSP par défaut sûre, HSTS https-only). Voir security_headers.py.
after_request = ["admission.security_headers.set_security_headers"]

# Job Events
# ----------
# before_job = ["admission.utils.before_job"]
# after_job = ["admission.utils.after_job"]

# User Data Protection
# --------------------

# DAT-1 : champs PII à anonymiser — déclaration native Frappe = SOURCE DE VÉRITÉ (lue par
# admission.api.retention._pii_fields). Carve-out : seul Admission Applicant est déclaré ;
# Consent Record (preuve art.29) et Applicant Fee Payment (compta OHADA) NON déclarés → préservés.
# L'anonymisation est déclenchée côté admission (api.retention.anonymize_applicant) car le
# workflow natif Personal Data Deletion Request exige un compte User (candidats = guests).
user_data_fields = [
	{
		"doctype": "Admission Applicant",
		"filter_by": "email",
		# LOT G : + applicant_name (nom complet stocké — la PII survivait à l'anonymisation)
		# AUDIT-RECETTE : + motifs LIBRES (saisie staff — PII potentielle : « contacter au 97… »)
		"redact_fields": ["first_name", "last_name", "applicant_name", "email", "phone", "bac_date",
		                  "motif_incompletude", "motif_refus", "motif_desistement"],
		"partial": 1,
	},
]

# Authentication and authorization
# --------------------------------

# auth_hooks = [
# 	"admission.auth.validate"
# ]

# Automatically update python controller files with type annotations for this app.
# export_python_type_annotations = True

# default_log_clearing_doctypes = {
# 	"Logging DocType Name": 30  # days to retain logs
# }

# Translation
# ------------
# List of apps whose translatable strings should be excluded from this app's translations.
# ignore_translatable_strings_from = []

