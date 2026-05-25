import json
import requests
from bs4 import BeautifulSoup

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    )
}


class RecipeNotFoundError(Exception):
    pass


class HTTPError(Exception):
    pass


def _is_recipe(obj: dict) -> bool:
    type_value = obj.get("@type")
    if isinstance(type_value, list):
        return "Recipe" in type_value
    return type_value == "Recipe"


def _find_recipe_in_block(data) -> dict | None:
    """Search a parsed JSON-LD block (dict or list) for a Recipe object."""
    if isinstance(data, dict):
        # Yoast SEO / common graph wrapper
        if "@graph" in data:
            for item in data["@graph"]:
                result = _find_recipe_in_block(item)
                if result is not None:
                    return result
        if _is_recipe(data):
            return data
    elif isinstance(data, list):
        for item in data:
            result = _find_recipe_in_block(item)
            if result is not None:
                return result
    return None


def extract_raw_json_ld(url: str) -> dict:
    """
    Fetch *url* and return the first Schema.org Recipe object found in any
    <script type="application/ld+json"> block on the page.

    Raises:
        HTTPError: if the server does not return HTTP 200.
        RecipeNotFoundError: if no Recipe object is found in any ld+json block.
    """
    response = requests.get(url, headers=HEADERS, timeout=15)

    if response.status_code != 200:
        raise HTTPError(
            f"HTTP {response.status_code} for URL: {url}"
        )

    soup = BeautifulSoup(response.text, "html.parser")
    scripts = soup.find_all("script", type="application/ld+json")

    for script in scripts:
        raw = script.string
        if not raw or not raw.strip():
            continue
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            continue

        recipe = _find_recipe_in_block(data)
        if recipe is not None:
            return recipe

    raise RecipeNotFoundError(f"Ingen Recipe-objekt fundet på: {url}")


if __name__ == "__main__":
    url = input("Indtast opskrifts-URL: ").strip()
    try:
        recipe = extract_raw_json_ld(url)
        print(json.dumps(recipe, indent=2, ensure_ascii=False))
    except (HTTPError, RecipeNotFoundError) as e:
        print(f"Fejl: {e}")
    except Exception as e:
        print(f"Uventet fejl: {e}")
