"""
Mock-baseret test af recipe_scraper.extract_raw_json_ld.

Bruger unittest.mock til at erstatte requests.get med lokale HTML-fixtures
der efterligner de fem målsiders reelle JSON-LD strukturer.
"""
import json
import sys
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, ".")
from recipe_scraper import RecipeNotFoundError, HTTPError, extract_raw_json_ld


def _mock_response(html: str, status: int = 200) -> MagicMock:
    mock = MagicMock()
    mock.status_code = status
    mock.text = html
    return mock


def _page(ld_json_blocks: list[str]) -> str:
    scripts = "\n".join(
        f'<script type="application/ld+json">{b}</script>'
        for b in ld_json_blocks
    )
    return f"<html><head>{scripts}</head><body></body></html>"


# ---------------------------------------------------------------------------
# Fixtures — efterligner real JSON-LD fra de fem kilder
# ---------------------------------------------------------------------------

# Valdemarsro / Yoast SEO: Recipe er begravet i et @graph-array
VALDEMARSRO_BLOCK = json.dumps({
    "@context": "https://schema.org",
    "@graph": [
        {"@type": "WebSite", "@id": "https://www.valdemarsro.dk/#website"},
        {"@type": "WebPage", "@id": "https://www.valdemarsro.dk/lasagne/#webpage"},
        {
            "@type": "Recipe",
            "@id": "https://www.valdemarsro.dk/lasagne/#recipe",
            "name": "Klassisk lasagne",
            "recipeIngredient": ["400 g hakket oksekød", "1 løg"],
            "recipeInstructions": [{"@type": "HowToStep", "text": "Steg kødet."}],
            "recipeYield": "4",
        },
    ],
})

# Gastromand: Direkte dict uden wrapper
GASTROMAND_BLOCK = json.dumps({
    "@context": "https://schema.org",
    "@type": "Recipe",
    "name": "Boeuf Bourguignon",
    "recipeIngredient": ["1 kg oksekød", "1 flaske rødvin"],
    "recipeYield": "6",
})

# GastroFun / WP Recipe Maker: Enkelt objekt i et JSON-array
GASTROFUN_BLOCK = json.dumps([
    {
        "@context": "https://schema.org",
        "@type": "Recipe",
        "name": "Nem pasta carbonara",
        "recipeIngredient": ["200 g spaghetti", "100 g bacon"],
        "recipeYield": "2",
    }
])

# BBC Good Food: @type er en liste
BBC_BLOCK = json.dumps({
    "@context": "https://schema.org",
    "@type": ["Recipe", "WebPageElement"],
    "name": "Classic lasagne",
    "recipeIngredient": ["400g/14oz minced beef"],
    "recipeYield": "6",
})

# Serious Eats: To separate ld+json scripts — første er WebPage, andet er Recipe
SERIOUS_EATS_WEBPAGE_BLOCK = json.dumps({
    "@context": "https://schema.org",
    "@type": "WebPage",
    "name": "Best Bolognese",
})
SERIOUS_EATS_RECIPE_BLOCK = json.dumps({
    "@context": "https://schema.org",
    "@type": "Recipe",
    "name": "The Best Slow-Cooked Bolognese Sauce",
    "recipeIngredient": ["1 pound ground beef"],
    "recipeYield": "8",
})


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestExtractRawJsonLd(unittest.TestCase):

    def _run(self, html: str, status: int = 200) -> dict:
        with patch("recipe_scraper.requests.get") as mock_get:
            mock_get.return_value = _mock_response(html, status)
            return extract_raw_json_ld("https://example.com/opskrift/")

    # --- De fem kildetyper ---

    def test_valdemarsro_graph_wrapper(self):
        recipe = self._run(_page([VALDEMARSRO_BLOCK]))
        self.assertEqual(recipe["name"], "Klassisk lasagne")
        self.assertEqual(recipe["@type"], "Recipe")

    def test_gastromand_direct_dict(self):
        recipe = self._run(_page([GASTROMAND_BLOCK]))
        self.assertEqual(recipe["name"], "Boeuf Bourguignon")

    def test_gastrofun_json_array(self):
        recipe = self._run(_page([GASTROFUN_BLOCK]))
        self.assertEqual(recipe["name"], "Nem pasta carbonara")

    def test_bbc_type_is_list(self):
        recipe = self._run(_page([BBC_BLOCK]))
        self.assertEqual(recipe["name"], "Classic lasagne")
        self.assertIn("Recipe", recipe["@type"])

    def test_serious_eats_multiple_scripts(self):
        """Recipe sidder i det ANDET ld+json script på siden."""
        recipe = self._run(_page([SERIOUS_EATS_WEBPAGE_BLOCK, SERIOUS_EATS_RECIPE_BLOCK]))
        self.assertEqual(recipe["name"], "The Best Slow-Cooked Bolognese Sauce")

    # --- Fejlhåndtering ---

    def test_http_403_raises_http_error(self):
        with self.assertRaises(HTTPError) as ctx:
            self._run("<html></html>", status=403)
        self.assertIn("403", str(ctx.exception))

    def test_no_recipe_raises_not_found(self):
        html = _page([json.dumps({"@type": "WebPage", "name": "Ingen opskrift her"})])
        with self.assertRaises(RecipeNotFoundError):
            self._run(html)

    def test_malformed_json_is_skipped(self):
        """Ugyldig JSON i ét script må ikke ødelægge parsing af de øvrige."""
        broken = "{ dette er ikke json !!!"
        html = _page([broken, GASTROMAND_BLOCK])
        recipe = self._run(html)
        self.assertEqual(recipe["name"], "Boeuf Bourguignon")

    def test_empty_script_is_skipped(self):
        html = _page(["", VALDEMARSRO_BLOCK])
        recipe = self._run(html)
        self.assertEqual(recipe["name"], "Klassisk lasagne")


if __name__ == "__main__":
    unittest.main(verbosity=2)
