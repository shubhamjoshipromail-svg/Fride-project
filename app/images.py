import json
import urllib.parse
import urllib.request


def fetch_food_image(query: str, access_key: str) -> str | None:
    """
    Search Unsplash for a food photo matching the recipe title.
    Returns the regular-size image URL or None if unavailable.
    """
    if not access_key:
        return None
    try:
        params = urllib.parse.urlencode(
            {
                "query": query + " food recipe",
                "per_page": 1,
                "orientation": "landscape",
                "content_filter": "high",
            }
        )
        url = f"https://api.unsplash.com/search/photos?{params}"
        req = urllib.request.Request(
            url,
            headers={"Authorization": f"Client-ID {access_key}"},
        )
        with urllib.request.urlopen(req, timeout=4) as response:
            data = json.loads(response.read())
            results = data.get("results", [])
            if results:
                return results[0]["urls"]["regular"]
    except Exception:
        return None
    return None
