"""Mirror builds using source and discovery rules supplied at runtime."""

import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlparse


def fetch_page(url):
    request = urllib.request.Request(url, headers={"User-Agent": "sublime-release-mirror/1.0"})
    with urllib.request.urlopen(request, timeout=30) as response:
        return response.read().decode("utf-8")


def load_config(raw):
    if not raw:
        raise ValueError("Mirror config secret is not configured")
    data = json.loads(raw)
    rules = data["rules"]
    for name in ("build_pattern", "template_pattern", "package_pattern"):
        re.compile(rules[name])
    if not 1 <= int(rules["max_per_source"]) <= 20:
        raise ValueError("Invalid per-source limit")
    sources = data["sources"]
    if not sources or len({source["key"] for source in sources}) != len(sources):
        raise ValueError("Missing or duplicate source keys")
    for source in sources:
        url = urlparse(source["page"])
        host = source["download_host"]
        if url.scheme != "https" or not url.hostname:
            raise ValueError("Source pages must use HTTPS")
        if not re.fullmatch(r"[a-z0-9.-]+", host):
            raise ValueError("Invalid download host")
        if not re.fullmatch(r"[a-z0-9-]+", source["key"]):
            raise ValueError("Invalid source key")
    if rules["latest_source"] not in {source["key"] for source in sources}:
        raise ValueError("Latest source is not configured")
    return rules, sources


def parse_page(html, rules, download_host):
    builds = list(dict.fromkeys(re.findall(rules["build_pattern"], html)))
    if any(not isinstance(build, str) or not build.isdigit() for build in builds):
        raise ValueError("Build pattern must capture numeric versions")
    templates = list(dict.fromkeys(re.findall(rules["template_pattern"], html)))
    templates = [url for url in templates
                 if urlparse(url).hostname == download_host
                 and rules["version_token"] in url
                 and re.search(rules["package_pattern"], url)]
    if not builds or not templates:
        raise ValueError("Official page has no changelog builds or package templates")
    return sorted(builds, key=int, reverse=True), templates


def run_gh(*args, capture=False):
    command = ["gh", *args]
    result = subprocess.run(command, check=True, text=True,
                            stdout=subprocess.PIPE if capture else None)
    return result.stdout if capture else None


def existing_tags(repo):
    output = run_gh("release", "list", "-R", repo, "--limit", "1000",
                    "--json", "tagName", capture=True)
    return {item["tagName"] for item in json.loads(output)}


def probe(url):
    request = urllib.request.Request(url, method="HEAD", headers={"User-Agent": "sublime-release-mirror/1.0"})
    for attempt in range(3):
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                return response.status == 200
        except urllib.error.HTTPError as error:
            if error.code in (403, 404, 410):
                return False
            if error.code not in (429, 500, 502, 503, 504):
                raise
        except (TimeoutError, urllib.error.URLError):
            pass
        time.sleep(2 ** attempt)
    raise RuntimeError(f"Cannot probe official package: {url}")


def download(url, path):
    request = urllib.request.Request(url, headers={"User-Agent": "sublime-release-mirror/1.0"})
    for attempt in range(3):
        try:
            digest = hashlib.sha256()
            with urllib.request.urlopen(request, timeout=120) as response, path.open("wb") as output:
                while chunk := response.read(1024 * 1024):
                    output.write(chunk)
                    digest.update(chunk)
            if path.stat().st_size == 0:
                raise RuntimeError(f"Empty package: {url}")
            return digest.hexdigest()
        except (OSError, urllib.error.URLError, TimeoutError):
            path.unlink(missing_ok=True)
            if attempt == 2:
                raise
            time.sleep(2 ** attempt)


def make_tag(source, rules, build):
    tag = rules["tag_format"].format(key=source["key"], build=build)
    if not re.fullmatch(r"[a-zA-Z0-9._-]+", tag):
        raise ValueError("Invalid release tag")
    return tag


def mirror_build(repo, source, rules, build, latest_build, templates):
    tag = make_tag(source, rules, build)
    title = rules["title_format"].format(
        product=source["product"], channel=source["channel"], build=build
    )
    urls = [url.replace(rules["version_token"], build) for url in templates]
    available = [url for url in urls if probe(url)]
    if not available:
        print(f"{tag}: no official packages available", flush=True)
        return False

    with tempfile.TemporaryDirectory(prefix="sublime-mirror-") as temp:
        directory = Path(temp)
        checksums = []
        assets = []
        for url in available:
            filename = Path(urlparse(url).path).name
            path = directory / filename
            print(f"{tag}: downloading {filename}", flush=True)
            digest = download(url, path)
            checksums.append(f"{digest}  {filename}")
            assets.append(path)
        manifest = directory / "SHA256SUMS.txt"
        manifest.write_text("\n".join(checksums) + "\n", encoding="utf-8")
        notes = directory / "notes.md"
        notes.write_text(
            f"## {title}\n\n"
            "Source: official product download page.\n\n"
            "Unmodified official packages. Packages not available from the official "
            "download server for this build are omitted.\n\n"
            "SHA-256 checksums are in `SHA256SUMS.txt`.\n",
            encoding="utf-8",
        )
        latest = "--latest" if source["key"] == rules["latest_source"] and build == latest_build else "--latest=false"
        run_gh("release", "create", tag, "-R", repo, "--title",
               title,
               "--notes-file", str(notes), latest,
               *(str(asset) for asset in assets), str(manifest))
        print(f"{tag}: published {len(assets)} official packages", flush=True)
    return True


def main():
    repo = os.environ.get("GITHUB_REPOSITORY", "LOVECHEN/sublime-release")
    rules, sources = load_config(os.environ.get("SUBLIME_MIRROR_CONFIG", ""))
    selected_key = os.environ.get("SOURCE_KEY", "")
    selected_build = os.environ.get("BUILD", "")
    if selected_build and not selected_key:
        raise ValueError("A source key is required when selecting a build")
    if selected_key:
        sources = [source for source in sources if source["key"] == selected_key]
        if not sources:
            raise ValueError("Unknown source key")
    if os.environ.get("GITHUB_ACTIONS") == "true":
        for value in {item for source in sources for item in (source["page"], source["download_host"])}:
            print(f"::add-mask::{value}", flush=True)
    tags = existing_tags(repo)
    for source in sources:
        builds, templates = parse_page(fetch_page(source["page"]), rules, source["download_host"])
        latest_build = builds[0]
        if selected_build:
            if selected_build not in builds:
                raise ValueError("Selected build is not in the source changelog")
            builds = [selected_build]
        count = 0
        for build in builds:
            tag = make_tag(source, rules, build)
            if tag in tags:
                continue
            if mirror_build(repo, source, rules, build, latest_build, templates):
                count += 1
                tags.add(tag)
            if count >= int(rules["max_per_source"]):
                break
        print(f"{source['key']}: created {count} releases", flush=True)


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print(f"Mirror failed: {type(error).__name__}", file=sys.stderr)
        sys.exit(1)
