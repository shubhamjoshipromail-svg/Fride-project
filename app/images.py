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
                "query": query + " dish plated food photography",
                "per_page": 3,
                "orientation": "landscape",
                "content_filter": "high",
                "order_by": "relevant",
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
            if not results:
                return None
            best = max(results, key=lambda x: x.get("likes", 0))
            return best["urls"]["regular"]
    except Exception:
        return None
    return None
