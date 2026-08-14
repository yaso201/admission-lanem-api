"""NOTES-CONCOURS — SOURCE UNIQUE de la structure des épreuves, du barème, de la plage,
de la moyenne pondérée et du signal éliminatoire.

Principe (mandat §3) : UNE seule définition des 3 épreuves du concours, consommée par
  (a) la saisie (garde de plage bloquante — GN3),
  (b) le calcul de moyenne (GN1) et le signal éliminatoire (GN4) — À LA LECTURE, JAMAIS stockés,
  (c) l'export / l'import de masse (rapprochement + aperçu),
  (d) le front (rendu pur).

« Le système signale, le Responsable décide » (mandat §3.4) : la moyenne et l'éliminatoire sont des
AFFICHAGES, jamais des décisions. Aucune valeur figée : tout se recalcule depuis les notes brutes et
les coefficients de la session → si un coefficient change (avant verrou), rien à re-migrer.

Absent ≠ zéro (vigilance architecte) : un candidat ABSENT n'a PAS de moyenne. Il est stocké par une
sentinelle et n'entre JAMAIS dans le calcul (jamais affiché « 0 », ce qui l'éliminerait à tort).
"""

import json

# Les 3 épreuves canoniques (mandat §3.1). Clé technique → libellé affiché. Cet ordre EST l'ordre
# des colonnes d'export et d'affichage. Modifier ici = tout suit (saisie, moyenne, front, CSV).
EXAM_SUBJECTS = [
    ("maths", "Mathématiques"),
    ("physique", "Sciences physiques"),
    ("culture", "Culture générale"),
]
SUBJECT_KEYS = [k for k, _ in EXAM_SUBJECTS]
SUBJECT_LABELS = dict(EXAM_SUBJECTS)

NOTE_MIN = 0.0
NOTE_MAX = 20.0                 # échelle /20 (mandat §3.1)
ELIMINATOIRE_THRESHOLD = 6.0    # note < 6 sur UNE épreuve = éliminatoire (mandat §3.4)

# Sentinelle d'absence (mandat §3.6). Stockée dans notes_concours = {"__absent__": true}. L'état du
# dossier qui en découle relève du LOT SUIVANT — on enregistre l'absence, on ne la traduit pas.
ABSENT_MARKER = "__absent__"


def _load(notes):
    """notes (str JSON | dict | None) → dict | None (jamais lève)."""
    if notes is None or notes == "":
        return None
    if isinstance(notes, str):
        try:
            return json.loads(notes)
        except (ValueError, TypeError):
            return None
    return notes if isinstance(notes, dict) else None


def is_absent(notes):
    """True si la saisie marque une ABSENCE (sentinelle)."""
    d = _load(notes)
    return isinstance(d, dict) and d.get(ABSENT_MARKER) is True


def make_absent():
    """Représentation canonique d'une absence à stocker."""
    return {ABSENT_MARKER: True}


def validate_entry(entry):
    """Valide UNE saisie candidat (mandat §3.1/§3.3, GN1/GN3). Deux formes légales :
      - ABSENCE : {"__absent__": true} seul ;
      - COMPLET : EXACTEMENT les 3 épreuves canoniques, chacune numérique dans [0, 20].
    Un sous-ensemble (1 ou 2 notes) est REFUSÉ — « les trois passent ou aucune » (arbitrage 2) : on
    ne calcule jamais une moyenne sur deux notes. Retourne (parsed_dict, None) | (None, message)."""
    d = _load(entry)
    if not isinstance(d, dict) or not d:
        return None, "Saisie vide ou invalide."
    if d.get(ABSENT_MARKER) is True and len(d) == 1:
        return {ABSENT_MARKER: True}, None
    keys = set(d.keys())
    expected = set(SUBJECT_KEYS)
    if keys != expected:
        missing = [SUBJECT_LABELS[k] for k in SUBJECT_KEYS if k not in keys]
        extra = [k for k in keys if k not in expected]
        parts = []
        if missing:
            parts.append("manquante(s) : " + ", ".join(missing))
        if extra:
            parts.append("inconnue(s) : " + ", ".join(extra))
        return None, "Les trois notes sont requises — " + " ; ".join(parts) + "."
    parsed = {}
    for k in SUBJECT_KEYS:
        try:
            v = float(d[k])
        except (ValueError, TypeError):
            return None, f"Note non numérique pour {SUBJECT_LABELS[k]}."
        if v < NOTE_MIN or v > NOTE_MAX:
            return None, (f"Note hors barème pour {SUBJECT_LABELS[k]} : {_fmt(v)} "
                          f"(attendu {int(NOTE_MIN)}–{int(NOTE_MAX)}).")
        parsed[k] = v
    return parsed, None


