#!/usr/bin/env python3
"""tvOS Ad Hoc 签名：在 macOS runner 上把未签名 IPA 签成可安装的 Ad-Hoc 包。

前置：先运行 build_tvos.py 生成未签名 IPA（dist/tvos/*.ipa）。

环境变量（仓库级 Secrets）：
  TVOS_CERTIFICATE_BASE64    Distribution 证书 .p12 的 Base64
  TVOS_CERTIFICATE_PASSWORD  .p12 导出密码
  TVOS_PROFILE_IGUOTV       Ad-Hoc 描述文件 .mobileprovision 的 Base64

流程：解码 Secrets → 校验描述文件 → 临时钥匙串导入 Distribution 身份 →
重签未签名 IPA（动态框架 + 主程序，内嵌描述文件）→ 校验签名 → 输出
dist/tvos-sig/*-adhoc.ipa → 清理临时钥匙串与解码文件。

安全约束：不打印描述文件内容、证书私钥、密码或 Base64 值。
"""
import argparse
import base64
import hashlib
import os
import plistlib
import re
import secrets
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

BUNDLE_ID = 'me.guoqiao.iguotv'
TEAM_ID = 'YWUEH5CWCS'
WWDR_G3_SHA256 = 'dcf21878c77f4198e4b4614f03d696d89c66c66008d4244e1b99161aac91601f'


def fail(msg):
    print('SIGN FAIL:', msg)
    sys.exit(1)


def run(*args, capture=False):
    result = subprocess.run(args, capture_output=True, stdin=subprocess.DEVNULL)
    if result.returncode:
        fail(f'{Path(args[0]).name} {args[1]} 失败（退出状态 {result.returncode}）')
    return result.stdout if capture else b''


def find_distribution_identity(keychain, profile_hashes):
    output = run('security', 'find-identity', '-v', '-p', 'codesigning',
                 keychain, capture=True).decode()
    identities = re.findall(r'\b([A-F0-9]{40}) "Apple Distribution: [^"\n]+"', output)
    matches = profile_hashes.intersection(identities)
    if len(matches) != 1:
        fail('临时钥匙串中需要一个由描述文件授权的有效 Apple Distribution 身份（请检查 WWDR 证书链）')
    return matches.pop()


def validate_profile(profile):
    """Return the app entitlements and authorized signing certificate hashes."""
    if not isinstance(profile, dict):
        fail('描述文件不是合法的 plist 字典')
    entitlements = profile.get('Entitlements')
    if (profile.get('TeamIdentifier') != [TEAM_ID] or not isinstance(entitlements, dict)
            or entitlements.get('com.apple.developer.team-identifier') != TEAM_ID):
        fail('描述文件 Team ID 与 iGuoTV 不匹配')

    # Legacy App ID prefixes can differ from the team ID.
    prefixes = profile.get('ApplicationIdentifierPrefix')
    app_id = entitlements.get('application-identifier')
    if not isinstance(prefixes, list) or not any(
        isinstance(prefix, str) and re.fullmatch(r'[A-Z0-9]{10}', prefix)
        and app_id == f'{prefix}.{BUNDLE_ID}' for prefix in prefixes
    ):
        fail('描述文件 application-identifier 与完整的 iGuoTV App ID 不匹配')

    expiration = profile.get('ExpirationDate')
    if not isinstance(expiration, datetime) or expiration.replace(tzinfo=timezone.utc) <= datetime.now(timezone.utc):
        fail('描述文件已过期或缺少到期时间')
    devices = profile.get('ProvisionedDevices')
    if (not isinstance(devices, list) or not devices or profile.get('ProvisionsAllDevices')
            or entitlements.get('get-task-allow') is not False):
        fail('描述文件必须为包含设备的 Ad Hoc 分发描述文件')
    certificates = profile.get('DeveloperCertificates')
    if not isinstance(certificates, list) or not certificates or not all(isinstance(cert, bytes) for cert in certificates):
        fail('描述文件缺少签名证书')
    signing_entitlements = {
        'application-identifier': app_id,
        'com.apple.developer.team-identifier': TEAM_ID,
        'get-task-allow': False,
    }
    return signing_entitlements, {hashlib.sha1(cert).hexdigest().upper() for cert in certificates}


def check_profile(raw):
    decoded = run('security', 'cms', '-D', '-i', raw, capture=True)
    try:
        profile = plistlib.loads(decoded)
    except (ValueError, plistlib.InvalidFileException):
        fail('描述文件不是合法的 plist')
    return validate_profile(profile)


