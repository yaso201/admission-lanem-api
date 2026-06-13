"""Seed du catalogue de formations recette (données réelles 13/06/2026).
Idempotent : skip si programme_code déjà présent. Appelé par after_install + à la main."""
import frappe

ESIIA_SIG = "ESIIA"
ESIIA_NAME = "École Supérieure d'Informatique et d'Intelligence Artificielle (ESIIA)"
LOC = "LaNEM, Cotonou — ou ESIIA, France"
LOC_LANEM = "LaNEM, Cotonou"

# (code, title, parcours, partner, partner_name, location, levels[(code,name,order)], fees{type:amount})
PROGRAMMES = [
    {"code": "PREPA", "title": "Cycle Préparatoire", "parcours": "Prépa", "partner": None,
     "partner_name": None, "location": LOC_LANEM,
     "levels": [("A1", "Première année", 1), ("A2", "Deuxième année", 2)],
     "fees": {"competition": 10000, "enrollment": 75000, "annual": 800000}},
    {"code": "LIC-IS", "title": "Licence Informatique & Systèmes", "parcours": "Licence", "partner": None,
     "partner_name": None, "location": LOC_LANEM,
     "levels": [("L1", "Première année", 1), ("L2", "Deuxième année", 2)],
     "fees": {"application": 25000, "enrollment": 50000, "annual": 600000}},
    {"code": "LIC-RC", "title": "Licence Réseaux & Cloud", "parcours": "Licence", "partner": None,
     "partner_name": None, "location": LOC_LANEM,
     "levels": [("L1", "Première année", 1), ("L2", "Deuxième année", 2)],
     "fees": {"application": 25000, "enrollment": 50000, "annual": 600000}},
    {"code": "LIC-MI", "title": "Licence Multimédia & Internet", "parcours": "Licence", "partner": None,
     "partner_name": None, "location": LOC_LANEM,
     "levels": [("L1", "Première année", 1), ("L2", "Deuxième année", 2)],
     "fees": {"application": 25000, "enrollment": 50000, "annual": 600000}},
    {"code": "BACH-DWM", "title": "Bachelor Développeur Web et Mobile", "parcours": "Bachelor",
     "partner": ESIIA_SIG, "partner_name": ESIIA_NAME, "location": LOC,
     "levels": [("B1", "Première année", 1), ("B2", "Deuxième année", 2)],
     "fees": {"application": 40000, "enrollment": 75000, "annual": 1315000}},
    {"code": "BACH-CPI", "title": "Bachelor Chef de Projet Informatique", "parcours": "Bachelor",
     "partner": ESIIA_SIG, "partner_name": ESIIA_NAME, "location": LOC,
     "levels": [("B1", "Première année", 1), ("B2", "Deuxième année", 2)],
     "fees": {"application": 40000, "enrollment": 75000, "annual": 1315000}},
    {"code": "BACH-UX", "title": "Bachelor UX/UI Design", "parcours": "Bachelor",
     "partner": ESIIA_SIG, "partner_name": ESIIA_NAME, "location": LOC,
     "levels": [("B1", "Première année", 1), ("B2", "Deuxième année", 2)],
     "fees": {"application": 40000, "enrollment": 75000, "annual": 1315000}},
    {"code": "BACH-ASRC", "title": "Bachelor Administration Systèmes, Réseaux et Cybersécurité",
     "parcours": "Bachelor", "partner": ESIIA_SIG, "partner_name": ESIIA_NAME, "location": LOC,
     "levels": [("B1", "Première année", 1), ("B2", "Deuxième année", 2)],
     "fees": {"application": 40000, "enrollment": 75000, "annual": 1315000}},
]

DD_FEES = {"application": 40000, "enrollment": 75000, "annual": 1640000}

