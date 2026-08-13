"""after_install — rejoue les correctifs idempotents sur une install NEUVE.

Frappe marque les patches 'complétés' SANS les exécuter à l'install : `install_app(set_as_patched=
True)` appelle `set_all_patches_as_completed(app)` (frappe/installer.py) → un Patch Log par correctif,
zéro exécution. Le sync des DocTypes reproduit les schémas déclaratifs, mais PAS les effets des
correctifs de config (System Settings), de rôles/workflow, ni les INDEX SQL bruts → un site neuf
naît avec ces garanties manquantes, silencieusement (audit A04 : desk-lock, expiry lien, et les
DEUX index d'intégrité argent étaient absents en prod).

Parade : ce hook appelle DIRECTEMENT les correctifs effectifs et idempotents (skip si déjà présent),
pour qu'un site neuf atteigne la parité avec un site migré. Les correctifs de DONNÉES (backfill_uf,
rename_catalog, backfill_numbering) sont exclus : sans objet sur base neuve. harden_sm_account
(2FA globale) est exclu VOLONTAIREMENT : la version Frappe déployée n'exempte PAS `Administrator`
de la 2FA → une activation globale risquerait de verrouiller le compte break-glass (finding A04,
à réintégrer une fois l'exemption Administrator disponible côté Frappe)."""
from admission.patches.v1_0.set_password_policy import execute as set_password_policy
from admission.patches.v1_0.create_admission_workflow import execute as create_workflow
from admission.patches.v1_0.seed_rib_settings import execute as seed_rib
# A04 — correctifs effectifs que l'install neuve n'exécute pas (marqués joués à vide).
from admission.patches.v1_1.add_confirmed_fee_unique import execute as add_confirmed_fee_unique
from admission.patches.v1_1.add_applicant_fee_type_unique import execute as add_applicant_fee_type_unique
from admission.patches.v1_1.lock_desk_for_staff import execute as lock_desk_for_staff
from admission.patches.v1_1.set_invitation_link_expiry import execute as set_invitation_link_expiry
from admission.patches.v1_1.set_invitation_email_template import execute as set_invitation_email_template


def seed_recette_catalogue():
    # Seed du catalogue (17 programmes) — défini en Task 5, importé ici.
    from admission.seed.catalogue import run as _run
    _run()


def after_install():
    set_password_policy()
    create_workflow()          # rôles staff + workflow + états (REJ, hybride) + sessions
    seed_rib()                 # + rôle Admission Finance (requis par lock_desk_for_staff)
    seed_recette_catalogue()
    # A04 — intégrité + durcissement, APRÈS la création des rôles (ordre requis par lock_desk).
    add_confirmed_fee_unique()        # 🔴 index unicité « ≤ 1 Confirmed / frais » (anti double-encaissement)
    add_applicant_fee_type_unique()   # 🔴 index unicité (applicant, fee_type)
    lock_desk_for_staff()             # 🔴 staff hors Desk Frappe (desk_access=0 sur les 4 rôles)
    set_invitation_link_expiry()      # lien invitation/reset = 24 h
    set_invitation_email_template()   # gabarits e-mail LaNEM (invitation/reset)
