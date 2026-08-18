"""CONTRAT-3 — couverture de contrat des endpoints lourds différés par CONTRAT-2 (volet A).

Six endpoints jamais couverts par un schéma jusqu'ici :
  · calendar_view.calendar_list / session_detail / pending_queue      (calendrier)
  · staff.institutional_transfer_targets                              (transfert institutionnel)
  · staff.list_notes_roster                                          (feuille de notes)
  · staff.get_dossier                                                (détail dossier + A4)

CINQ tournent par APPEL RÉEL in-process sur les sessions déjà présentes en base de test (patron
`test_list_dossiers_conforms` de CONTRAT-2) — aucune fixture lourde. `build_to` (recette_fixtures)
n'est PAS utilisable ici : c'est un pilote E2E HORS-PROCESSUS (HTTP réel vers le serveur de recette,
docstring « bench execute », jamais `run-tests`) — le seed d'un catalogue ne l'aurait pas rendu
in-process (recadrage CONTRAT-3, DEC-A).

get_dossier a besoin d'UN dossier → VOIE (A) : on insère un dossier réel « décoré » en état BRO
(l'état initial — aucun statut/verdict avancé triché) et on appelle le VRAI `staff.get_dossier`.
⚠️ CE QUE CE TEST COUVRE : la FORME DU SÉRIALISEUR réel (les 31 clés, leur type, blocked_actions).
    CE QU'IL NE COUVRE PAS : le WORKFLOW (le dossier est inséré, pas construit par le tunnel métier —
    ça, c'est le rôle des E2E `test_claim_recovered_dossier`/`test_etude`, et de build_to en recette).

A4 (DEC-F) : le schéma get_dossier ACCEPTE l'item réservé Administratif sur blocked_actions
(`{action:confirm_payment, code:RESERVED_TO_ADMINISTRATIF}`, NT-UX-2). Prouvé par l'appel RÉEL à
`_actions.blocked_actions` sur un BRO vu par un non-Administratif.

Falsifiabilité (DEC-D, ≥3, deux sens) : retirer une clé consommée → rouge ; ajouter une clé hors
schéma → rouge (`additionalProperties:false`) ; un blocked_action sans `code` → rouge (item strict).
"""
import frappe
from frappe.tests.utils import FrappeTestCase

from admission.contracts import registry
from admission.contracts.validator import validate

TAG = "contrat3-fixture@e2e.lanem.test"


def _envelope(schema_id):
    return registry.envelope_schema(registry.load_data_schema(schema_id))


def _a_session():
    s = frappe.get_all("Admission Session", pluck="name", order_by="creation asc", limit_page_length=1)
    return s[0] if s else None


def _insert_decorated_bro():
    """VOIE (A) — un dossier réel DÉCORÉ en BRO (état initial), rattaché à une session non-prépa
    existante. Insertion directe (pas le tunnel) : on éprouve la FORME du sérialiseur, pas le workflow.
    Rollback automatique (FrappeTestCase). Retourne (doc, session) ou (None, None) si le décor manque."""
    sess = frappe.get_all("Admission Session", filters={"is_prepa_session": 0},
                          fields=["name", "programme_code"], order_by="creation asc", limit_page_length=1)
    if not sess:
        return None, None
    s = sess[0]
    lvl = frappe.get_all("Admission Fee Catalog", filters={"program_code": s.programme_code},
                         fields=["level_code"], limit_page_length=1)
    level = lvl[0].level_code if lvl else "N1"
    doc = frappe.get_doc({
        "doctype": "Admission Applicant", "status": "BRO",
        "first_name": "Contrat", "last_name": "Trois", "email": TAG, "phone": "+22990000000",
        "programme_code": s.programme_code, "level_code": level, "session": s.name,
    })
    doc.flags.ignore_permissions = True
    doc.insert(ignore_permissions=True)
    return doc, s.name


# ── Conformité par appel RÉEL in-process (5 endpoints sans dossier) ────────────

class TestHeavyEndpointsConformance(FrappeTestCase):

    def test_calendar_list_conforms(self):
        from admission.api import calendar_view
        resp = calendar_view.calendar_list()
        errs = validate(resp, _envelope("calendar_view.calendar_list"))
        self.assertEqual(errs, [], "\n".join(errs))

    def test_session_detail_conforms(self):
        from admission.api import calendar_view
        s = _a_session()
        if not s:
            self.skipTest("aucune session en base de test")
        resp = calendar_view.session_detail(session=s)
        errs = validate(resp, _envelope("calendar_view.session_detail"))
        self.assertEqual(errs, [], "\n".join(errs))

    def test_pending_queue_conforms(self):
        from admission.api import calendar_view
        resp = calendar_view.pending_queue()
        errs = validate(resp, _envelope("calendar_view.pending_queue"))
        self.assertEqual(errs, [], "\n".join(errs))

    def test_institutional_transfer_targets_conforms(self):
        from admission.api import staff
        s = _a_session()
        if not s:
            self.skipTest("aucune session en base de test")
        resp = staff.institutional_transfer_targets(source_session=s)
        errs = validate(resp, _envelope("staff.institutional_transfer_targets"))
        self.assertEqual(errs, [], "\n".join(errs))

    def test_list_notes_roster_conforms(self):
        from admission.api import staff
        s = _a_session()
        if not s:
            self.skipTest("aucune session en base de test")
        resp = staff.list_notes_roster(session_id=s)
        errs = validate(resp, _envelope("staff.list_notes_roster"))
        self.assertEqual(errs, [], "\n".join(errs))


