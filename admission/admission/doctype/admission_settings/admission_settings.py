"""Admission Settings (single) — cloisonnement de consultation (SOCLE-0-CONSULT) + COMPTE
D'ENCAISSEMENT (LOT RIB-SETTINGS, solution 1 légère validée architecte).

RIB : source UNIQUE des mails SOP (inline + PDF joint), de get_frais (portail candidat) et
du gate recette. Édition réservée au rôle Admission Finance. À l'enregistrement :
  1. ROTATION versionnée du PDF par hash — l'ancien File est DÉTRUIT (aucun exemplaire
     périmé ne peut partir avec un mail) ; corps et PJ sortent de la même génération ;
  2. mail de VÉRIFICATION à l'éditeur (le gabarit virement RÉEL, avec la nouvelle PJ) ;
  3. ALERTES croisées Direction + System Managers (compta UF) ;
  4. invalidation du cache catalogue (get_frais sert la nouvelle valeur immédiatement).
NB : les mails déjà en file embarquent leur PDF (fenêtre de drain) — procédure OPS en
checklist §4 pour un changement d'urgence.

Porte l'interrupteur maître `consultation_cloisonnee` (OFF par défaut), l'`consultation_axis`
(fieldname d'Admission Applicant) et le `consultation_role_scopes` (mapping rôle→[valeurs]).
La logique de cloisonnement vit dans admission/api/permissions.py.
"""

import json

import frappe
from frappe.model.document import Document


