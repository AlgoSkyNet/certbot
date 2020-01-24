"""Test for certbot_apache._internal.configurator OCSP Prefetching functionality"""
import base64
import json
import unittest
import sys

import mock
# six is used in mock.patch()
import six  # pylint: disable=unused-import

from acme.magic_typing import Dict, List, Set, Union  # pylint: disable=unused-import, no-name-in-module

from certbot import errors

from certbot.compat import os
import util



class MockDBM(object):
    # pylint: disable=missing-docstring
    """Main mock DBM class for Py3 dbm module"""
    def __init__(self, library='Berkeley DB'):
        self.ndbm = Mockdbm_impl(library)


class Mockdbm_impl(object):
    """Mock dbm implementation that satisfies both bsddb and dbm interfaces"""
    # pylint: disable=missing-docstring

    def __init__(self, library='Berkeley DB'):
        self.library = library
        self.name = 'ndbm'

    def open(self, path, mode):
        return Mockdb(path, mode)

    def hashopen(self, path, mode):
        return Mockdb(path, mode)


class Mockdb(object):
    """Mock dbm.db for both bsddb and dbm databases"""
    # pylint: disable=missing-docstring
    def __init__(self, path, mode):
        self._data = dict()  # type: Dict[str, str]
        if mode == "r" or mode == "w":
            if not path.endswith(".db"):
                path = path+".db"
            with open(path, 'r') as fh:
                try:
                    self._data = json.loads(fh.read())
                except Exception:  # pylint: disable=broad-except
                    self._data = dict()
        self.path = path
        self.mode = mode

    def __setitem__(self, key, item):
        bkey = base64.b64encode(key)
        bitem = base64.b64encode(item)
        self._data[bkey.decode()] = bitem.decode()

    def __getitem__(self, key):
        bkey = base64.b64encode(key)
        return base64.b64decode(self._data[bkey.decode()])

    def keys(self):
        return [base64.b64decode(k) for k in self._data.keys()]

    def sync(self):
        return

    def close(self):
        with open(self.path, 'w') as fh:
            fh.write(json.dumps(self._data))