def compute_moyenne(parsed, coefficients):
    """Moyenne PONDÉRÉE (GN1) = Σ(note_k · coef_k) / Σ(coef_k). None si absent, saisie incomplète ou
    coefficients absents/nuls. Arrondi à 2 décimales. Calcul À LA LECTURE — jamais stocké."""
    if not parsed or is_absent(parsed):
        return None
    if any(k not in parsed for k in SUBJECT_KEYS):
        return None
    total = 0.0
    total_coef = 0.0
    for k in SUBJECT_KEYS:
        c = float(coefficients.get(k, 0) or 0)
        total += float(parsed[k]) * c
        total_coef += c
    if total_coef <= 0:
        return None
    return round(total / total_coef, 2)


def eliminatoire_signals(parsed):
    """SIGNAL (GN4) — liste des clés d'épreuves sous le seuil (< 6), ÉPREUVE PAR ÉPREUVE. [] si
    absent/incomplet. Un 5 en culture est éliminatoire même avec 15 de moyenne. On SIGNALE ; le
    Responsable DÉCIDE (doctrine « aucune décision automatisée », loi 2017-20)."""
    if not parsed or is_absent(parsed) or any(k not in parsed for k in SUBJECT_KEYS):
        return []
    return [k for k in SUBJECT_KEYS if float(parsed[k]) < ELIMINATOIRE_THRESHOLD]


def summary(notes_concours, coefficients):
    """Vue LECTURE (serializer / front / aperçu) dérivée des notes brutes + coefficients. Ne stocke
    RIEN, ne décide RIEN. Absent → moyenne None + éliminatoire [] (jamais 0)."""
    d = _load(notes_concours)
    if not d:
        return {"renseigne": False, "absent": False, "valeurs": None, "moyenne": None, "eliminatoire": []}
    if is_absent(d):
        return {"renseigne": True, "absent": True, "valeurs": None, "moyenne": None, "eliminatoire": []}
    return {
        "renseigne": True,
        "absent": False,
        "valeurs": {k: d.get(k) for k in SUBJECT_KEYS if k in d},
        "moyenne": compute_moyenne(d, coefficients or {}),
        "eliminatoire": eliminatoire_signals(d),
    }


def coefficients_of(session):
    """Lit exam_coefficients (JSON) d'une session (doc Frappe ou dict). {} si absent/illisible."""
    raw = session.get("exam_coefficients") if isinstance(session, dict) else getattr(session, "exam_coefficients", None)
    d = _load(raw)
    if not isinstance(d, dict):
        return {}
    out = {}
    for k in SUBJECT_KEYS:
        if k in d:
            try:
                out[k] = float(d[k])
            except (ValueError, TypeError):
                continue
    return out


def coefficients_complete(coefficients):
    """True si les 3 coefficients sont présents et strictement positifs (moyenne calculable)."""
    return all(float(coefficients.get(k, 0) or 0) > 0 for k in SUBJECT_KEYS)


def validate_coefficients(coefs):
    """Valide des coefficients à POSER (GN2) : les 3 épreuves, chacune strictement positive.
    Retourne (parsed_dict, None) | (None, message)."""
    d = _load(coefs)
    if not isinstance(d, dict):
        return None, "Coefficients invalides (objet {épreuve: coefficient} attendu)."
    parsed = {}
    for k in SUBJECT_KEYS:
        if k not in d:
            return None, f"Coefficient manquant pour {SUBJECT_LABELS[k]}."
        try:
            c = float(d[k])
        except (ValueError, TypeError):
            return None, f"Coefficient non numérique pour {SUBJECT_LABELS[k]}."
        if c <= 0:
            return None, f"Le coefficient de {SUBJECT_LABELS[k]} doit être strictement positif."
        parsed[k] = c
    return parsed, None


def subjects_payload():
    """Définition des épreuves pour le front (rendu pur) : ordre, clés, libellés, barème, seuil."""
    return {
        "subjects": [{"key": k, "label": SUBJECT_LABELS[k]} for k in SUBJECT_KEYS],
        "note_min": NOTE_MIN,
        "note_max": NOTE_MAX,
        "eliminatoire_threshold": ELIMINATOIRE_THRESHOLD,
        "absent_marker": ABSENT_MARKER,
    }


def _fmt(v):
    """Affiche 14 au lieu de 14.0, 14.5 tel quel (messages d'erreur lisibles)."""
    f = float(v)
    return str(int(f)) if f == int(f) else str(f)
