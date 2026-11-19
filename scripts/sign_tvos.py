"""Sign the fork build's unsigned tvOS IPA with an Apple Ad Hoc profile."""

import argparse
import base64
import binascii
import hashlib
import os
import plistlib
import re
import secrets
import shutil
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path

BUNDLE_ID = 'me.guoqiao.iguotv'
SECURITY = '/usr/bin/security'
CODESIGN = '/usr/bin/codesign'
DITTO = '/usr/bin/ditto'
WWDR_G3_SHA256 = 'dcf21878c77f4198e4b4614f03d696d89c66c66008d4244e1b99161aac91601f'


def run(*args, error):
    # Never echo commands or tool diagnostics that can contain signing material.
    result = subprocess.run(args, stdin=subprocess.DEVNULL, capture_output=True)
    if result.returncode:
        raise SystemExit(error)
    return result.stdout


def read_secret(name):
    value = os.environ.pop(name, '')
    if not value:
        raise SystemExit(f'Missing Actions secret {name}.')
    return value


def decode_secret(name):
    try:
        return base64.b64decode(''.join(read_secret(name).split()), validate=True)
    except (ValueError, binascii.Error):
        raise SystemExit(f'Actions secret {name} must contain base64 data.') from None


def read_plist(data, error):
    try:
        result = plistlib.loads(data)
    except (ValueError, TypeError, OverflowError, plistlib.InvalidFileException):
        raise SystemExit(error) from None
    if not isinstance(result, dict):
        raise SystemExit(error)
    return result


def validate_profile(profile, expected_team):
    team_ids = profile.get('TeamIdentifier')
    if not isinstance(team_ids, list) or len(team_ids) != 1:
        raise SystemExit('The profile must identify one Apple team.')
    team = team_ids[0]
    if not isinstance(team, str) or not re.fullmatch(r'[A-Z0-9]{10}', team):
        raise SystemExit('The profile has an invalid team ID.')
    if expected_team and team != expected_team:
        raise SystemExit('The profile team does not match TVOS_TEAM_ID.')
    expires = profile.get('ExpirationDate')
    if not isinstance(expires, datetime) or expires.replace(tzinfo=timezone.utc) <= datetime.now(timezone.utc):
        raise SystemExit('The provisioning profile is expired or has no expiration date.')
    devices = profile.get('ProvisionedDevices')
    entitlements = profile.get('Entitlements')
    platforms = profile.get('Platform')
    if (not isinstance(platforms, list) or 'tvOS' not in platforms
            or not isinstance(devices, list) or not devices or profile.get('ProvisionsAllDevices')
            or not isinstance(entitlements, dict) or entitlements.get('get-task-allow') is not False):
        raise SystemExit('Expected a tvOS Ad Hoc distribution profile with registered devices.')

    # A legacy App ID prefix can differ from the team ID. Read it from the profile
    # and require an explicit App ID for this bundle.
    app_id = entitlements.get('application-identifier')
    if not isinstance(app_id, str):
        raise SystemExit('The profile has no application identifier.')
    prefix, _, bundle_id = app_id.partition('.')
    prefixes = profile.get('ApplicationIdentifierPrefix')
    if (bundle_id != BUNDLE_ID or not re.fullmatch(r'[A-Z0-9]{10}', prefix)
            or not isinstance(prefixes, list) or prefix not in prefixes
            or entitlements.get('com.apple.developer.team-identifier') != team):
        raise SystemExit('The profile does not authorize me.guoqiao.iguotv for its team.')
    certs = profile.get('DeveloperCertificates')
    if not isinstance(certs, list) or not certs or not all(isinstance(cert, bytes) for cert in certs):
        raise SystemExit('The profile has no usable signing certificates.')
    return {
        'application-identifier': app_id,
        'com.apple.developer.team-identifier': team,
        'get-task-allow': False,
    }, {hashlib.sha1(cert).hexdigest().upper() for cert in certs}


def app_from_ipa(ipa, destination):
    run(DITTO, '-x', '-k', ipa, destination, error='Cannot extract the tvOS IPA.')
    app = destination / 'Payload' / 'iGuoTV.app'
    info_file = app / 'Info.plist'
    if not info_file.is_file() or not app.resolve().is_relative_to(destination.resolve()):
        raise SystemExit('The IPA is missing Payload/iGuoTV.app.')
    info = read_plist(info_file.read_bytes(), 'The app has an invalid Info.plist.')
    if (info.get('CFBundleIdentifier') != BUNDLE_ID or info.get('CFBundleExecutable') != 'iGuoTV'
            or info.get('CFBundleSupportedPlatforms') != ['AppleTVOS'] or not (app / 'iGuoTV').is_file()):
        raise SystemExit('The IPA does not contain the expected iGuoTV device app.')
    return app


def nested_code(app):
    # MPVKit is the only external dependency. Sign any embedded frameworks or
    # dylibs from the inside out before signing the app.
    code = [path for path in app.rglob('*') if not path.is_symlink()
            and path.suffix in ('.framework', '.dylib')]
    return sorted(code, key=lambda path: (-len(path.parts), path))


