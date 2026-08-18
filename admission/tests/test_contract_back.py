"""CONTRAT-1 — Conformité BACK : la réponse RÉELLE de chaque endpoint P1 est validée contre son
schéma. `required` du schéma = champs consommés par le front (A3 §2.1) → si le back cesse de servir
un champ consommé, ou change sa forme, ou sert un champ non documenté (additionalProperties:false),
le test ROUGIT. C'est la garantie primaire de non-divergence (DEC-A/DEC-B), en un seul dépôt.

Les endpoints sont appelés DIRECTEMENT comme fonctions (utilisateur Administrator par défaut → passe
`frappe.only_for`), comme le font déjà les tests existants (ex. test_admin_config).
Niveau atteint déclaré par endpoint (docstring). list_dossiers EXCLU (frontière PERF-1, décrit en dernier).
"""
from frappe.tests.utils import FrappeTestCase

from admission.api import admin_config, admin_ops, admin_referentiel, public, staff
from admission.contracts import registry
from admission.contracts.validator import validate


def _conforms(test, response, schema_id):
    schema = registry.envelope_schema(registry.load_data_schema(schema_id))
    errs = validate(response, schema)
    test.assertEqual(errs, [], f"\n[{schema_id}] réponse NON conforme au contrat :\n  " + "\n  ".join(errs))


class TestContractBackSMDiagnostics(FrappeTestCase):
    """Endpoints de diagnostic SM/staff — appelables sans dossier (rôle Administrator)."""

    def test_get_config_health(self):
        # Niveau : back-conformance. E-01 (fantôme fedapay) vit ici.
        _conforms(self, admin_config.get_config_health(), "admin_config.get_config_health")

    def test_get_ops_health(self):
        # Niveau : back-conformance (additif). Les 8 compteurs requis ; clés OBS-2 tolérées.
        _conforms(self, admin_ops.get_ops_health(), "admin_ops.get_ops_health")

    def test_get_degraded_status(self):
        _conforms(self, admin_referentiel.get_degraded_status(), "admin_referentiel.get_degraded_status")

    def test_whoami(self):
        # staff.whoami — LU seulement, staff.py NON modifié (frontière PERF-1 = list_dossiers, décrit en dernier).
        _conforms(self, staff.whoami(), "staff.whoami")

    def test_stats_direction(self):
        # Endpoint de DÉCISION (Direction). Agrégats — valides même base vide.
        _conforms(self, staff.stats_direction(), "staff.stats_direction")


class TestContractBackPublicReads(FrappeTestCase):
    """Lectures publiques candidat (allow_guest) — tableaux valides même si vides sur base de test."""

    def test_list_sessions(self):
        _conforms(self, public.list_sessions(), "public.list_sessions")

    def test_list_programmes(self):
        _conforms(self, public.list_programmes(), "public.list_programmes")