DOUBLE_DIPLOMES = [
    {"code": "DD-IS-DWM", "licence": "LIC-IS", "bachelor": "BACH-DWM", "affinity": "Recommandé"},
    {"code": "DD-IS-CPI", "licence": "LIC-IS", "bachelor": "BACH-CPI", "affinity": "Possible"},
    {"code": "DD-IS-UX", "licence": "LIC-IS", "bachelor": "BACH-UX", "affinity": "Possible"},
    {"code": "DD-RC-ASRC", "licence": "LIC-RC", "bachelor": "BACH-ASRC", "affinity": "Recommandé"},
    {"code": "DD-RC-CPI", "licence": "LIC-RC", "bachelor": "BACH-CPI", "affinity": "Possible"},
    {"code": "DD-RC-DWM", "licence": "LIC-RC", "bachelor": "BACH-DWM", "affinity": "Possible"},
    {"code": "DD-MI-UX", "licence": "LIC-MI", "bachelor": "BACH-UX", "affinity": "Recommandé"},
    {"code": "DD-MI-DWM", "licence": "LIC-MI", "bachelor": "BACH-DWM", "affinity": "Possible"},
    {"code": "DD-MI-CPI", "licence": "LIC-MI", "bachelor": "BACH-CPI", "affinity": "Possible"},
]

DD_LEVELS = [("L1", "Première année", 1), ("L2", "Deuxième année", 2)]


def _upsert_programme(code, title, parcours, partner, partner_name, location,
                      dd1=None, dd2=None, affinity=None):
    if frappe.db.exists("Admission Programme", code):
        return
    frappe.get_doc({
        "doctype": "Admission Programme", "programme_code": code, "title": title,
        "parcours": parcours, "partner": partner, "partner_name": partner_name,
        "location": location, "is_active": 1, "source": "Manuel",
        "dd_component_1": dd1, "dd_component_2": dd2, "dd_affinity": affinity,
    }).insert(ignore_permissions=True)


def _upsert_level(program_code, level_code, level_name, order):
    if frappe.db.exists("Admission Level Mirror",
                        {"program_code": program_code, "level_code": level_code}):
        return
    frappe.get_doc({"doctype": "Admission Level Mirror", "program_code": program_code,
                    "level_code": level_code, "level_name": level_name,
                    "level_order": order}).insert(ignore_permissions=True)


def _upsert_fee(program_code, level_code, fee_type, amount):
    key = f"{program_code}-{level_code}-{fee_type}"
    if frappe.db.exists("Admission Fee Catalog", key):
        return
    frappe.get_doc({"doctype": "Admission Fee Catalog", "program_code": program_code,
                    "level_code": level_code, "fee_type": fee_type,
                    "amount_xof": amount}).insert(ignore_permissions=True)


def run():
    # NB convention modèle : level_code GLOBALEMENT unique = "{program_code}-{terme}"
    # (ex. LIS-L1, PRE-A1) ; la clé Fee Catalog est "{program}-{level_code}-{fee_type}".
    title_by_code = {}
    # 1) programmes de base + niveaux + frais
    for p in PROGRAMMES:
        _upsert_programme(p["code"], p["title"], p["parcours"], p["partner"],
                          p["partner_name"], p["location"])
        title_by_code[p["code"]] = p["title"]
        for (term, ln, order) in p["levels"]:
            lc = f"{p['code']}-{term}"
            _upsert_level(p["code"], lc, ln, order)
            for ft, amt in p["fees"].items():
                _upsert_fee(p["code"], lc, ft, amt)
    # 2) double-diplomations (niveaux L1/L2, frais DD)
    for d in DOUBLE_DIPLOMES:
        title = f"{title_by_code[d['licence']]} + {title_by_code[d['bachelor']]}"
        _upsert_programme(d["code"], title, "Double-Diplomation", ESIIA_SIG, ESIIA_NAME, LOC,
                          dd1=d["licence"], dd2=d["bachelor"], affinity=d["affinity"])
        for (term, ln, order) in DD_LEVELS:
            lc = f"{d['code']}-{term}"
            _upsert_level(d["code"], lc, ln, order)
            for ft, amt in DD_FEES.items():
                _upsert_fee(d["code"], lc, ft, amt)
    frappe.db.commit()
    return {"programmes": frappe.db.count("Admission Programme")}
