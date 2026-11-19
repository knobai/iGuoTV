import hashlib
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from sign_tvos import BUNDLE_ID, TEAM_ID, validate_profile


class ProfileTests(unittest.TestCase):
    def fixture(self, prefix=TEAM_ID):
        return {
            'ApplicationIdentifierPrefix': [prefix],
            'TeamIdentifier': [TEAM_ID],
            'ExpirationDate': datetime.now(timezone.utc) + timedelta(days=1),
            'ProvisionedDevices': ['fixture-device'],
            'DeveloperCertificates': [b'fixture-certificate'],
            'Entitlements': {
                'application-identifier': f'{prefix}.{BUNDLE_ID}',
                'com.apple.developer.team-identifier': TEAM_ID,
                'get-task-allow': False,
            },
        }

    def test_preserves_full_identifier_and_team(self):
        for prefix in (TEAM_ID, 'ABCDE12345'):
            with self.subTest(prefix=prefix):
                profile = self.fixture(prefix)
                entitlements, hashes = validate_profile(profile)
                self.assertEqual(entitlements, profile['Entitlements'])
                self.assertEqual(hashes, {hashlib.sha1(b'fixture-certificate').hexdigest().upper()})

    @patch('builtins.print')
    def test_rejects_bare_foreign_and_wildcard_ids(self, _print):
        for app_id in (BUNDLE_ID, f'ABCDE12345.{BUNDLE_ID}', f'{TEAM_ID}.other.app', f'{TEAM_ID}.*'):
            with self.subTest(app_id=app_id), self.assertRaises(SystemExit):
                profile = self.fixture()
                profile['Entitlements']['application-identifier'] = app_id
                validate_profile(profile)

    @patch('builtins.print')
    def test_rejects_wrong_profile_or_entitlement_team(self, _print):
        profile = self.fixture()
        profile['TeamIdentifier'] = ['ABCDE12345']
        with self.assertRaises(SystemExit):
            validate_profile(profile)

        profile = self.fixture()
        profile['Entitlements']['com.apple.developer.team-identifier'] = 'ABCDE12345'
        with self.assertRaises(SystemExit):
            validate_profile(profile)


if __name__ == '__main__':
    unittest.main()