# ── get_dossier : VOIE (A) dossier décoré + A4 ────────────────────────────────

class TestGetDossierConformance(FrappeTestCase):

    def test_get_dossier_real_serializer_conforms(self):
        doc, _ = _insert_decorated_bro()
        if not doc:
            self.skipTest("aucune session non-prépa en base de test (décor absent)")
        from admission.api import staff
        resp = staff.get_dossier(dossier_id=doc.name)
        self.assertTrue(resp.get("ok"), f"get_dossier a échoué : {resp}")
        errs = validate(resp, _envelope("staff.get_dossier"))
        self.assertEqual(errs, [], "\n".join(errs))

    def test_a4_reserved_administratif_blocked_action(self):
        """A4 (DEC-F) : un BRO vu par un non-Administratif expose l'item réservé — item RÉEL, et le
        schéma get_dossier l'ACCEPTE dans blocked_actions."""
        doc, _ = _insert_decorated_bro()
        if not doc:
            self.skipTest("aucune session non-prépa en base de test (décor absent)")
        from admission.api._actions import blocked_actions
        from admission.api import staff
        items = blocked_actions(doc, ["Admission Direction"], is_prepa=False)
        a4 = [b for b in items if b.get("action") == "confirm_payment"]
        self.assertTrue(a4, "un dossier BRO doit bloquer confirm_payment pour un non-Administratif (NT-UX-2)")
        self.assertEqual(a4[0]["code"], "RESERVED_TO_ADMINISTRATIF")
        self.assertEqual(set(a4[0].keys()), {"action", "actor", "code", "reason"})
        # le schéma accepte cet item réel injecté dans une réponse get_dossier réelle
        resp = staff.get_dossier(dossier_id=doc.name)
        resp["data"]["blocked_actions"] = a4
        errs = validate(resp, _envelope("staff.get_dossier"))
        self.assertEqual(errs, [], "\n".join(errs))


# ── Falsifiabilité (≥3, deux sens) : le contrat n'est pas vacant ──────────────

class TestFalsifiability(FrappeTestCase):

    def _base_dossier_resp(self):
        doc, _ = _insert_decorated_bro()
        if not doc:
            self.skipTest("aucune session non-prépa en base de test (décor absent)")
        from admission.api import staff
        return staff.get_dossier(dossier_id=doc.name)

    def test_removing_consumed_key_turns_red(self):
        resp = self._base_dossier_resp()
        for key in ("identite", "statut", "blocked_actions", "pieces"):
            broken = {"ok": True, "error": None,
                      "data": {k: v for k, v in resp["data"].items() if k != key}}
            self.assertTrue(validate(broken, _envelope("staff.get_dossier")),
                            f"retirer '{key}' aurait dû rougir (required)")

    def test_extra_key_turns_red(self):
        resp = self._base_dossier_resp()
        resp["data"]["fuite_sur_reponse"] = "PII"
        errs = validate(resp, _envelope("staff.get_dossier"))
        self.assertTrue(any("fuite_sur_reponse" in e for e in errs),
                        f"une clé hors schéma aurait dû rougir (additionalProperties:false) : {errs}")

    def test_blocked_action_without_code_turns_red(self):
        resp = self._base_dossier_resp()
        resp["data"]["blocked_actions"] = [{"action": "confirm_payment", "actor": "Administratif",
                                            "reason": "Réservé à l'Administratif"}]  # code manquant
        errs = validate(resp, _envelope("staff.get_dossier"))
        self.assertTrue(errs, "un blocked_action sans 'code' aurait dû rougir (item strict A4)")

    def test_session_detail_extra_key_turns_red(self):
        s = _a_session()
        if not s:
            self.skipTest("aucune session en base de test")
        from admission.api import calendar_view
        resp = calendar_view.session_detail(session=s)
        resp["data"]["cle_fantome"] = 1
        errs = validate(resp, _envelope("calendar_view.session_detail"))
        self.assertTrue(any("cle_fantome" in e for e in errs),
                        f"clé hors schéma aurait dû rougir : {errs}")
