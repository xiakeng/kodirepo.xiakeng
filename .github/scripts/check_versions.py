#!/usr/bin/env python3
"""
Check addon versions in submodules against published versions in repo/zips/addons.xml.
Queries open GitHub issues with the 'plugin-update' label to detect in-progress updates.
Outputs updates_needed and updates_json to $GITHUB_OUTPUT.
"""

import configparser
import json
import os
import re
import subprocess
import sys
import xml.etree.ElementTree as ET

REPO_ROOT = os.environ.get("GITHUB_WORKSPACE", os.getcwd())
ADDONS_XML = os.path.join(REPO_ROOT, "repo", "zips", "addons.xml")

LABEL = "plugin-update"


def parse_gitmodules():
    """
    Parse .gitmodules to dynamically discover submodules.
    Returns a list of dicts with keys: path, repo (e.g. 'xiakeng/jellyfin-kodi').
    """
    gitmodules_path = os.path.join(REPO_ROOT, ".gitmodules")
    if not os.path.exists(gitmodules_path):
        print(f"Warning: {gitmodules_path} not found", file=sys.stderr)
        return []

    # configparser needs a default section; prepend one
    with open(gitmodules_path, "r", encoding="utf-8") as f:
        content = f.read()

    # Wrap in a fake section so configparser can handle git config format
    wrapped = "[DEFAULT]\n" + content
    parser = configparser.ConfigParser()
    parser.read_string(wrapped)

    submodules = []
    # Real sections are like: [submodule "repo/jellyfin-kodi"]
    for section in parser.sections():
        if section == "DEFAULT":
            continue
        m = re.match(r'submodule\s+"(.+)"', section)
        if not m:
            continue
        name = m.group(1)
        path = parser.get(section, "path", fallback=name)
        url = parser.get(section, "url", fallback="")

        # Derive 'owner/repo' from the URL
        # Handles https://github.com/owner/repo.git and git@github.com:owner/repo.git
        repo_match = re.search(r"github\.com[:/]([^/]+/[^/.]+?)(?:\.git)?$", url)
        repo_slug = repo_match.group(1) if repo_match else name

        submodules.append({"path": path, "repo": repo_slug})

    return submodules


def get_published_versions():
    """Parse repo/zips/addons.xml to get currently published addon versions."""
    versions = {}
    if not os.path.exists(ADDONS_XML):
        return versions
    try:
        tree = ET.parse(ADDONS_XML)
        root = tree.getroot()
        for addon in root.findall("addon"):
            addon_id = addon.get("id")
            version = addon.get("version")
            if addon_id and version:
                versions[addon_id] = version
    except Exception as e:
        print(f"Warning: Could not parse {ADDONS_XML}: {e}", file=sys.stderr)
    return versions


def get_addon_info(submodule_path):
    """Parse a submodule's addon.xml to get (addon_id, version)."""
    addon_xml = os.path.join(REPO_ROOT, submodule_path, "addon.xml")
    if not os.path.exists(addon_xml):
        print(
            f"Warning: {addon_xml} not found, skipping",
            file=sys.stderr,
        )
        return None, None
    try:
        tree = ET.parse(addon_xml)
        root = tree.getroot()
        return root.get("id"), root.get("version")
    except Exception as e:
        print(f"Warning: Could not parse {addon_xml}: {e}", file=sys.stderr)
        return None, None


def get_open_issues():
    """
    Query open GitHub issues with the 'plugin-update' label via gh CLI.
    Returns a list of issue dicts with keys: number, title, body.
    """
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    if not repo:
        print("Warning: GITHUB_REPOSITORY not set", file=sys.stderr)
        return []
    try:
        result = subprocess.run(
            [
                "gh",
                "api",
                f"/repos/{repo}/issues",
                "-q",
                f'.[] | select(.labels[]?.name == "{LABEL}") | select(.state == "open") | '
                '{number: .number, title: .title, body: (.body // "")}',
                "--paginate",
            ],
            capture_output=True,
            text=True,
            check=True,
        )
        issues = []
        # gh --paginate outputs one JSON object per line
        for line in result.stdout.strip().splitlines():
            line = line.strip()
            if line:
                try:
                    issues.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
        return issues
    except (subprocess.CalledProcessError, FileNotFoundError) as e:
        print(f"Warning: Could not query GitHub issues: {e}", file=sys.stderr)
        return []


def set_output(key, value):
    """Write a key=value pair to $GITHUB_OUTPUT."""
    github_output = os.environ.get("GITHUB_OUTPUT")
    if github_output:
        with open(github_output, "a", encoding="utf-8") as f:
            f.write(f"{key}={value}\n")
    else:
        print(f"[OUTPUT] {key}={value}")


def main():
    submodules = parse_gitmodules()
    print(f"Discovered submodules: {[sm['path'] for sm in submodules]}", file=sys.stderr)

    published = get_published_versions()
    print(f"Published versions: {published}", file=sys.stderr)

    open_issues = get_open_issues()
    print(f"Open plugin-update issues: {len(open_issues)}", file=sys.stderr)

    updates = []
    for sm in submodules:
        addon_id, new_version = get_addon_info(sm["path"])
        if addon_id is None:
            continue

        old_version = published.get(addon_id)
        print(
            f"  {addon_id}: published={old_version}, submodule={new_version}",
            file=sys.stderr,
        )

        if old_version == new_version:
            continue

        # Check for an existing open issue for this addon_id
        existing_issue = None
        for issue in open_issues:
            if addon_id in issue.get("title", ""):
                existing_issue = issue["number"]
                break

        updates.append(
            {
                "addon_id": addon_id,
                "submodule": sm["path"],
                "repo": sm["repo"],
                "old_version": old_version or "N/A",
                "new_version": new_version,
                "existing_issue": existing_issue,
            }
        )

    if updates:
        set_output("updates_needed", "true")
        set_output("updates_json", json.dumps(updates))
        print(f"Updates needed: {len(updates)}", file=sys.stderr)
        for u in updates:
            print(
                f"  {u['addon_id']}: {u['old_version']} -> {u['new_version']}"
                + (f" (existing issue #{u['existing_issue']})" if u["existing_issue"] else ""),
                file=sys.stderr,
            )
    else:
        set_output("updates_needed", "false")
        set_output("updates_json", "[]")
        print("No updates needed.", file=sys.stderr)


if __name__ == "__main__":
    main()