class OCSPPrefetchTest(util.ApacheTest):
    """Tests for OCSP Prefetch feature"""
    # pylint: disable=protected-access

    def setUp(self):  # pylint: disable=arguments-differ
        super(OCSPPrefetchTest, self).setUp()

        self.config = util.get_apache_configurator(
            self.config_path, self.vhost_path, self.config_dir, self.work_dir,
            os_info="debian")

        self.lineage = mock.MagicMock(cert_path="cert", chain_path="chain")
        self.config.parser.modules.add("headers_module")
        self.config.parser.modules.add("mod_headers.c")
        self.config.parser.modules.add("ssl_module")
        self.config.parser.modules.add("mod_ssl.c")
        self.config.parser.modules.add("socache_dbm_module")
        self.config.parser.modules.add("mod_socache_dbm.c")

        self.vh_truth = util.get_vh_truth(
            self.temp_dir, "debian_apache_2_4/multiple_vhosts")
        self.config._ensure_ocsp_dirs()
        self.db_path = os.path.join(self.config_dir, "ocsp", "ocsp_cache")
        self.db_fullpath = self.db_path + ".db"

    def _call_mocked(self, func, *args, **kwargs):
        """Helper method to call functins with mock stack"""

        db_fullpath = self.db_path + ".db"

        def mock_restart():
            """Mock ApacheConfigurator.restart that creates the dbm file"""
            # Mock the Apache dbm file creation
            open(db_fullpath, 'a').close()

        ver_path = "certbot_apache._internal.configurator.ApacheConfigurator.get_version"
        res_path = "certbot_apache._internal.prefetch_ocsp.OCSPPrefetchMixin.restart"
        cry_path = "certbot.crypto_util.cert_sha1_fingerprint"

        with mock.patch(ver_path) as mock_ver:
            mock_ver.return_value = (2, 4, 10)
            with mock.patch(cry_path) as mock_cry:
                mock_cry.return_value = b'j\x056\x1f\xfa\x08B\xe8D\xa1Bn\xeb*A\xebWx\xdd\xfe'
                with mock.patch(res_path, side_effect=mock_restart):
                    return func(*args, **kwargs)

    def call_mocked_py2(self, func, *args, **kwargs):
        """Calls methods with imports mocked to suit Py2 environment"""
        if 'dbm' in sys.modules.keys():  # pragma: no cover
            sys.modules['dbm'] = None
        sys.modules['bsddb'] = Mockdbm_impl()
        return self._call_mocked(func, *args, **kwargs)

    def call_mocked_py3(self, func, *args, **kwargs):
        """Calls methods with imports mocked to suit Py3 environment"""
        real_import = six.moves.builtins.__import__

        def mock_import(*args, **kwargs):
            """Mock import to raise ImportError for Py2 specific module to make
            ApacheConfigurator pick the correct one for Python3 regardless of the
            python version the tests are running under."""
            if args[0] == "bsddb":
                raise ImportError
            return real_import(*args, **kwargs)

        with mock.patch('six.moves.builtins.__import__', side_effect=mock_import):
            sys.modules['dbm'] = MockDBM()
            return self._call_mocked(func, *args, **kwargs)

    @mock.patch("certbot_apache._internal.override_debian.DebianConfigurator.enable_mod")
    def test_ocsp_prefetch_enable_mods(self, mock_enable):
        self.config.parser.modules.discard("socache_dbm_module")
        self.config.parser.modules.discard("mod_socache_dbm.c")
        self.config.parser.modules.discard("headers_module")
        self.config.parser.modules.discard("mod_header.c")

        ref_path = "certbot_apache._internal.override_debian.DebianConfigurator._ocsp_refresh"
        with mock.patch(ref_path):
            self.call_mocked_py2(self.config.enable_ocsp_prefetch,
                                 self.lineage, ["ocspvhost.com"])
        self.assertTrue(mock_enable.called)
        self.assertEqual(len(self.config._ocsp_prefetch), 1)

    @mock.patch("certbot_apache._internal.override_debian.DebianConfigurator.enable_mod")
    def test_ocsp_prefetch_enable_error(self, _mock_enable):
        ref_path = "certbot_apache._internal.override_debian.DebianConfigurator._ocsp_refresh"
        self.config.recovery_routine = mock.MagicMock()
        with mock.patch(ref_path, side_effect=errors.PluginError("failed")):
            self.assertRaises(errors.PluginError,
                              self.call_mocked_py2,
                              self.config.enable_ocsp_prefetch,
                              self.lineage,
                              ["ocspvhost.com"])
        self.assertTrue(self.config.recovery_routine.called)

    @mock.patch("certbot_apache._internal.constants.OCSP_INTERNAL_TTL", 0)
    def test_ocsp_prefetch_refresh(self):
        def ocsp_req_mock(_cert, _chain, workfile):
            """Method to mock the OCSP request and write response to file"""
            with open(workfile, 'w') as fh:
                fh.write("MOCKRESPONSE")
            return False

        ocsp_path = "certbot._internal.ocsp.RevocationChecker.revoked"
        with mock.patch(ocsp_path, side_effect=ocsp_req_mock):
            self.call_mocked_py2(self.config.enable_ocsp_prefetch,
                                 self.lineage, ["ocspvhost.com"])
        odbm = self.config._ocsp_dbm_open(self.db_path)
        self.assertEqual(len(odbm.keys()), 1)
        # The actual response data is prepended by Apache timestamp
        self.assertTrue(odbm[list(odbm.keys())[0]].endswith(b'MOCKRESPONSE'))
        self.config._ocsp_dbm_close(odbm)

        with mock.patch(ocsp_path, side_effect=ocsp_req_mock) as mock_ocsp:
            self.call_mocked_py2(self.config.update_ocsp_prefetch, None)
            self.assertTrue(mock_ocsp.called)

    def test_ocsp_prefetch_refresh_noop(self):
        def ocsp_req_mock(_cert, _chain, workfile):
            """Method to mock the OCSP request and write response to file"""
            with open(workfile, 'w') as fh:
                fh.write("MOCKRESPONSE")
            return True

        ocsp_path = "certbot._internal.ocsp.RevocationChecker.revoked"
        with mock.patch(ocsp_path, side_effect=ocsp_req_mock):
            self.call_mocked_py2(self.config.enable_ocsp_prefetch,
                                 self.lineage, ["ocspvhost.com"])
        self.assertEqual(len(self.config._ocsp_prefetch), 1)
        refresh_path = "certbot_apache._internal.override_debian.DebianConfigurator._ocsp_refresh"
        with mock.patch(refresh_path) as mock_refresh:
            self.call_mocked_py2(self.config.update_ocsp_prefetch, None)
            self.assertFalse(mock_refresh.called)

    @mock.patch("certbot_apache._internal.configurator.ApacheConfigurator.config_test")
    def test_ocsp_prefetch_backup_db(self, _mock_test):
        def ocsp_del_db():
            """Side effect of _reload() that deletes the DBM file, like Apache
            does when restarting"""
            os.remove(self.db_fullpath)
            self.assertFalse(os.path.isfile(self.db_fullpath))

        # Make sure that the db file exists
        open(self.db_fullpath, 'a').close()
        odbm = self.call_mocked_py2(self.config._ocsp_dbm_open, self.db_path)
        odbm[b'mock_key'] = b'mock_value'
        self.config._ocsp_dbm_close(odbm)

        # Mock OCSP prefetch dict to signify that there should be a db
        self.config._ocsp_prefetch = {"mock": "value"}
        rel_path = "certbot_apache._internal.configurator.ApacheConfigurator._reload"
        with mock.patch(rel_path, side_effect=ocsp_del_db):
            self.config.restart()

        odbm = self.config._ocsp_dbm_open(self.db_path)
        self.assertEqual(odbm[b'mock_key'], b'mock_value')
        self.config._ocsp_dbm_close(odbm)

    @mock.patch("certbot_apache._internal.configurator.ApacheConfigurator.config_test")
    @mock.patch("certbot_apache._internal.configurator.ApacheConfigurator._reload")
    def test_ocsp_prefetch_backup_db_error(self, _mock_reload, _mock_test):
        log_path = "certbot_apache._internal.prefetch_ocsp.logger.debug"
        log_string = "Encountered an issue while trying to backup OCSP dbm file"
        log_string2 = "Encountered an issue when trying to restore OCSP dbm file"
        self.config._ocsp_prefetch = {"mock": "value"}
        with mock.patch("shutil.copy2", side_effect=IOError):
            with mock.patch(log_path) as mock_log:
                self.config.restart()
                self.assertTrue(mock_log.called)
                self.assertEqual(mock_log.call_count, 2)
                self.assertTrue(log_string in mock_log.call_args_list[0][0][0])
                self.assertTrue(log_string2 in mock_log.call_args_list[1][0][0])

    @mock.patch("certbot_apache._internal.prefetch_ocsp.OCSPPrefetchMixin.restart")
    def test_ocsp_prefetch_refresh_fail(self, _mock_restart):
        ocsp_path = "certbot._internal.ocsp.RevocationChecker.revoked"
        log_path = "certbot_apache._internal.prefetch_ocsp.logger.warning"
        with mock.patch(ocsp_path) as mock_ocsp:
            mock_ocsp.return_value = True
            with mock.patch(log_path) as mock_log:
                self.call_mocked_py2(self.config.enable_ocsp_prefetch,
                                     self.lineage, ["ocspvhost.com"])
                self.assertTrue(mock_log.called)
                self.assertTrue(
                    "trying to prefetch OCSP" in mock_log.call_args[0][0])

    @mock.patch("certbot_apache._internal.override_debian.DebianConfigurator._ocsp_refresh_if_needed")
    def test_ocsp_prefetch_update_noop(self, mock_refresh):
        self.config.update_ocsp_prefetch(None)
        self.assertFalse(mock_refresh.called)

    def test_ocsp_prefetch_preflight_check_noerror(self):
        self.call_mocked_py2(self.config._ensure_ocsp_prefetch_compatibility)
        self.call_mocked_py3(self.config._ensure_ocsp_prefetch_compatibility)

        real_import = six.moves.builtins.__import__

        def mock_import(*args, **kwargs):
            if args[0] == "bsddb":
                raise ImportError
            return real_import(*args, **kwargs)
        with mock.patch('six.moves.builtins.__import__', side_effect=mock_import):
            sys.modules['dbm'] = MockDBM(library='Not Berkeley DB')
            self.assertRaises(errors.NotSupportedError,
                              self._call_mocked,
                              self.config._ensure_ocsp_prefetch_compatibility)

    def test_ocsp_prefetch_open_dbm_no_file(self):
        open(self.db_fullpath, 'a').close()
        db_not_exists = self.db_path+"nonsense"
        self.call_mocked_py2(self.config._ocsp_dbm_open, self.db_path)
        self.assertRaises(errors.PluginError,
                          self.call_mocked_py2, self.config._ocsp_dbm_open, db_not_exists)

    def test_ocsp_prefetch_py2_open_file_error(self):
        open(self.db_fullpath, 'a').close()
        mock_db = mock.MagicMock()
        mock_db.hashopen.side_effect = Exception("error")
        sys.modules["bsddb"] = mock_db
        self.assertRaises(errors.PluginError,
                          self.config._ocsp_dbm_open,
                          self.db_path)

    def test_ocsp_prefetch_py3_open_file_error(self):
        open(self.db_fullpath, 'a').close()
        mock_db = mock.MagicMock()
        mock_db.ndbm.open.side_effect = Exception("error")
        sys.modules["dbm"] = mock_db
        sys.modules["bsddb"] = None
        self.assertRaises(errors.PluginError,
                          self.config._ocsp_dbm_open,
                          self.db_path)

    def test_ocsp_prefetch_open_close_py2_noerror(self):
        expected_val = b'whatever_value'
        open(self.db_fullpath, 'a').close()
        db = self.call_mocked_py2(
            self.config._ocsp_dbm_open, self.db_path)
        db[b'key'] = expected_val
        self.call_mocked_py2(self.config._ocsp_dbm_close, db)
        db2 = self.call_mocked_py2(self.config._ocsp_dbm_open, self.db_path)
        self.assertEqual(db2[b'key'], expected_val)

    def test_ocsp_prefetch_open_close_py3_noerror(self):
        expected_val = b'whatever_value'
        open(self.db_fullpath, 'a').close()
        db = self.call_mocked_py3(
            self.config._ocsp_dbm_open, self.db_path)
        db[b'key'] = expected_val
        self.call_mocked_py3(self.config._ocsp_dbm_close, db)
        db2 = self.call_mocked_py3(self.config._ocsp_dbm_open, self.db_path)
        self.assertEqual(db2[b'key'], expected_val)

    @mock.patch("certbot_apache._internal.prefetch_ocsp.OCSPPrefetchMixin._ocsp_prefetch_fetch_state")
    @mock.patch("certbot_apache._internal.configurator.ApacheConfigurator.config_test")
    @mock.patch("certbot_apache._internal.configurator.ApacheConfigurator._reload")
    def test_restart_load_state_call(self, _rl, _ct, mock_fch):
        self.assertFalse(self.config._ocsp_prefetch)
        self.config.restart()
        self.assertTrue(mock_fch.called)

    @mock.patch("certbot_apache._internal.prefetch_ocsp.OCSPPrefetchMixin._ocsp_prefetch_backup_db")
    @mock.patch("certbot_apache._internal.prefetch_ocsp.OCSPPrefetchMixin._ocsp_prefetch_restore_db")
    @mock.patch("certbot_apache._internal.configurator.ApacheConfigurator.config_test")
    @mock.patch("certbot_apache._internal.configurator.ApacheConfigurator._reload")
    def test_restart_backupdb_call(self, _rl, _ctest, mock_rest, mock_bck):
        self.config._ocsp_prefetch = True
        self.config.restart()
        self.assertTrue(mock_rest.called)
        self.assertTrue(mock_bck.called)

    @mock.patch("certbot_apache._internal.prefetch_ocsp.OCSPPrefetchMixin._ocsp_prefetch_backup_db")
    @mock.patch("certbot_apache._internal.prefetch_ocsp.OCSPPrefetchMixin._ocsp_prefetch_restore_db")
    @mock.patch("certbot_apache._internal.configurator.ApacheConfigurator.config_test")
    @mock.patch("certbot_apache._internal.configurator.ApacheConfigurator._reload")
    def test_restart_recover_error(self, mock_reload, _ctest, mock_rest, mock_bck):
        self.config._ocsp_prefetch = True
        mock_reload.side_effect = errors.MisconfigurationError
        self.assertRaises(errors.MisconfigurationError, self.config.restart)
        self.assertTrue(mock_bck.called)
        self.assertTrue(mock_rest.called)


if __name__ == "__main__":
    unittest.main()  # pragma: no cover