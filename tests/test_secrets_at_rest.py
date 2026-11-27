"""Nov 27 security review: secrets encrypted at rest (H3), retention that matches the policy (M5),
and an audit trail keyed with the server key and checked every day (M6)."""

import base64
import io
import json
import os
import stat
import sys
import tarfile
import tempfile
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock
from contextlib import redirect_stdout

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import audit_check
import backup
import domain
import storage
import store
import totp
import vault
from domain import trail
from seed import seed_state

KEY = "k" * 40
OTHER_KEY = "o" * 40


class TempData(unittest.TestCase):
    """A data directory of its own, and a server key in the environment."""

    key = KEY

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.patches = [mock.patch.object(storage, "DATA_DIR", self.root), mock.patch.dict(os.environ, {vault.KEY_ENV: self.key})]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()
        self.tmp.cleanup()


class VaultTests(TempData):
    def test_round_trip_and_a_fresh_nonce_each_time(self):
        a, b = vault.seal("JBSWY3DPEHPK3PXP", "totp:u1"), vault.seal("JBSWY3DPEHPK3PXP", "totp:u1")
        self.assertTrue(a.startswith("enc1:"))
        self.assertNotEqual(a, b)
        self.assertNotIn("JBSWY3DP", a)
        self.assertEqual(vault.open_sealed(a, "totp:u1"), "JBSWY3DPEHPK3PXP")
        self.assertEqual(vault.open_sealed(vault.seal("", "x"), "x"), "")
        long = "é" * 500
        self.assertEqual(vault.open_sealed(vault.seal(long, "x"), "x"), long)

    def test_plaintext_from_before_reads_as_it_is(self):
        self.assertEqual(vault.open_sealed("JBSWY3DPEHPK3PXP", "totp:u1"), "JBSWY3DPEHPK3PXP")
        self.assertFalse(vault.sealed("JBSWY3DPEHPK3PXP"))

    def test_any_change_is_refused(self):
        sealed = vault.seal("refresh-token-123", "qbo:co-1")
        raw = base64.urlsafe_b64decode(sealed[5:] + "=" * (-len(sealed[5:]) % 4))
        for i in (0, vault.NONCE, len(raw) - 1):  # the nonce, the ciphertext, the tag
            bad = bytearray(raw)
            bad[i] ^= 1
            with self.assertRaises(vault.VaultError):
                vault.open_sealed("enc1:" + base64.urlsafe_b64encode(bytes(bad)).decode(), "qbo:co-1")
        with self.assertRaises(vault.VaultError):
            vault.open_sealed(sealed[:20], "qbo:co-1")
        with self.assertRaises(vault.VaultError):
            vault.open_sealed(sealed, "qbo:co-2")  # moved to another company's field
        with self.assertRaises(vault.VaultError):
            vault.open_sealed("enc1:***", "qbo:co-1")
        with mock.patch.dict(os.environ, {vault.KEY_ENV: OTHER_KEY}):
            with self.assertRaises(vault.VaultError):
                vault.open_sealed(sealed, "qbo:co-1")

    def test_lookup_is_keyed_and_stable(self):
        mine = vault.lookup("tok", "inbound")
        self.assertEqual(mine, vault.lookup("tok", "inbound"))
        self.assertTrue(mine.startswith("h1:"))
        self.assertNotIn("tok", mine)
        self.assertNotEqual(mine, vault.lookup("tok", "other"))
        with mock.patch.dict(os.environ, {vault.KEY_ENV: OTHER_KEY}):
            self.assertNotEqual(mine, vault.lookup("tok", "inbound"))

    def test_files_round_trip_across_chunks_and_refuse_tampering(self):
        src, enc, out = self.root / "a.bin", self.root / "a.enc", self.root / "a.out"
        data = os.urandom(5000)
        src.write_bytes(data)
        with mock.patch.object(vault, "CHUNK", 1024):
            vault.encrypt_file(src, enc)
            self.assertTrue(vault.is_encrypted_file(enc))
            self.assertNotIn(data[:64], enc.read_bytes())
            vault.decrypt_file(enc, out)
            self.assertEqual(out.read_bytes(), data)
            for mangle in (lambda b: b[:100] + bytes([b[100] ^ 1]) + b[101:], lambda b: b[:-1], lambda b: b[:-40]):
                enc2 = self.root / "b.enc"
                enc2.write_bytes(mangle(enc.read_bytes()))
                out.unlink(missing_ok=True)
                with self.assertRaises(vault.VaultError):
                    vault.decrypt_file(enc2, out)
                self.assertFalse(out.exists(), "nothing unchecked is left behind")
        with mock.patch.dict(os.environ, {vault.KEY_ENV: OTHER_KEY}):
            with self.assertRaises(vault.VaultError):
                vault.decrypt_file(enc, out)

    def test_production_and_staging_need_the_key(self):
        saved = dict(vault._DEV_KEY)
        vault._forget_dev_key()
        self.addCleanup(vault._DEV_KEY.update, saved)
        for env in ("production", "staging"):
            with mock.patch.dict(os.environ, {"DURACASH_ENV": env, vault.KEY_ENV: ""}):
                self.assertIn("DURACASH_SECRET_KEY is not set", vault.problem())
                with self.assertRaises(vault.KeyMissing):
                    vault.seal("x", "y")
        with mock.patch.dict(os.environ, {"DURACASH_ENV": "production", vault.KEY_ENV: "short"}):
            self.assertIn("shorter than 32", vault.problem())
        with mock.patch.dict(os.environ, {"DURACASH_ENV": "production", vault.KEY_ENV: KEY}):
            self.assertIsNone(vault.problem())
        self.assertFalse((self.root / vault.KEY_FILE).exists(), "production never makes a key of its own")

    def test_the_server_refuses_to_start_without_it(self):
        import server
        with mock.patch.dict(os.environ, {"DURACASH_ENV": "production", vault.KEY_ENV: ""}), \
                mock.patch.object(server, "log_json") as log, mock.patch.object(server, "Server") as srv:
            with self.assertRaises(SystemExit):
                server.main()
        self.assertIn("DURACASH_SECRET_KEY", log.call_args[1]["msg"])
        srv.assert_not_called()

    def test_a_laptop_makes_its_own_key_once(self):
        saved = dict(vault._DEV_KEY)
        vault._forget_dev_key()
        try:
            with mock.patch.dict(os.environ, {"DURACASH_ENV": "", vault.KEY_ENV: ""}):
                sealed = vault.seal("x", "y")
                path = self.root / vault.KEY_FILE
                self.assertTrue(path.exists())
                self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
                vault._forget_dev_key()
                self.assertEqual(vault.open_sealed(sealed, "y"), "x", "read again from the file")
        finally:
            vault._DEV_KEY.clear()
            vault._DEV_KEY.update(saved)


