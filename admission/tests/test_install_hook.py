"""Test after_install — rejoue les seeds idempotents sur install neuve (constat déploiement recette)."""
from unittest import TestCase
from unittest.mock import patch

MOD = "admission.install"


class TestAfterInstall(TestCase):
    @patch(f"{MOD}.seed_recette_catalogue")
    @patch(f"{MOD}.seed_rib")
    @patch(f"{MOD}.create_workflow")
    @patch(f"{MOD}.set_password_policy")
    def test_after_install_runs_all_seeds(self, mpw, mwf, mrib, mcat):
        from admission.install import after_install
        after_install()
        mpw.assert_called_once()
        mwf.assert_called_once()
        mrib.assert_called_once()
        mcat.assert_called_once()