def sign(ipa, intermediate, output):
    if hashlib.sha256(intermediate.read_bytes()).hexdigest() != WWDR_G3_SHA256:
        raise SystemExit('The Apple WWDR G3 intermediate certificate has an unexpected checksum.')
    cert = decode_secret('TVOS_CERTIFICATE_BASE64')
    password = read_secret('TVOS_CERTIFICATE_PASSWORD')
    profile_bytes = decode_secret('TVOS_PROFILE_IGUOTV')
    with tempfile.TemporaryDirectory(prefix='iguotv-sign-') as directory:
        temp = Path(directory)
        app = app_from_ipa(ipa, temp / 'unsigned')
        embedded = app / 'embedded.mobileprovision'
        if embedded.exists() or embedded.is_symlink():
            raise SystemExit('Signing input already has an embedded provisioning profile.')
        embedded.write_bytes(profile_bytes)
        profile = read_plist(run(SECURITY, 'cms', '-D', '-i', embedded,
                                 error='Cannot decode the provisioning profile CMS.'),
                             'The provisioning profile has an invalid plist.')
        entitlements, profile_certs = validate_profile(profile, os.environ.get('TVOS_TEAM_ID'))
        entitlements_file = temp / 'entitlements.plist'
        entitlements_file.write_bytes(plistlib.dumps(entitlements))

        keychain = temp / 'signing.keychain-db'
        keychain_password = secrets.token_hex(32)
        p12 = temp / 'identity.p12'
        p12.write_bytes(cert)
        original_keychains = run(SECURITY, 'list-keychains', '-d', 'user',
                                 error='Cannot read the keychain search list.')
        keychains = re.findall(r'"([^"\n]+)"', original_keychains.decode())
        run(SECURITY, 'create-keychain', '-p', keychain_password, keychain,
            error='Cannot create the temporary signing keychain.')
        try:
            run(SECURITY, 'list-keychains', '-d', 'user', '-s', keychain, *keychains,
                error='Cannot add the temporary keychain to the search list.')
            run(SECURITY, 'unlock-keychain', '-p', keychain_password, keychain,
                error='Cannot unlock the temporary signing keychain.')
            run(SECURITY, 'import', intermediate, '-k', keychain,
                error='Cannot import the Apple WWDR G3 intermediate certificate.')
            run(SECURITY, 'import', p12, '-k', keychain, '-f', 'pkcs12', '-x',
                '-P', password, '-T', CODESIGN,
                error='Cannot import the distribution identity. Check the PKCS#12 and password.')
            p12.unlink()
            run(SECURITY, 'set-key-partition-list', '-S', 'apple-tool:,apple:', '-s', '-k',
                keychain_password, keychain, error='Cannot grant codesign access to the temporary private key.')
            identities = run(SECURITY, 'find-identity', '-v', '-p', 'codesigning', keychain,
                             error='Cannot read valid signing identities from the temporary keychain.')
            distribution_ids = re.findall(r'\b([A-F0-9]{40}) "Apple Distribution: [^"\n]+"',
                                          identities.decode())
            matches = profile_certs.intersection(distribution_ids)
            if len(matches) != 1:
                raise SystemExit('Expected one valid Apple Distribution identity authorized by the profile. '
                                 'Check the certificate, private key and WWDR certificate chain.')
            identity = matches.pop()
            signing = (CODESIGN, '--force', '--sign', identity, '--keychain', keychain, '--timestamp=none')
            for path in nested_code(app):
                run(*signing, path, error='Cannot sign embedded tvOS code.')
            run(*signing, '--entitlements', entitlements_file, '--generate-entitlement-der', app,
                error='Cannot sign iGuoTV.app.')

            # Verify the app as shipped by reading it back out of the IPA.
            signed_ipa = temp / 'signed.ipa'
            run(DITTO, '-c', '-k', '--sequesterRsrc', '--keepParent', app.parent, signed_ipa,
                error='Cannot package the signed tvOS IPA.')
            signed_app = app_from_ipa(signed_ipa, temp / 'verified')
            requirement = f'anchor apple generic and certificate leaf = H"{identity}"'
            for path in nested_code(signed_app):
                run(CODESIGN, '--verify', '--strict', '-R', requirement, path,
                    error='Embedded tvOS code failed signature verification.')
            run(CODESIGN, '--verify', '--deep', '--strict', '-R', requirement, signed_app,
                error='The signed IPA failed deep signature verification.')
            signed_entitlements = read_plist(run(CODESIGN, '-d', '--entitlements', '-', signed_app,
                                                 error='Cannot read the embedded app entitlements.'),
                                             'The embedded app entitlements are invalid.')
            if signed_entitlements != entitlements:
                raise SystemExit('The embedded signature does not contain the expected app entitlements.')
            if (signed_app / embedded.name).read_bytes() != profile_bytes:
                raise SystemExit('The IPA does not contain the validated provisioning profile.')
        finally:
            try:
                run(SECURITY, 'list-keychains', '-d', 'user', '-s', *keychains,
                    error='Cannot restore the keychain search list.')
            finally:
                run(SECURITY, 'delete-keychain', keychain, error='Cannot delete the temporary keychain.')

        output.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(signed_ipa, output)
    print('Verified the Apple Distribution signature and packaged the tvOS Ad Hoc IPA.')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('input_directory', type=Path)
    parser.add_argument('wwdr_certificate', type=Path)
    parser.add_argument('output_ipa', type=Path)
    args = parser.parse_args()
    inputs = list(args.input_directory.glob('iguotv-*-tvos-unsigned.ipa'))
    if len(inputs) != 1:
        raise SystemExit('Expected one unsigned iGuoTV IPA from the build job.')
    sign(inputs[0], args.wwdr_certificate, args.output_ipa)
