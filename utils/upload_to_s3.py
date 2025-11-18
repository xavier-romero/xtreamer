import os
import sys
import json
import boto3
import requests
import hashlib
from urllib.parse import quote_plus


if len(sys.argv) < 3:
    print("Usage: python upload_to_s3.py config.json /path/to/movie1 /path/to/movie2 ...")  # noqa
    sys.exit(1)

config_file = sys.argv[1]
with open(config_file) as f:
    CONFIG = json.load(f)

BUCKET_NAME = CONFIG.get("s3_uploads", {}).get("aws", {}).get("s3_bucket")
TMDB_API_KEY = CONFIG.get("s3_uploads", {}).get("tmdb_api_key")
MOVIE_CATEGORY = CONFIG.get("s3_uploads", {}).get("category", "Custom")
if not BUCKET_NAME or not TMDB_API_KEY:
    print("No S3 bucket or TMDB API key configured in config file.")
    sys.exit(1)

s3 = boto3.client(
    "s3",
    aws_access_key_id=CONFIG.get("s3_uploads", {}).get("aws", {}).get("aws_access_key_id"),  # noqa
    aws_secret_access_key=CONFIG.get("s3_uploads", {}).get("aws", {}).get("aws_secret_access_key"),  # noqa
    region_name=CONFIG.get("s3_uploads", {}).get("aws", {}).get("region_name"),
)


def get_poster_url(movie_name):
    query = quote_plus(movie_name)
    url = f"https://api.themoviedb.org/3/search/movie?api_key={TMDB_API_KEY}&query={query}"  # noqa

    print(f"Fetching poster for movie: {movie_name} from URL: {url}")
    resp = requests.get(url)
    data = resp.json()

    if not data.get("results"):
        return None

    poster = data["results"][0].get("poster_path")
    if not poster:
        return None

    return f"https://image.tmdb.org/t/p/w600_and_h900_bestv2{poster}"


def upload_to_s3(filepath, key):
    _, ext = os.path.splitext(filepath)
    # check if file already exists in S3
    try:
        s3.head_object(Bucket=BUCKET_NAME, Key=key)
        print(f"File {key} already exists in S3 bucket {BUCKET_NAME}. Skipping upload.")  # noqa
        return f"s3://{BUCKET_NAME}/{key}"
    except s3.exceptions.ClientError as e:
        if e.response['Error']['Code'] != '404':
            raise
    print(f"Uploading {filepath} to S3 bucket {BUCKET_NAME} with key {key}")
    s3.upload_file(filepath, BUCKET_NAME, key)


def main():
    # Take movie paths from command-line args (skip index 0 = script name)
    movie_paths = sys.argv[2:]

    if not movie_paths:
        print("ERROR: No movie paths received.")
        print("Usage: python upload_to_s3.py config.json /path/to/movie1 /path/to/movie2 ...")  # noqa
        sys.exit(1)

    rows = []

    for path in movie_paths:
        if not os.path.isfile(path):
            print(f"Skipping (not a file): {path}")
            continue

        filename = os.path.basename(path)
        movie_name, extension = os.path.splitext(filename)
        extension = extension.replace(".", "").lower()

        # MD5 hash of filename
        hashed = hashlib.md5(filename.encode("utf-8")).hexdigest()

        print(
            f"Processing: {filename}, movie name: {movie_name}, "
            f"extension: {extension}, hashed: {hashed}"
        )

        # Upload movie
        upload_to_s3(path, hashed)

        # Fetch poster
        poster_url = get_poster_url(movie_name)

        # Movie name will be store on a CSV file, so remove commas
        movie_name = movie_name.replace(",", "")

        rows.append([movie_name, hashed, extension, poster_url])

    # Output CSV
    output_file = CONFIG.get("s3_uploads", {}).get("csv_file", "uploads.csv")
    print(f"Appending CSV output to: {output_file}")
    with open(output_file, "a") as f:
        f.write("# name,hashed_name,extension,icon_url\n")
        f.write(f"category={MOVIE_CATEGORY}\n")
        for row in rows:
            f.write(",".join([field if field else "" for field in row]) + "\n")


if __name__ == "__main__":
    main()
