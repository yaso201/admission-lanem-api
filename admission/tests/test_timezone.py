"""D-TZ-01 — vérifie l'invariant fuseau : le site opère à l'heure du Bénin (Africa/Porto-Novo,
UTC+1). Garanti par le patch set_benin_timezone (appliqué au migrate). now_datetime() stocke en
heure murale du fuseau système → doit coller à l'heure Bénin réelle."""

import datetime

import frappe  # noqa: F401
import pytz
from frappe.tests.utils import FrappeTestCase
from frappe.utils import now_datetime
from frappe.utils.data import get_system_timezone

TARGET_TZ = "Africa/Porto-Novo"


class TestBeninTimezone(FrappeTestCase):
    def test_system_timezone_is_porto_novo(self):
        self.assertEqual(get_system_timezone(), TARGET_TZ)

    def test_benin_zone_is_utc_plus_1_no_dst(self):
        # Invariant indépendant de l'horloge (ne peut pas faux-négatif sur une coïncidence de
        # wall-clock) : Porto-Novo = UTC+1 FIXE, sans DST — offset identique en janvier et juillet.
        tz = pytz.timezone(TARGET_TZ)
        jan = tz.utcoffset(datetime.datetime(2026, 1, 15))
        jul = tz.utcoffset(datetime.datetime(2026, 7, 15))
        self.assertEqual(jan, datetime.timedelta(hours=1))
        self.assertEqual(jul, datetime.timedelta(hours=1))  # pas de DST → offset stable toute l'année

    def test_now_datetime_matches_benin_wallclock(self):
        # now_datetime() = heure murale du fuseau système ; doit coller à Africa/Porto-Novo (UTC+1),
        # PAS à Asia/Kolkata (+5:30, l'ancien fallback → écart ~4h30). On utilise pytz (comme la prod :
        # now_datetime → convert_utc_to_system_timezone via pytz), PAS zoneinfo qui dépend du tzdata
        # OS et casserait sur un conteneur CI slim sans /usr/share/zoneinfo (audit indépendant D-TZ-01).
        nd = now_datetime()
        benin = datetime.datetime.now(pytz.timezone(TARGET_TZ)).replace(tzinfo=None)
        delta = abs((nd - benin).total_seconds())
        self.assertLess(delta, 120, f"now_datetime={nd} vs heure Bénin={benin} (écart {delta:.0f}s)")
