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

安全约束：只打印校验摘要（UUID、过期时间、设备数、Team、身份 CN），
不打印描述文件内容、证书私钥、密码或 Base64 值。
"""
import argparse
import base64
import os
import plistlib
import secrets
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

BUNDLE_ID = 'me.guoqiao.iguotv'


def fail(msg):
    print('SIGN FAIL:', msg)
    sys.exit(1)


def run(*args, capture=False):
    kwargs = {'check': True}
    if capture:
        kwargs['capture_output'] = True
        kwargs['text'] = True
    result = subprocess.run(list(args), **kwargs)
    if capture:
        return result.stdout
    return ''


def find_distribution_identity(keychain):
    """返回 (sha1, common_name)；只认 Apple/iPhone Distribution 身份。"""
    output = run('security', 'find-identity', '-v', '-p', 'codesigning',
                  '-k', keychain, capture=True)
    found = []
    for line in output.splitlines():
        line = line.strip()
        if not line.endswith('"') or 's' not in line:
            continue
        # 形如: 1) <SHA> "Apple Distribution: Wu Bin (TEAM)"
        marker = line.find(') ')
        if marker < 0:
            continue
        sha = line[marker + 2:].split(' "', 1)[0]
        cn = line.split(' "', 1)[1].rstrip('"')
        if cn.startswith(('Apple Distribution', 'iPhone Distribution')):
            found.append((sha, cn))
    if not found:
        fail('临时钥匙串中找不到可用的 Distribution 身份')
    if len(found) > 1:
        print(f'SIGN NOTE: 发现 {len(found)} 个 Distribution 身份，使用第一个')
    return found[0]


def check_profile(raw):
    """解码并校验 Ad-Hoc 描述文件，返回摘要 dict。"""
    decoded = run('security', 'cms', '-D', '-i', raw, capture=True)
    try:
        profile = plistlib.loads(decoded.encode('utf-8'))
    except Exception as error:
        fail(f'描述文件不是合法 plist：{error}')
    uuid = profile.get('UUID', '?')
    name = profile.get('Name', '?')
    team_ids = profile.get('TeamIdentifier', [])
    expiration = profile.get('ExpirationDate')
    devices = profile.get('ProvisionedDevices') or []
    app_id = (profile.get('Entitlements') or {}).get('application-identifier', '')
    if app_id != BUNDLE_ID:
        fail(f'描述文件 application-identifier={app_id!r}，期望 {BUNDLE_ID}')
    if not team_ids:
        fail('描述文件缺少 TeamIdentifier')
    if expiration is None:
        fail('描述文件缺少 ExpirationDate')
    if time.time() >= expiration.timestamp():
        fail(f'描述文件 {name} 已过期（{expiration}）')
    return {'uuid': uuid, 'name': name, 'team': team_ids,
            'expires': expiration, 'devices': len(devices)}


def main():
    parser = argparse.ArgumentParser(description='tvOS Ad-Hoc 签名')
    parser.add_argument('--input', type=Path,
                        help='未签名 IPA；缺省取 dist/tvos 下唯一 .ipa')
    parser.add_argument('--output', type=Path,
                        help='签名后 IPA；缺省 dist/tvos-sig/<同名去 unsigned>ad-hoc')
    args = parser.parse_args()

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

    cert_b64 = os.environ.get('TVOS_CERTIFICATE_BASE64', '').strip()
    cert_password = os.environ.get('TVOS_CERTIFICATE_PASSWORD', '').strip()
    profile_b64 = os.environ.get('TVOS_PROFILE_IGUOTV', '').strip()
    if not (cert_b64 and cert_password and profile_b64):
        fail('缺少 TVOS_CERTIFICATE_BASE64 / TVOS_CERTIFICATE_PASSWORD / TVOS_PROFILE_IGUOTV')

    temp = Path(tempfile.mkdtemp(prefix='tvos-sign-'))
    try:
        # 1) 解码 Secrets 到临时目录（立即 unlink 原始 Base64 文件）
        cert_path = temp / 'distribution.p12'
        profile_path = temp / 'iguotv.mobileprovision'
        cert_path.write_bytes(base64.b64decode(cert_b64))
        profile_path.write_bytes(base64.b64decode(profile_b64))

        # 2) 校验描述文件
        summary = check_profile(profile_path)
        print(f"SIGN PROFILE: {summary['name']} (UUID {summary['uuid'][:8]}…, "
              f"Team {summary['team']}, {summary['devices']} 台设备, "
              f"过期 {summary['expires']})")
        if summary['devices'] == 0:
            fail('Ad-Hoc 描述文件未包含任何设备')

        # 3) 临时钥匙串导入 Distribution 身份
        keychain_password = secrets.token_hex(16)
        keychain = temp / 'signing.keychain'
        run('security', 'create-keychain', '-p', keychain_password, str(keychain))
        run('security', 'set-keychain-settings', '-u', '10800', '-t', '3600', str(keychain))
        run('security', 'import', str(cert_path), '-P', cert_password,
            '-t', 'identity', '-f', 'identityP12', '-k', str(keychain))
        run('security', 'list-keychains', '-d', 'user', '-s', str(keychain))
        sha, common_name = find_distribution_identity(str(keychain))
        print(f'SIGN IDENTITY: {common_name} ({sha[:16]}…)')

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
        for framework in sorted(app.rglob('*.framework')):
            if framework.is_dir():
                run('codesign', '--force', '--timestamp', '--sign', sha, str(framework))
        if (app / 'embedded.mobileprovision').exists():
            (app / 'embedded.mobileprovision').unlink()
        shutil.copy2(profile_path, app / 'embedded.mobileprovision')
        run('codesign', '--force', '--timestamp', '--sign', sha, str(app))

        # 6) 校验
        run('codesign', '--verify', '--strict', '--verbose=1', str(app))
        print('SIGN VERIFY: codesign --verify --strict 通过')

        # 7) 重新打包
        output_ipa.parent.mkdir(parents=True, exist_ok=True)
        if output_ipa.exists():
            output_ipa.unlink()
        run('ditto', '-c', '-k', '--sequesterRsrc', '--keepParent', str(payload),
            str(output_ipa))
        print(f'SIGN DONE: {output_ipa}（{output_ipa.stat().st_size // 1048576} MB）')
    finally:
        # 清理：临时钥匙串、解码出的证书/描述文件、签名暂存目录
        shutil.rmtree(temp, ignore_errors=True)


if __name__ == '__main__':
    main()
