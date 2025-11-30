import sys
import json
import os
from time import time


if len(sys.argv) < 2:
    print("Usage: python add_vod_files.py config.json")
    sys.exit(1)

config_file = sys.argv[1]
with open(config_file) as f:
    CONFIG = json.load(f)

json_data_file = CONFIG.get('json_data_file', 'final_data.json')
with open(json_data_file, "r") as f:
    data = json.load(f)

vod_nums = [
    movie_stream.get('num') for movie_stream in data.get('movie_streams')
]
stream_ids = [
    movie_stream.get('stream_id') for movie_stream in data.get('movie_streams')
]

next_vod_num = max(vod_nums) + 1
next_stream_id = max(stream_ids) + 1

category_map = {
    cat['category_name']: cat['category_id']
    for cat in data.get('movie_categories', [])
}

csv_file = CONFIG.get("s3_uploads", {}).get("csv_file", "uploads.csv")
category_name = CONFIG.get("s3_uploads", {}).get("category", "Custom")
print(f"Processing category: {category_name}")
movies_added = 0

if not os.path.exists(csv_file):
    print(f"File {csv_file} not found.")
    sys.exit(1)

with open(csv_file, "r") as f:
    for line in f:
        if not line:
            break
        if line.startswith("#"):
            continue

        # if line is: category=category_name
        if line.startswith("category="):
            category_name = line.strip().split("=", 1)[1]
            print(f"Switching to category: {category_name}")
            continue

        fields = line.strip().split(',')

        movie_name = fields[0]
        hashed_name = fields[1]
        extension = fields[2]
        icon_url = fields[3]

        # Check if movie already exists
        exists = False
        for movie_stream in data.get('movie_streams', []):
            if movie_stream.get('name') == movie_name:
                if movie_stream.get('category_id') == str(category_map.get(category_name)):  # noqa
                    print(f"Movie {movie_name} already exists. Skipping.")
                    exists = True
                    break
                else:
                    print(f"Movie {movie_name} exists but in different category. Adding to {category_name}.")  # noqa
        if exists:
            continue

        print(f"Adding movie: {movie_name} with extension: {extension}")
        movies_added += 1
        category_id = category_map.get(category_name)
        if not category_id:
            print(f"Adding new category: {category_name}")
            category_id = category_name.replace(' ', '_').lower()
            category_map[category_name] = category_id
            new_category = {
                "category_id": str(category_id),
                "category_name": category_name
            }
            # Add in first position, so clients show it first
            data['movie_categories'].insert(0, new_category)

        vod = {
            "num": next_vod_num,
            "name": movie_name,
            "stream_type": "movie",
            "stream_id": next_stream_id,
            "stream_icon": icon_url,
            "added": int(time()),
            "category_id": str(category_id),
            "container_extension": extension,
            "s3_hashed_name": hashed_name
        }
        next_vod_num += 1
        next_stream_id += 1
        data['movie_streams'].append(vod)

if movies_added > 0:
    with open(json_data_file, "w") as f:
        json.dump(data, f, indent=4)
    print(f"Added {movies_added} movies to {json_data_file}.")
else:
    print("No new movies were added.")
