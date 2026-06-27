import frappe


ROLES = [
	"Admission Administratif",
	"Admission Responsable",
	"Admission Direction",
]

STATES = [
	("BRO", "Warning", "Admission Administratif"),
	("SOP", "Warning", "Admission Administratif"),
	("SOU", "Info", "Admission Administratif"),
	("INC", "Danger", "Admission Administratif"),
	("ETU", "Info", "Admission Responsable"),
	("ATT", "Warning", "Admission Responsable"),
	("ADM", "Primary", "Admission Responsable"),
	("ACO", "Warning", "Admission Responsable"),
	("ACC", "Success", "Admission Direction"),
	("INS", "Success", "Admission Direction"),
	("REF", "Danger", "Admission Direction"),
	("REJ", "Danger", "Admission Administratif"),
	("DES", "Inverse", "Admission Administratif"),
]

TRANSITIONS = [
	("BRO", "Declare Offline Payment", "SOP", "Admission Administratif"),
	("BRO", "Confirm Online Payment", "SOU", "Admission Administratif"),
	("SOP", "Confirm Payment", "SOU", "Admission Administratif"),
	("SOU", "Request Complement", "INC", "Admission Administratif"),
	("INC", "Resubmit Complement", "SOU", "Admission Administratif"),
	("SOU", "Start Review", "ETU", "Admission Administratif"),
	("SOU", "Reject Documentary", "REJ", "Admission Administratif"),
	("REJ", "Reopen", "SOU", "Admission Administratif"),
	("ETU", "Request Complement", "INC", "Admission Responsable"),
	("ETU", "Waitlist", "ATT", "Admission Responsable"),
	("ETU", "Mark Admissible", "ADM", "Admission Responsable"),
	("ETU", "Conditional Admission", "ACO", "Admission Responsable"),
	("ETU", "Refuse", "REF", "Admission Responsable"),
	("ATT", "Mark Admissible", "ADM", "Admission Responsable"),
	("ADM", "Accept Admission", "ACC", "Admission Direction"),
	("ADM", "Refuse", "REF", "Admission Direction"),
	("ACO", "Lift Condition", "ACC", "Admission Direction"),
	("ACO", "Refuse", "REF", "Admission Direction"),
	("ACC", "Enroll", "INS", "Admission Direction"),
	("BRO", "Withdraw", "DES", "Admission Administratif"),
	("SOP", "Withdraw", "DES", "Admission Administratif"),
	("SOU", "Withdraw", "DES", "Admission Administratif"),
	("ETU", "Withdraw", "DES", "Admission Administratif"),
	("ATT", "Withdraw", "DES", "Admission Administratif"),
	("ADM", "Withdraw", "DES", "Admission Administratif"),
	("ACO", "Withdraw", "DES", "Admission Administratif"),
	("ACC", "Withdraw", "DES", "Admission Administratif"),
]


def _setup_workflow():
	for role in ROLES:
		if not frappe.db.exists("Role", role):
			frappe.get_doc({"doctype": "Role", "role_name": role, "desk_access": 1}).insert(ignore_permissions=True)

	for state, style, _role in STATES:
		if not frappe.db.exists("Workflow State", state):
			frappe.get_doc(
				{"doctype": "Workflow State", "workflow_state_name": state, "style": style}
			).insert(ignore_permissions=True)

	for _state, action, _next_state, _role in TRANSITIONS:
		if not frappe.db.exists("Workflow Action Master", action):
			frappe.get_doc(
				{"doctype": "Workflow Action Master", "workflow_action_name": action}
			).insert(ignore_permissions=True)

	workflow_name = "Admission Applicant Workflow"
	if frappe.db.exists("Workflow", workflow_name):
		workflow = frappe.get_doc("Workflow", workflow_name)
		workflow.states = []
		workflow.transitions = []
	else:
		workflow = frappe.get_doc(
			{
				"doctype": "Workflow",
				"workflow_name": workflow_name,
				"document_type": "Admission Applicant",
			}
		)

	workflow.document_type = "Admission Applicant"
	workflow.is_active = 1
	workflow.workflow_state_field = "status"
	workflow.send_email_alert = 0
	workflow.override_status = 0

	for state, _style, role in STATES:
		workflow.append(
			"states",
			{
				"state": state,
				"doc_status": "0",
				"allow_edit": role,
				"update_field": "status",
				"update_value": state,
				"send_email": 0,
			},
		)

	for state, action, next_state, role in TRANSITIONS:
		workflow.append(
			"transitions",
			{
				"state": state,
				"action": action,
				"next_state": next_state,
				"allowed": role,
				"allow_self_approval": 1,
			},
		)

	if workflow.is_new():
		workflow.insert(ignore_permissions=True)
	else:
		workflow.save(ignore_permissions=True)


def execute():
	_setup_workflow()
	_seed_sessions()


def _seed_sessions():
	defaults = [
		{
			"session_code": "SES-2026-10",
			"label": "Octobre 2026",
			"programme_code": "PRE",
			"programme_label": "Cycle preparatoire",
			"academic_year": "2026-2027",
			"opens_on": "2026-06-01",
			"closes_on": "2026-09-15",
			"bac_results_date": "2026-07-15",
			"application_fee_xof": 15000,
			"is_open": 1,
			"is_prepa_session": 1,
		},
		{
			"session_code": "SES-2026-LIC",
			"label": "Licence 2026",
			"programme_code": "LIC",
			"programme_label": "Licence",
			"academic_year": "2026-2027",
			"opens_on": "2026-06-01",
			"closes_on": "2026-09-30",
			"bac_results_date": "2026-07-15",
			"application_fee_xof": 15000,
			"is_open": 1,
			"is_prepa_session": 0,
		},
	]
	for payload in defaults:
		if not frappe.db.exists("Admission Session", payload["session_code"]):
			doc = frappe.get_doc({"doctype": "Admission Session", **payload})
			doc.insert(ignore_permissions=True)
