"""after_install — rejoue les seeds idempotents sur une install NEUVE.

Frappe marque les patches 'complétés' sans les exécuter à l'install (constat déploiement
recette : workflow + RIB absents). Ce hook appelle directement les fonctions de seed.
Toutes sont idempotentes (skip si déjà présent)."""
from admission.patches.v1_0.set_password_policy import execute as set_password_policy
from admission.patches.v1_0.create_admission_workflow import execute as create_workflow
from admission.patches.v1_0.seed_rib_settings import execute as seed_rib


def seed_recette_catalogue():
    # Seed du catalogue (17 programmes) — défini en Task 5, importé ici.
    from admission.seed.catalogue import run as _run
    _run()


def after_install():
    set_password_policy()
    create_workflow()
    seed_rib()
    seed_recette_catalogue()