class TotpAtRestTests(TempData):
    def setUp(self):
        super().setUp()
        import auth
        self.auth = auth
        self.patches.append(mock.patch.object(auth, "AUTH_PATH", self.root / "auth.json"))
        self.patches[-1].start()

    def test_the_key_is_kept_encrypted_and_still_works(self):
        user = {"id": "u1", "email": "a@b.example", "company_id": "co-1", "role": "owner"}
        storage.write_json(self.auth.AUTH_PATH, {"users": [user], "sessions": {}, "sessions_hashed": True})
        with mock.patch.object(self.auth, "check_password", return_value=True):  # sec1: it takes the password
            secret = self.auth.totp_begin("u1", "pw")["secret"]
        on_disk = json.loads(self.auth.AUTH_PATH.read_text())["users"][0]
        self.assertTrue(on_disk["totp_pending"].startswith("enc1:"))
        self.assertNotIn(secret, self.auth.AUTH_PATH.read_text())
        self.auth.totp_enable("u1", totp.code_at(secret, int(time.time() // 30)))
        on_disk = json.loads(self.auth.AUTH_PATH.read_text())["users"][0]
        self.assertTrue(on_disk["totp_secret"].startswith("enc1:"))
        self.assertTrue(self.auth.totp_check("u1", totp.code_at(secret, int(time.time() // 30) + 1)))

    def test_a_plaintext_key_from_before_works_and_is_encrypted_at_start_up(self):
        secret = totp.new_secret()
        user = {"id": "u1", "email": "a@b.example", "company_id": "co-1", "role": "owner",
                "totp_enabled": True, "totp_secret": secret, "totp_last": -1, "totp_recovery": []}
        storage.write_json(self.auth.AUTH_PATH, {"users": [user], "sessions": {}, "sessions_hashed": True})
        self.assertTrue(self.auth.totp_check("u1", totp.code_at(secret, int(time.time() // 30))))
        self.auth.upgrade_file()
        self.assertNotIn(secret, self.auth.AUTH_PATH.read_text())
        self.assertTrue(self.auth.totp_check("u1", totp.code_at(secret, int(time.time() // 30) + 1)))

    def test_a_changed_key_is_refused_with_plain_words(self):
        user = {"id": "u1", "email": "a@b.example", "company_id": "co-1", "role": "owner", "totp_enabled": True,
                "totp_secret": vault.seal(totp.new_secret(), "totp:someone-else"), "totp_last": -1}
        storage.write_json(self.auth.AUTH_PATH, {"users": [user], "sessions": {}, "sessions_hashed": True})
        with self.assertRaisesRegex(self.auth.AuthError, "can't be checked"):
            self.auth.totp_check("u1", "123456")


class QboAtRestTests(TempData):
    def test_tokens_from_before_read_and_are_encrypted_on_the_next_save(self):
        import qbo
        storage.write_json(self.root / "qbo_connections.json", {"co-1": {"access_token": "A1", "refresh_token": "R1", "realm_id": "9"}})
        conn = qbo.connection("co-1")
        self.assertEqual((conn["access_token"], conn["refresh_token"]), ("A1", "R1"))
        qbo._store("co-1", conn)
        text = (self.root / "qbo_connections.json").read_text()
        self.assertNotIn('"R1"', text)
        self.assertNotIn('"A1"', text)
        self.assertEqual(qbo.connection("co-1")["refresh_token"], "R1")
        self.assertTrue(qbo.status("co-1")["connected"])


class InboundTokenTests(TempData):
    def test_file_keeps_hashes_and_an_old_file_is_converted(self):
        import importer
        storage.write_json(importer.tokens_path(), {"OLDTOKEN": {"company_id": "co-1", "kind": "email", "created_at": "2026-01-01"}})
        self.assertEqual(importer.company_for_token("OLDTOKEN", "email"), "co-1", "an address from before still works")
        self.assertIsNone(importer.company_for_token("OLDTOKEN", "sms"))
        mine = importer.company_tokens("co-1")
        self.assertEqual(mine["email"], "OLDTOKEN")
        text = importer.tokens_path().read_text()
        for tok in mine.values():
            self.assertNotIn(tok, text)
        self.assertEqual(importer.company_for_token(mine["sms"], "sms"), "co-1")
        new = importer.rotate_token("co-1", "email")
        self.assertIsNone(importer.company_for_token("OLDTOKEN", "email"))
        self.assertEqual(importer.company_for_token(new, "email"), "co-1")
        self.assertIsNone(importer.company_for_token("", "email"))
        importer.forget_company("co-1")
        self.assertIsNone(importer.company_for_token(new, "email"))


class EncryptedBackupTests(TempData):
    def setUp(self):
        super().setUp()
        (self.root / "auth.json").write_text(json.dumps({"users": [{"id": "u1", "company_id": "co-1"}], "sessions": {}}))
        store.SqliteStore(self.root / "companies").save("co-1", seed_state())
        self.addCleanup(store._STORES.clear)

    def test_a_backup_is_encrypted_and_restores(self):
        made = backup.make_backup()
        self.assertTrue(made.name.endswith(".tar.gz.enc"))
        self.assertTrue(vault.is_encrypted_file(made))
        with self.assertRaises(tarfile.ReadError):
            tarfile.open(made)
        self.assertEqual(backup.backups(), [made])
        with backup.open_archive(made) as tar:
            self.assertIn("auth.json", tar.getnames())
        (self.root / "auth.json").write_text(json.dumps({"users": [], "sessions": {}}))
        aside = backup.restore(made)
        self.assertEqual(json.loads((self.root / "auth.json").read_text())["users"][0]["id"], "u1")
        self.assertTrue((aside / "auth.json").exists())
        self.assertFalse(list(self.root.glob(".restore-staging-*")), "the decrypted archive is removed")
        report, problems = backup.drill(made)
        self.assertEqual(problems, [], report)

    def test_a_changed_backup_or_another_key_is_refused_and_nothing_moves(self):
        made = backup.make_backup()
        raw = bytearray(made.read_bytes())
        raw[len(raw) // 2] ^= 1
        bad = self.root / "backups" / "duracash-20990101T000000000000Z.tar.gz.enc"
        bad.write_bytes(bytes(raw))
        before = (self.root / "auth.json").read_text()
        with self.assertRaises(vault.VaultError):
            backup.restore(bad)
        with mock.patch.dict(os.environ, {vault.KEY_ENV: OTHER_KEY}):
            with self.assertRaises(vault.VaultError):
                backup.restore(made)
        self.assertEqual((self.root / "auth.json").read_text(), before)
        self.assertFalse(list(self.root.glob("pre-restore-*")))
        self.assertFalse(list(self.root.glob(".restore-staging-*")))

    def test_a_plain_backup_from_before_still_restores(self):
        made = backup.make_backup()
        with backup.open_archive(made) as tar:
            members = [(m, tar.extractfile(m).read()) for m in tar.getmembers()]
        old = self.root / "backups" / "duracash-20261101T000000000000Z.tar.gz"
        with tarfile.open(old, "w:gz") as tar:
            for m, data in members:
                tar.addfile(m, io.BytesIO(data))
        self.assertIn(old, backup.backups())
        backup.restore(old)
        self.assertTrue((self.root / "companies").exists())
        self.assertEqual(backup.drill(old)[1], [])

    def test_off_site_gets_the_plain_archive_for_age(self):
        made = backup.make_backup()
        sent = []

        def upload(path):
            with tarfile.open(path) as tar:  # plain: age encrypts it in offsite.upload
                sent.append((path.name, tar.getnames()))
            return "duracash/" + path.name + ".age"

        with mock.patch.object(backup.offsite, "upload", upload):
            backup.offsite_copy(made)
        self.assertEqual(sent[0][0], made.name[:-len(".enc")])
        self.assertIn("auth.json", sent[0][1])
        self.assertFalse(list((self.root / "backups" / "offsite-staging").glob("*")), "the plain copy is removed")


def _stamp(days_ago: float) -> str:
    return datetime.fromtimestamp(time.time() - days_ago * 86400, timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")


class RetentionTests(TempData):
    def test_backups_rotate_by_age_and_by_count(self):
        folder = self.root / "backups"
        folder.mkdir()
        for days in (0.1, 1, 13, 15, 40):
            (folder / f"duracash-{_stamp(days)}.tar.gz.enc").write_bytes(b"x")
        (folder / f"{backup.OLD_PREFIX}{_stamp(20)}.tar.gz").write_bytes(b"x")
        backup.rotate()
        self.assertEqual(len(backup.backups()), 3, "15, 20 and 40 days old are gone")
        with mock.patch.dict(os.environ, {"BACKUP_KEEP": "2"}):
            backup.rotate()
        self.assertEqual(len(backup.backups()), 2)
        with mock.patch.dict(os.environ, {"BACKUP_KEEP_DAYS": "1"}):
            backup.rotate()
        self.assertEqual(len(backup.backups()), 1)

    def test_purge_removes_old_restore_copies_and_set_aside_files(self):
        old_aside = self.root / f"pre-restore-{_stamp(20)[:15]}Z"
        new_aside = self.root / f"pre-restore-{_stamp(1)[:15]}Z"
        for d in (old_aside, new_aside):
            (d / "companies").mkdir(parents=True)
            (d / "auth.json").write_text("{}")
        bad = self.root / "sms_sends.json.bad-123"
        bad.write_text("{")
        (self.root / "companies").mkdir()
        bad_company = self.root / "companies" / "co-1.json.bad-456"
        bad_company.write_text("{")
        keep = self.root / "auth.json"
        keep.write_text("{}")
        gone = backup.purge()
        self.assertIn(old_aside, gone)
        self.assertFalse(old_aside.exists())
        self.assertTrue(new_aside.exists())
        self.assertTrue(bad.exists(), "a copy made just now stays for a person to look at")
        backup.purge(now=time.time() + 15 * 86400)
        self.assertFalse(bad.exists())
        self.assertFalse(bad_company.exists())
        self.assertFalse(new_aside.exists())
        self.assertTrue(keep.exists())

    def test_the_sweep_runs_with_backups_off(self):
        calls = []

        class Inline:
            def __init__(self, target, **kw):
                self.target = target

            def start(self):
                try:
                    self.target()
                except StopIteration:
                    pass

        def sleep(_):
            raise StopIteration

        with mock.patch.dict(os.environ, {"BACKUP_HOURS": "0"}), mock.patch.object(backup, "purge", lambda: calls.append(1) or []), \
                mock.patch.object(backup.threading, "Thread", Inline), mock.patch.object(backup.time, "sleep", sleep), \
                mock.patch.object(backup, "make_backup") as made:
            backup.start_scheduler({}, log=lambda m: None)
        self.assertEqual(calls, [1])
        made.assert_not_called()

    def test_the_policy_states_the_retention(self):
        import legal
        with mock.patch.dict(os.environ, {"BACKUP_KEEP_DAYS": "10", "BACKUP_OFFSITE_DAYS": "30"}):
            d = legal.details()
            self.assertEqual((d["BACKUP_DAYS"], d["OFFSITE_DAYS"]), ("10", "30"))
            text = legal.page("privacy")
        self.assertIn("on our server within 10 days", text)
        self.assertIn("off-site copies within 30 days", text)
        self.assertIn("Stripe", text)


class StripeOnDeleteTests(TempData):
    def setUp(self):
        super().setUp()
        import auth
        import importer
        import server
        self.server = server
        for p in (mock.patch.object(server, "COMPANIES", self.root / "companies"), mock.patch.object(auth, "AUTH_PATH", self.root / "auth.json"),
                  mock.patch.object(importer, "UPLOADS", self.root / "uploads")):
            p.start()
            self.patches.append(p)
        self.addCleanup(store._STORES.clear)
        (self.root / "auth.json").write_text(json.dumps({"users": [], "sessions": {}}))
        state = seed_state()
        state["company"]["billing"] = {"customer_id": "cus_123"}
        server.save_company("co-1", state)

    def test_deleting_a_company_deletes_its_stripe_customer(self):
        calls = []
        with mock.patch.dict(os.environ, {"STRIPE_SECRET_KEY": "sk_test_x"}), \
                mock.patch.object(self.server.billing, "_post", lambda path, data, method="POST": calls.append((method, path)) or {}):
            self.server.delete_company_data("co-1")
        self.assertEqual(calls, [("DELETE", "/v1/customers/cus_123")])
        self.assertFalse(self.server.company_exists("co-1"))

    def test_a_stripe_failure_is_alerted_and_the_deletion_stands(self):
        def refuse(*a, **kw):
            raise self.server.billing.BillingError("Stripe refused it (500).")

        with mock.patch.dict(os.environ, {"STRIPE_SECRET_KEY": "sk_test_x"}), mock.patch.object(self.server.billing, "_post", refuse), \
                mock.patch.object(self.server.alerts, "notify") as notify, mock.patch.object(self.server, "log_json"):
            self.server.delete_company_data("co-1")
        self.assertIn("Stripe", notify.call_args[0][1])
        self.assertFalse(self.server.company_exists("co-1"))

    def test_without_billing_nothing_is_called(self):
        with mock.patch.dict(os.environ, {"STRIPE_SECRET_KEY": ""}), mock.patch.object(self.server.billing, "_post") as post:
            self.server.delete_company_data("co-1")
        post.assert_not_called()


def _signed_state():
    return {"jobs": [{"id": "j1", "change_orders": [{"id": "co1", "signature": {"name": "Pat Payer", "at": "2026-11-27T10:00:00+00:00", "ip": "1.2.3.4"}}],
                      "invoices": [{"id": "i1", "approvals": [{"id": "u1", "signed_name": "Dana Owner", "at": "2026-11-27T11:00:00+00:00"}]}]}],
            "audit": []}


def _legacy_rows(n):
    rows, prev = [], ""
    for i in range(n):
        row = {"id": f"aud-{i + 1}", "at": "2026-01-01T00:00:00+00:00", "job_id": "j1", "actor": "owner", "action": "note", "detail": f"row {i}", "prev": prev}
        row["hash"] = trail._audit_hash(prev, row)
        rows.append(row)
        prev = row["hash"]
    return rows


def _rebuild_unkeyed(rows):
    """What someone with the file and the code but not the key can do: rewrite every row as version 1."""
    prev = ""
    for row in rows:
        for k in ("v", "record", "record_sha256"):
            row.pop(k, None)
        row["prev"] = prev
        row["hash"] = trail._audit_hash(prev, row)
        prev = row["hash"]


class KeyedAuditTests(TempData):
    def test_new_rows_are_keyed_and_cant_be_rebuilt_without_the_key(self):
        state = seed_state()
        domain.audit(state, state["jobs"][0]["id"], "note", "one", "owner")
        self.assertEqual(state["audit"][-1]["v"], 2)
        self.assertTrue(domain.check_audit(state)["keyed"])
        state["audit"][-1]["detail"] = "changed"
        with mock.patch.dict(os.environ, {vault.KEY_ENV: OTHER_KEY}):  # rebuilt with a guessed key
            prev = state["audit"][-2]["hash"]
            state["audit"][-1]["hash"] = trail._keyed_hash(prev, state["audit"][-1])
        self.assertEqual(domain.check_audit(state)["broken_at"], state["audit"][-1]["id"])

    def test_an_old_chain_still_verifies_and_the_first_keyed_row_seals_it(self):
        state = {"jobs": [], "audit": _legacy_rows(3)}
        self.assertEqual(domain.check_audit(state), {"ok": True, "rows": 3, "broken_at": None, "keyed": False})
        domain.audit(state, "j1", "note", "after the key", "owner")
        self.assertTrue(domain.check_audit(state)["ok"])
        # Editing an old row and rebuilding the old part of the chain breaks the first keyed row.
        state["audit"][1]["detail"] = "changed"
        prev = ""
        for row in state["audit"][:3]:
            row["prev"], row["hash"] = prev, trail._audit_hash(prev, row)
            prev = row["hash"]
        state["audit"][3]["prev"] = prev
        self.assertEqual(domain.check_audit(state)["broken_at"], "aud-4")

    def test_no_going_back_to_unkeyed_rows(self):
        state = {"jobs": [], "audit": _legacy_rows(2)}
        domain.audit(state, "j1", "note", "keyed", "owner")
        state["audit"].append(_legacy_rows(4)[3])
        row = state["audit"][-1]
        row["prev"] = state["audit"][-2]["hash"]
        row["hash"] = trail._audit_hash(row["prev"], row)
        self.assertEqual(domain.check_audit(state)["broken_at"], row["id"])

    def test_signature_records_are_in_the_chain(self):
        state = _signed_state()
        job = state["jobs"][0]
        domain.audit(state, "j1", "approve_co", "signed online", "Pat", record=("co-signature:j1:co1", job["change_orders"][0]["signature"]))
        domain.audit(state, "j1", "payapp_approved", "signed", "Dana", record=("approval:j1:i1:u1", job["invoices"][0]["approvals"][0]))
        self.assertTrue(domain.check_audit(state)["ok"])
        job["invoices"][0]["approvals"][0]["signed_name"] = "Someone Else"
        self.assertEqual(domain.check_audit(state)["broken_at"], "aud-2")
        job["invoices"][0]["approvals"][0]["signed_name"] = "Dana Owner"
        job["change_orders"][0]["signature"]["ip"] = "9.9.9.9"
        self.assertEqual(domain.check_audit(state)["broken_at"], "aud-1")
        state["audit"][0]["record_sha256"] = trail.record_sha256(job["change_orders"][0]["signature"])
        self.assertEqual(domain.check_audit(state)["broken_at"], "aud-1", "the row's hash covers the record's hash")

    def test_signing_a_change_order_and_approving_record_it(self):
        state = seed_state()
        job = next(j for j in state["jobs"] if j.get("change_orders"))
        co = job["change_orders"][0]
        co.update(status="pending", amount_cents=co.get("amount_cents") or 100)
        try:
            domain.sign_change_order(state, job, co, "Pat Payer", True, "1.2.3.4", "test")
        except domain.GateError as exc:
            self.skipTest(f"the seed's change order can't be signed here: {exc}")
        row = next(r for r in reversed(state["audit"]) if r.get("action") == "approve_co")
        self.assertEqual(row["record"], f"co-signature:{job['id']}:{co['id']}")
        self.assertTrue(domain.check_audit(state)["ok"])
        co["signature"]["name"] = "Not Pat"
        self.assertFalse(domain.check_audit(state)["ok"])


class AuditAnchorTests(TempData):
    def test_a_rebuilt_or_shortened_trail_is_caught_by_the_anchor(self):
        state = seed_state()
        for n in range(3):
            domain.audit(state, state["jobs"][0]["id"], "note", f"row {n}", "owner")
        [(_, first)] = audit_check.check_all([("co-1", state)])
        self.assertTrue(first["ok"])
        rebuilt = json.loads(json.dumps(state))
        rebuilt["audit"][-1]["detail"] = "changed"
        _rebuild_unkeyed(rebuilt["audit"])
        self.assertTrue(domain.check_audit(rebuilt)["ok"], "on its own the unkeyed form passes")
        [(_, result)] = audit_check.check_all([("co-1", rebuilt)])
        self.assertFalse(result["ok"])
        short = json.loads(json.dumps(state))
        short["audit"].pop()
        self.assertFalse(audit_check.check_all([("co-1", short)])[0][1]["ok"])
        self.assertTrue(audit_check.check_all([("co-1", state)])[0][1]["ok"], "the real trail still passes")
        anchors = json.loads(audit_check.anchors_path().read_text())
        anchors["companies"]["co-1"]["rows"] = 1
        storage.write_json(audit_check.anchors_path(), anchors)
        self.assertIn("anchor", audit_check.check_all([("co-1", state)])[0][1]["broken_at"])

    def test_taking_a_companys_anchor_out_is_caught(self):  # Nov 27 hunt
        state, other = seed_state(), seed_state()
        self.assertTrue(all(r["ok"] for _, r in audit_check.check_all([("co-1", state), ("co-2", other)])))
        anchors = json.loads(audit_check.anchors_path().read_text())
        del anchors["companies"]["co-1"]
        storage.write_json(audit_check.anchors_path(), anchors)
        results = dict(audit_check.check_all([("co-1", state), ("co-2", other)]))
        self.assertFalse(results["co-1"]["ok"])
        self.assertIn("anchors file", results["co-1"]["broken_at"])
        self.assertFalse(dict(audit_check.check_all([("co-1", state)]))["co-1"]["ok"], "and stays reported: the file isn't rewritten")
        # The whole file taken away is caught once the check has run before; a new server's first check isn't.
        audit_check.anchors_path().unlink()
        self.assertFalse(dict(audit_check.check_all([("co-1", state)], expect_file=True))["co-1"]["ok"])
        self.assertTrue(dict(audit_check.check_all([("co-1", state)]))["co-1"]["ok"])
        # After a person has looked: --accept anchors afresh.
        broken = json.loads(audit_check.anchors_path().read_text())
        broken["companies"] = {}
        storage.write_json(audit_check.anchors_path(), broken)
        self.assertFalse(dict(audit_check.check_all([("co-1", state)]))["co-1"]["ok"])
        with redirect_stdout(io.StringIO()):
            audit_check.main(["--accept"])
        self.assertTrue(dict(audit_check.check_all([("co-1", state)]))["co-1"]["ok"])

    def test_an_anchors_file_in_the_first_unsigned_form_is_read_and_signed(self):
        state = seed_state()
        audit_check.check_all([("co-1", state)])
        signed = json.loads(audit_check.anchors_path().read_text())
        storage.write_json(audit_check.anchors_path(), signed["companies"])   # as Nov 27's first version wrote it
        self.assertTrue(dict(audit_check.check_all([("co-1", state)]))["co-1"]["ok"])
        self.assertIn("mac", json.loads(audit_check.anchors_path().read_text()))

    def test_the_daily_check_alerts_once_a_day(self):
        good, bad = seed_state(), seed_state()
        bad["audit"][0]["detail"] = "changed"

        class Site:
            def company_ids(self):
                return ["co-good", "co-bad", "co-gone"]

            def deleted(self):
                return {"co-gone"}

            def read(self, company_id):
                return {"co-good": good, "co-bad": bad}[company_id]

            def log(self, *a, **kw):
                pass

        now = datetime(2026, 11, 27, 3, tzinfo=timezone.utc)
        with mock.patch("alerts.notify") as notify:
            lines = audit_check.run_daily(Site(), now)
            self.assertIn("2 companies, 1 broken", lines[0])
            self.assertEqual([c[0][0] for c in notify.call_args_list], ["audit-chain:co-bad"])
            self.assertEqual(audit_check.run_daily(Site(), now), [], "once a day")
            self.assertEqual(notify.call_count, 1)

    def test_the_hourly_loop_runs_it(self):
        import monthly
        site = mock.Mock()
        with mock.patch.object(monthly, "run_monthly_jobs", return_value=[]), mock.patch.object(monthly, "send_daily_digests", return_value=[]), \
                mock.patch.object(monthly.reminders, "run", return_value=[]), mock.patch("audit_check.run_daily", return_value=["audit check: 0 companies, 0 broken"]) as run:
            monthly.run_once(site)
        run.assert_called_once()


if __name__ == "__main__":
    unittest.main()
