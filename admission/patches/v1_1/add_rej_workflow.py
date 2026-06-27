from admission.patches.v1_0.create_admission_workflow import _setup_workflow


def execute():
	"""Lot 3c-1 — ajoute l'état workflow REJ + les transitions SOU↔REJ (Reject Documentary / Reopen)
	sur les sites existants. Réutilise _setup_workflow (workflow SEUL : Workflow State/Action
	existence-checked, states/transitions ré-appliqués depuis les listes — idempotent).
	NE re-seed PAS les sessions de test (séparation execute/_setup_workflow)."""
	_setup_workflow()
