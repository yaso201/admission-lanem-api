from admission.patches.v1_0.create_admission_workflow import _setup_workflow


def execute():
	"""FIX-ROLES-HYBRIDE-WORKFLOW — reconfigure les transitions du Workflow « Admission Applicant
	Workflow » existant vers le modèle HYBRIDE (opérationnel ascendant multi-lignes + décisions/
	validations exactes + System Manager break-glass sur chaque transition save-path).

	Idempotent : _setup_workflow() reconstruit states/transitions sur le Workflow en place
	(il vide `states`/`transitions` puis ré-append depuis la liste TRANSITIONS hybride). Ne
	re-seede PAS les sessions (on n'appelle pas execute() du patch v1_0)."""
	_setup_workflow()