def main():
    parser = argparse.ArgumentParser(description='tvOS Ad-Hoc 签名')
    parser.add_argument('--input', type=Path,
                        help='未签名 IPA；缺省取 dist/tvos 下唯一 .ipa')
    parser.add_argument('--output', type=Path,
                        help='签名后 IPA；缺省 dist/tvos-sig/<同名去 unsigned>ad-hoc')
    parser.add_argument('--wwdr-certificate', type=Path, help='Apple WWDR G3 intermediate certificate')
    args = parser.parse_args()
    if args.wwdr_certificate and hashlib.sha256(args.wwdr_certificate.read_bytes()).hexdigest() != WWDR_G3_SHA256:
        fail('Apple WWDR G3 中间证书校验失败')

    root = Path(__file__).resolve().parent.parent
    if args.input:
        if not args.input.is_file():
            fail(f'未签名 IPA 不存在：{args.input}')
        input_ipa = args.input.resolve()
    else:
        candidates = sorted((root / 'dist' / 'tvos').glob('*.ipa'))
        if len(candidates) != 1:
            fail(f'dist/tvos 下应有且仅有一个 .ipa，实际 {len(candidates)} 个')
        input_ipa = candidates[0]
    if args.output:
        output_ipa = args.output.resolve()
    else:
        output_ipa = input_ipa.parent.parent / 'tvos-sig' / \
            input_ipa.name.replace('-unsigned', '-adhoc')

    cert_b64 = os.environ.pop('TVOS_CERTIFICATE_BASE64', '').strip()
    cert_password = os.environ.pop('TVOS_CERTIFICATE_PASSWORD', '')
    profile_b64 = os.environ.pop('TVOS_PROFILE_IGUOTV', '').strip()
    if not (cert_b64 and cert_password and profile_b64):
        fail('缺少 TVOS_CERTIFICATE_BASE64 / TVOS_CERTIFICATE_PASSWORD / TVOS_PROFILE_IGUOTV')

    temp = Path(tempfile.mkdtemp(prefix='tvos-sign-'))
    keychain = temp / 'signing.keychain-db'
    keychain_created = False
    old_keychains = None
    try:
        # 1) 解码 Secrets 到临时目录（立即 unlink 原始 Base64 文件）
        cert_path = temp / 'distribution.p12'
        profile_path = temp / 'iguotv.mobileprovision'
        cert_path.write_bytes(base64.b64decode(cert_b64))
        profile_path.write_bytes(base64.b64decode(profile_b64))

        # 2) 校验描述文件
        entitlements, profile_hashes = check_profile(profile_path)
        entitlements_file = temp / 'entitlements.plist'
        entitlements_file.write_bytes(plistlib.dumps(entitlements))
        print('SIGN PROFILE: Ad Hoc profile verified')

        # 3) 临时钥匙串导入 Distribution 身份
        keychain_password = secrets.token_hex(16)
        keychain_list = run('security', 'list-keychains', '-d', 'user', capture=True).decode()
        old_keychains = re.findall(r'"([^"\n]+)"', keychain_list)
        run('security', 'create-keychain', '-p', keychain_password, keychain)
        keychain_created = True
        run('security', 'list-keychains', '-d', 'user', '-s', keychain, *old_keychains)
        run('security', 'unlock-keychain', '-p', keychain_password, keychain)
        if args.wwdr_certificate:
            run('security', 'import', args.wwdr_certificate, '-k', keychain)
        run('security', 'import', cert_path, '-P', cert_password, '-f', 'pkcs12',
            '-x', '-k', keychain, '-T', '/usr/bin/codesign')
        cert_path.unlink()
        run('security', 'set-key-partition-list', '-S', 'apple-tool:,apple:', '-s',
            '-k', keychain_password, keychain)
        sha = find_distribution_identity(keychain, profile_hashes)

        # 4) 解开未签名 IPA
        stage = temp / 'stage'
        stage.mkdir()
        run('ditto', '-x', '-k', str(input_ipa), str(stage))
        payload = stage / 'Payload'
        apps = list(payload.glob('*.app'))
        if len(apps) != 1:
            fail(f'IPA 内应有且仅有一个 .app，实际 {len(apps)} 个')
        app = apps[0]

        # 5) 先签内嵌动态框架，再签主程序，内嵌描述文件
        signing = ('codesign', '--force', '--timestamp=none', '--sign', sha, '--keychain', keychain)
        nested = (path for path in app.rglob('*')
                  if not path.is_symlink() and path.suffix in ('.framework', '.dylib'))
        for path in sorted(nested, key=lambda path: (-len(path.parts), path)):
            run(*signing, path)
        shutil.copy2(profile_path, app / 'embedded.mobileprovision')
        run(*signing, '--entitlements', entitlements_file, '--generate-entitlement-der', app)

        # 6) 校验
        requirement = f'anchor apple generic and certificate leaf = H"{sha}"'
        run('codesign', '--verify', '--deep', '--strict', '-R', requirement, app)
        embedded_entitlements = plistlib.loads(run('codesign', '-d', '--entitlements', '-', app, capture=True))
        if embedded_entitlements != entitlements:
            fail('签名授权与描述文件不匹配')
        print('SIGN VERIFY: app signature and entitlements verified')

        # 7) 重新打包
        output_ipa.parent.mkdir(parents=True, exist_ok=True)
        if output_ipa.exists():
            output_ipa.unlink()
        run('ditto', '-c', '-k', '--sequesterRsrc', '--keepParent', str(payload),
            str(output_ipa))
        print(f'SIGN DONE: {output_ipa}（{output_ipa.stat().st_size // 1048576} MB）')
    finally:
        try:
            if keychain_created:
                try:
                    run('security', 'list-keychains', '-d', 'user', '-s', *old_keychains)
                finally:
                    run('security', 'delete-keychain', keychain)
        finally:
            shutil.rmtree(temp, ignore_errors=True)


if __name__ == '__main__':
    main()