class AdmissionSettings(Document):
    def validate(self):
        if not self.consultation_cloisonnee:
            return  # OFF : aucune contrainte sur axe/scopes

        # Anti-injection niveau 1 : l'axe doit être un fieldname réel d'Admission Applicant.
        axis = (self.consultation_axis or "").strip()
        if axis != "name" and not frappe.get_meta("Admission Applicant").has_field(axis):
            frappe.throw(
                f"Axe de cloisonnement invalide : {axis!r} n'est pas un champ d'Admission Applicant."
            )

        # Le périmètre doit être un objet JSON {rôle: [valeurs]}.
        raw = self.consultation_role_scopes
        if not raw:
            return
        try:
            scopes = json.loads(raw) if isinstance(raw, str) else raw
        except (ValueError, TypeError):
            frappe.throw("Périmètre par rôle : JSON invalide.")
        if not isinstance(scopes, dict) or not all(isinstance(v, list) for v in scopes.values()):
            frappe.throw("Périmètre par rôle : attendu un objet {rôle: [valeurs]}.")


    # ── LOT RIB-SETTINGS ───────────────────────────────────────────────────────

    def on_update(self):
        if getattr(self.flags, "in_rib_rotation", False):
            return
        changed = self._rotate_rib_pdf()
        data_changed = self._rib_data_changed()
        if changed or data_changed:
            self._stamp_and_invalidate()
            self._broadcast_rib_change()

    def _rib_data_changed(self):
        old = self.get_doc_before_save()
        if not old:
            return False
        return any((old.get(f) or "") != (self.get(f) or "")
                   for f in ("rib_banque", "rib_titulaire", "rib_iban", "rib_bic"))

    @staticmethod
    def _purge_rib_urls(urls, keep=None):
        """Purge POST-COMMIT par file_url : la machinerie core attach_files_to_document
        recrée des File pour l'URL du champ pendant le cycle de save — toute suppression
        intra-save est une course perdue. Après commit, on supprime TOUTES les lignes
        File de chaque URL périmée (le delete_doc de la dernière ligne détruit aussi le
        fichier physique) → aucun exemplaire périmé ne survit. `keep` préserve la ligne
        promue en canonique (son fichier physique est alors conservé par le core)."""
        import os
        for url in {u for u in urls if u}:
            for name in frappe.get_all("File", filters={"file_url": url}, pluck="name"):
                if name == keep:
                    continue
                try:
                    frappe.delete_doc("File", name, force=True, ignore_permissions=True)
                except Exception:
                    frappe.logger("admission").warning(
                        f"RIB purge failed for {url}: {frappe.get_traceback()}")
            # File.on_trash ne détruit pas toujours le physique (vu en preuve runtime) :
            # destruction explicite dès que plus AUCUNE ligne ne référence l'URL.
            if url.startswith("/private/files/") \
                    and not frappe.get_all("File", filters={"file_url": url}, limit=1):
                path = frappe.get_site_path("private", "files", os.path.basename(url))
                if os.path.exists(path):
                    os.remove(path)
        frappe.db.commit()

    def _schedule_purge(self, urls, keep=None):
        urls = [u for u in urls if u]
        if not urls:
            return
        frappe.db.after_commit.add(lambda: AdmissionSettings._purge_rib_urls(urls, keep=keep))

    def _rotate_rib_pdf(self):
        """Rotation par hash : nouveau contenu → File privé versionné, ANCIENS détruits.
        Renvoie True si le PDF a changé."""
        import hashlib
        if not self.rib_pdf:
            if self.rib_pdf_hash:  # PDF retiré volontairement → purge des versions
                self._schedule_purge(self._versioned_urls())
                self.flags.in_rib_rotation = True
                frappe.db.set_value("Admission Settings", "Admission Settings",
                                    {"rib_pdf_hash": None, "rib_version": None},
                                    update_modified=False)
                return True
            return False
        staging = frappe.get_all("File", filters={"file_url": self.rib_pdf},
                                 fields=["name", "file_name"], limit=1)
        if not staging:
            return False
        staging_doc = frappe.get_doc("File", staging[0].name)
        content = staging_doc.get_content()
        if isinstance(content, str):
            content = content.encode()
        if not content.startswith(b"%PDF"):
            frappe.throw("Le fichier RIB doit être un PDF valide.")
        digest = hashlib.sha256(content).hexdigest()
        if digest == (self.rib_pdf_hash or ""):
            # Même contenu : NO-OP de version — on repointe vers la CANONIQUE et on
            # détruit le staging (sinon il s'accumule et rib_pdf désigne un duplicata).
            canonical = frappe.get_all("File", filters={
                "attached_to_doctype": "Admission Settings",
                "file_name": ("like", f"RIB-LaNEM-v{self.rib_version}%")},
                fields=["name", "file_url"], limit=1)
            if canonical:
                if staging_doc.file_url != canonical[0].file_url:
                    self.flags.in_rib_rotation = True
                    frappe.db.set_value("Admission Settings", "Admission Settings",
                                        "rib_pdf", canonical[0].file_url, update_modified=False)
                    self.rib_pdf = canonical[0].file_url
                    self._schedule_purge([staging_doc.file_url])
                return False
            # canonique absente (état à réparer) → retomber dans la rotation pour la recréer
        version = digest[:8]
        fname = f"RIB-LaNEM-v{version}.pdf"
        new_url = f"/private/files/{fname}"
        old_urls = self._versioned_urls() + [staging_doc.file_url, new_url]
        # PIÈGE save_file : il dédoublonne par content_hash et renverrait le staging
        # lui-même (même contenu par construction) → on écrit le fichier physique
        # nous-mêmes et on PROMEUT la ligne File du staging en canonique (db-level).
        import os
        path = frappe.get_site_path("private", "files", fname)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as fh:
            fh.write(content)
        frappe.db.set_value("File", staging_doc.name, {
            "file_name": fname, "file_url": new_url, "is_private": 1,
            "attached_to_doctype": "Admission Settings",
            "attached_to_name": "Admission Settings", "attached_to_field": "rib_pdf"},
            update_modified=False)
        # Détruire TOUTES les autres versions + les duplicatas — APRÈS COMMIT (course core)
        self._schedule_purge([u for u in old_urls if u], keep=staging_doc.name)
        self.flags.in_rib_rotation = True
        frappe.db.set_value("Admission Settings", "Admission Settings",
                            {"rib_pdf": new_url, "rib_pdf_hash": digest,
                             "rib_version": version}, update_modified=False)
        self.rib_pdf, self.rib_pdf_hash, self.rib_version = new_url, digest, version
        return True

    def _versioned_urls(self):
        return [f.file_url for f in frappe.get_all(
            "File", filters={"attached_to_doctype": "Admission Settings",
                             "file_name": ("like", "RIB-LaNEM-v%")},
            fields=["file_url"])]

    def _stamp_and_invalidate(self):
        frappe.db.set_value("Admission Settings", "Admission Settings",
                            {"rib_updated_by": frappe.session.user,
                             "rib_updated_at": frappe.utils.now_datetime()},
                            update_modified=False)
        try:
            from admission.api.public import _invalidate_catalog_cache
            _invalidate_catalog_cache()
        except Exception:
            pass

    def _broadcast_rib_change(self):
        """Mail de VÉRIFICATION à l'éditeur (gabarit virement réel + PJ) + ALERTES
        Direction/System Managers. Non-bloquant : un échec d'envoi ne bloque pas la saisie."""
        try:
            from types import SimpleNamespace
            from admission.api.notifications import send_offline_submission
            editor_email = frappe.db.get_value("User", frappe.session.user, "email") \
                or frappe.session.user
            probe = SimpleNamespace(name="VERIFICATION-RIB", applicant_name="Vérification RIB",
                                    programme_label="(mail de contrôle — aucune action candidat)",
                                    email=editor_email)
            fee = SimpleNamespace(amount_xof=0)
            send_offline_submission(probe, fee, "bank")  # le gabarit RÉEL, nouvelle PJ incluse
            recipients = set()
            for u in frappe.get_all("Has Role", filters={"role": "Admission Direction",
                                                         "parenttype": "User"}, pluck="parent"):
                em = frappe.db.get_value("User", u, "email")
                if em and frappe.db.get_value("User", u, "enabled"):
                    recipients.add(em)
            from frappe.utils.user import get_system_managers
            recipients.update(get_system_managers(only_name=False) or [])
            recipients.discard(editor_email)
            if recipients:
                frappe.sendmail(
                    recipients=sorted(recipients),
                    subject="[Admission] Le RIB d'encaissement a été modifié",
                    message=(f"Le compte d'encaissement affiché aux candidats a été modifié par "
                             f"{frappe.session.user} le {frappe.utils.now_datetime()}.<br>"
                             f"Banque : {self.rib_banque} · IBAN : {self.rib_iban} · "
                             f"BIC : {self.rib_bic} · version PDF : {self.rib_version or '—'}.<br>"
                             f"Compta : vérifier la concordance avec le Bank Account UF."),
                )
            frappe.logger("admission").info(f"RIB change broadcast v={self.rib_version}")
        except Exception:
            frappe.logger("admission").warning(
                f"RIB broadcast failed (non-blocking): {frappe.get_traceback()}")
