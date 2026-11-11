import importlib.util
import json
import unittest
from pathlib import Path


SPEC = importlib.util.spec_from_file_location(
    "mirror", Path(__file__).resolve().parents[1] / "scripts" / "mirror.py"
)
mirror = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(mirror)

RULES = {
    "build_pattern": r'data-version="(\d+)"',
    "template_pattern": r'https://[^"\s<>]*__BUILD__[^"\s<>]*',
    "package_pattern": r"\.pkg$",
    "version_token": "__BUILD__",
    "tag_format": "{key}-{build}",
    "title_format": "{product} {channel} Build {build}",
    "max_per_source": 2,
    "latest_source": "example-stable",
}
SOURCE = {
    "key": "example-stable",
    "product": "Example",
    "channel": "Stable",
    "page": "https://www.example.test/download",
    "download_host": "download.example.test",
}


class ParsePageTests(unittest.TestCase):
    def test_extracts_builds_and_official_packages(self):
        html = '''
        <a href="https://download.example.test/example__BUILD__.pkg">package</a>
        <a href="https://download.example.test/example__BUILD__.pkg.sig">sig</a>
        <a href="https://wrong.example.test/example__BUILD__.pkg">wrong host</a>
        <div data-version="4213"></div><div data-version="4215"></div><div data-version="4213"></div>
        '''
        builds, templates = mirror.parse_page(html, RULES, "download.example.test")
        self.assertEqual(builds, ["4215", "4213"])
        self.assertEqual(templates, [
            "https://download.example.test/example__BUILD__.pkg"
        ])

    def test_rejects_missing_templates(self):
        with self.assertRaises(ValueError):
            mirror.parse_page('<div data-version="4215"></div>', RULES, "download.example.test")

    def test_requires_secret_configuration(self):
        with self.assertRaises(ValueError):
            mirror.load_config("")

    def test_loads_rules_and_source(self):
        rules, sources = mirror.load_config(json.dumps({"rules": RULES, "sources": [SOURCE]}))
        self.assertEqual(rules["latest_source"], "example-stable")
        self.assertEqual(sources[0]["key"], "example-stable")
        self.assertEqual(mirror.make_tag(sources[0], rules, "4215"), "example-stable-4215")


if __name__ == "__main__":
    unittest.main()
