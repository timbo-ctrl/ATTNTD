#!/usr/bin/env python3
"""Rebuild attntd.com as a static mirror from the live Webflow staging site.
Usage: python3 mirror_attntd.py [output_dir]   (default: ./attntd-mirror)
Requires: python3 + curl (both ship with macOS). No pip installs.
"""
import html as htmllib
import json
import os
import re
import shutil
import subprocess
import sys
from urllib.parse import quote, unquote, urlparse

BASE = "https://attn.webflow.io"
OUT = sys.argv[1] if len(sys.argv) > 1 else "./attntd-mirror"

PAGES = {
    "/": "index.html",
    "/changelog": "changelog.html",
    "/licensing": "licensing.html",
    "/style-guide": "style-guide.html",
    "/404": "404.html",
    "/portfolio/boutiq": "portfolio/boutiq.html",
    "/portfolio/lthr": "portfolio/lthr.html",
    "/portfolio/greenerways": "portfolio/greenerways.html",
    "/portfolio/primetime": "portfolio/primetime.html",
    "/portfolio/babushka": "portfolio/babushka.html",
}
INTERNAL = {k: v for k, v in PAGES.items() if k != "/404"}
ASSET_HOSTS = ("website-files.com", "webflow.com", "cloudfront.net")


def curl(url, dest=None):
    cmd = ["curl", "-sk", "-A", "Mozilla/5.0", "--retry", "3", "-w", "%{http_code}"]
    cmd += ["-o", dest] if dest else ["-o", "-"]
    if dest:
        r = subprocess.run(cmd + [url], capture_output=True, text=True)
        return r.stdout.strip()
    r = subprocess.run(cmd + [url], capture_output=True)
    body, code = r.stdout[:-3], r.stdout[-3:].decode()
    return code, body.decode("utf-8", "ignore")


def is_asset(u):
    return u.startswith("https://") and any(h in u for h in ASSET_HOSTS)


def sanitize(n):
    return re.sub(r"-+", "-", re.sub(r"[^A-Za-z0-9._-]", "-", n))


def encoded(u):
    p = urlparse(u)
    return p.scheme + "://" + p.netloc + quote(unquote(p.path), safe="/")


def main():
    os.makedirs(f"{OUT}/assets", exist_ok=True)
    os.makedirs(f"{OUT}/portfolio", exist_ok=True)

    print("Fetching pages...")
    raw = {}
    for path in PAGES:
        code, body = curl(BASE + path)
        if code != "200" and path != "/404":
            sys.exit(f"FATAL: {path} returned HTTP {code}. Is {BASE} still up?")
        raw[path] = body
        print(f"  {path}  {len(body)} bytes")

    # Discover assets: attribute values (handles srcset lists, parens,
    # comma-joined data-video-urls, HTML entities), then CSS url("...") refs.
    attr_re = re.compile(
        r'(?:src|href|srcset|data-video-urls|data-poster-url|poster|style)\s*=\s*"([^"]*)"'
    )
    assets = set()
    for body in raw.values():
        for val in attr_re.findall(body):
            for piece in re.split(r"[,\s]+", htmllib.unescape(val)):
                if is_asset(piece.strip()):
                    assets.add(piece.strip())

    print(f"Downloading {len(assets)} assets (CSS may add more)...")
    mapping, queue, seen, failed = {}, sorted(assets), set(assets), []
    while queue:
        url = queue.pop()
        name = sanitize(unquote(os.path.basename(urlparse(url).path)))
        dest = f"{OUT}/assets/{name}"
        code = curl(encoded(url), dest)
        if code == "200":
            mapping[url] = name
            if name.endswith(".css"):
                css = open(dest, encoding="utf-8", errors="ignore").read()
                found = set(re.findall(r'url\("([^"]+)"\)', css))
                found |= set(re.findall(r"url\('([^']+)'\)", css))
                for u in found:
                    if is_asset(u) and u not in seen:
                        seen.add(u)
                        queue.append(u)
        else:
            failed.append((url, code))
            if os.path.exists(dest):
                os.remove(dest)
    print(f"  ok: {len(mapping)}  failed: {len(failed)}")
    for u, c in failed:
        print(f"  FAILED {c}: {u}")

    print("Rewriting pages...")
    for path, fname in PAGES.items():
        body = raw[path]
        prefix = "../" * fname.count("/")
        for url in sorted(mapping, key=len, reverse=True):
            local = prefix + "assets/" + mapping[url]
            for variant in (url, htmllib.escape(url), url.replace("&", "&amp;")):
                body = body.replace(variant, local)
        for route in sorted(INTERNAL, key=len, reverse=True):
            body = body.replace(f'href="{route}"', f'href="{prefix}{INTERNAL[route]}"')
        for dom in ("https://www.attntd.com", "https://attntd.com", BASE):
            for route in sorted(INTERNAL, key=len, reverse=True):
                body = body.replace(
                    f'href="{dom}{route}"', f'href="{prefix}{INTERNAL[route]}"'
                )
            body = body.replace(f'href="{dom}"', f'href="{prefix}index.html"')
        open(f"{OUT}/{fname}", "w").write(body)

    print("Rewriting CSS...")
    for f in os.listdir(f"{OUT}/assets"):
        if f.endswith(".css"):
            p = f"{OUT}/assets/{f}"
            css = open(p, encoding="utf-8", errors="ignore").read()
            for url in sorted(mapping, key=len, reverse=True):
                css = css.replace(url, mapping[url])
            open(p, "w").write(css)

    # Verify nothing still points at Webflow.
    leftovers = 0
    for path, fname in PAGES.items():
        body = open(f"{OUT}/{fname}").read()
        leftovers += len(
            re.findall(
                r'https://[a-z0-9.-]*(?:website-files\.com|uploads-ssl\.webflow\.com)/',
                body,
            )
        )
    total = sum(
        os.path.getsize(f"{OUT}/assets/{f}") for f in os.listdir(f"{OUT}/assets")
    )
    print(f"\nDone. {len(PAGES)} pages, {len(mapping)} assets, "
          f"{total/1024/1024:.0f} MB. Remaining external Webflow refs: {leftovers}")
    print(f"Open {OUT}/index.html to preview, then push the folder to GitHub.")


if __name__ == "__main__":
    main()
