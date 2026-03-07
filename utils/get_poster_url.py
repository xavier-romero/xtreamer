import re
import sys
import json
import requests
from urllib.parse import quote_plus

# THIS IS FOR FULFILLING THE POSTER_URL COLUMN IN THE S3 UPLOADS CSV FILE. IT DOES NOT UPLOAD ANYTHING TO S3.

if len(sys.argv) < 2:
    print("Usage: python get_poster_url.py config.json /path/to/movie1 /path/to/movie2 ...")  # noqa
    sys.exit(1)

config_file = sys.argv[1]
with open(config_file) as f:
    CONFIG = json.load(f)

TMDB_API_KEY = CONFIG.get("s3_uploads", {}).get("tmdb_api_key")
S3_UPLOADS_FILE = CONFIG.get("s3_uploads", {}).get("csv_file")

if not TMDB_API_KEY or not S3_UPLOADS_FILE:
    print("No TMDB API key or S3 uploads file configured in config file.")
    sys.exit(1)

def get_poster_url(movie_name):
    query = quote_plus(movie_name)
    url = f"https://api.themoviedb.org/3/search/movie?api_key={TMDB_API_KEY}&query={query}"  # noqa

    # print(f"Fetching poster for movie: {movie_name} from URL: {url}")
    resp = requests.get(url)
    data = resp.json()

    if not data.get("results"):
        return None

    poster = None

    for result in data["results"]:
        title = result.get("title", "").lower()
        original_title = result.get("original_title", "").lower()
        if movie_name.lower() == title or movie_name.lower() == original_title:
            poster = result["poster_path"]

    if not poster:
        poster = data["results"][0].get("poster_path")

    if not poster:
        return None

    return f"https://image.tmdb.org/t/p/w600_and_h900_bestv2{poster}"


def main():
    with open(S3_UPLOADS_FILE) as f:
        lines = f.readlines()

    for line in lines:
        if line.startswith("category="):
            continue
        if line.startswith("#"):
            continue
        if not line.strip():
            continue

        movie_name, hashed, extension, poster_url = line.strip().split(",")
        if poster_url:
            continue

        # Fetch poster
        poster_url = get_poster_url(movie_name) or \
            get_poster_url(re.sub(r'^\(?\d{4}\)?\s*', '', movie_name))

        print(f"{movie_name},{hashed},{extension},{poster_url}")


if __name__ == "__main__":
    main()
