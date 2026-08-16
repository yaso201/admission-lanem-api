"""TRANSFERT-SESSION — ajoute ABS et ses transitions Responsable sur les sites existants."""

from admission.patches.v1_0.create_admission_workflow import _setup_workflow


def execute():
	"""Reconstruit le Workflow depuis sa source idempotente, sans reseeder les sessions."""
	_setup_workflow()
