"""Testes da autorização pela CRON_SECRET.

    python3 -m unittest discover -s tests
"""
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "api" / "_lib"))
import autorizacao  # noqa: E402


class TestAutorizacao(unittest.TestCase):
    def test_espaco_ou_quebra_de_linha_colados_com_a_senha(self):
        # Caso real de 27/10/2026: senha nova colada na Vercel e no link dava "acesso negado".
        with mock.patch.dict(os.environ, {"CRON_SECRET": "abc123\n"}):
            self.assertTrue(autorizacao.autorizado_cron({}, {"chave": ["abc123"]}))
            self.assertTrue(autorizacao.autorizado_cron({}, {"chave": [" abc123 "]}))
            self.assertTrue(autorizacao.autorizado_cron({"Authorization": "Bearer abc123"}, {}))
            self.assertFalse(autorizacao.autorizado_cron({}, {"chave": ["abc12"]}))
            self.assertFalse(autorizacao.autorizado_cron({}, {}))

    def test_sem_senha_configurada_nega_tudo(self):
        with mock.patch.dict(os.environ, {"CRON_SECRET": "  "}):
            self.assertFalse(autorizacao.autorizado_cron({}, {"chave": [""]}))
            self.assertEqual(autorizacao.token_webhook_whatsapp(), "")


if __name__ == "__main__":
    unittest.main()
