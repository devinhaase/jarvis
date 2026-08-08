"""
security_modules/dependency_audit.py — Defense: check your own project's pinned Python
dependencies against known vulnerabilities via OSV.dev (osv.dev — Google-run, free, no API
key, aggregates GitHub Security Advisories/PyPA advisories/etc.). No new pip dependency —
`requests` is already used throughout this codebase.

This is a defense tool, not offense: it reads a local requirements file and asks a public
vulnerability database about package names/versions — it never touches a remote target, so
it isn't gated by AuthorizationCheck (nothing here could be pointed at someone else's
infrastructure). It DOES touch the local filesystem, so it's tagged in
tools.REQUIRES_CAPABILITY like the other local-machine tools.
"""

import os
import re


def _parse_requirements(path: str) -> list:
    """Extract (name, version) pairs from exact pins ('pkg==1.2.3') only — ranges,
    unpinned, -e/-r includes, and comments can't be checked meaningfully against a single
    version, so they're reported separately rather than silently skipped."""
    pinned = []
    skipped = []
    if not os.path.exists(path):
        return pinned, skipped, f"File not found: {path}"

    with open(path, 'r') as f:
        for raw_line in f:
            line = raw_line.split("#", 1)[0].strip()
            if not line:
                continue
            if line.startswith(("-e ", "-r ", "--")):
                skipped.append(line)
                continue
            m = re.match(r'^([A-Za-z0-9_.\-]+)\s*==\s*([A-Za-z0-9_.\-+]+)', line)
            if m:
                pinned.append((m.group(1), m.group(2)))
            else:
                skipped.append(line)

    return pinned, skipped, None


def check_dependency_vulnerabilities(requirements_path: str = "requirements.txt") -> dict:
    """
    Check every exactly-pinned package in a requirements file against OSV.dev's PyPI
    vulnerability data. Defaults to this project's own requirements.txt — genuinely useful
    to run against Jarvis's own dependencies, not just other projects.
    """
    pinned, skipped, err = _parse_requirements(requirements_path)
    if err:
        return {"requirements_path": requirements_path, "error": err}
    if not pinned:
        return {
            "requirements_path": requirements_path, "checked": 0,
            "note": "No exactly-pinned (name==version) entries found to check. "
                    "Pin versions (e.g. 'requests==2.32.3') to make this check meaningful.",
            "skipped_unpinned_or_unparsed": skipped,
        }

    try:
        import requests
        queries = [{"package": {"name": name, "ecosystem": "PyPI"}, "version": version} for name, version in pinned]
        resp = requests.post(
            "https://api.osv.dev/v1/querybatch", json={"queries": queries}, timeout=30
        )
        resp.raise_for_status()
        batch_results = resp.json().get("results", [])
    except Exception as e:
        return {"requirements_path": requirements_path, "error": f"OSV.dev query failed: {e}"}

    vulnerable = []
    clean = []
    for (name, version), result in zip(pinned, batch_results):
        vulns = result.get("vulns", [])
        if not vulns:
            clean.append(f"{name}=={version}")
            continue
        detail = []
        for v in vulns[:3]:  # bound follow-up calls per package
            vuln_id = v.get("id")
            summary = v.get("summary", "")
            if not summary:
                try:
                    r2 = requests.get(f"https://api.osv.dev/v1/vulns/{vuln_id}", timeout=10)
                    if r2.ok:
                        summary = r2.json().get("summary", "")
                except Exception:
                    pass
            detail.append({"id": vuln_id, "summary": summary or "(no summary available)"})
        vulnerable.append({"package": name, "version": version, "vulnerabilities": detail,
                            "total_matches": len(vulns)})

    return {
        "requirements_path": requirements_path,
        "checked": len(pinned),
        "vulnerable_count": len(vulnerable),
        "vulnerable_packages": vulnerable,
        "clean_packages": clean,
        "skipped_unpinned_or_unparsed": skipped,
        "source": "OSV.dev (osv.dev) — aggregates GitHub Security Advisories, PyPA, and others",
    }
