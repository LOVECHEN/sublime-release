# Sublime Text / Sublime Merge Releases

Mirrors unmodified installers from the official product download pages to GitHub Releases. Source URLs and mirror rules are provided through the `SUBLIME_MIRROR_CONFIG` GitHub Actions secret.

The scheduled workflow checks configured sources every six hours. It mirrors new builds first, then backfills older builds according to the private configuration. Downloads are limited to package URLs present in the official page's download template; unavailable packages are omitted. Every release includes a `SHA256SUMS.txt` manifest.

Run manually from Actions with `source_key` and `build` to narrow a run. The available keys and all discovery, filtering, and release rules are maintained privately.

This repository contains no installer binaries in Git history; packages are Release assets only.
