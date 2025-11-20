from PIL import Image, ImageDraw, ImageFont
import requests
import hashlib
import os
import json
import sys


def fetch_from_endpoint(endpoint_info):
    ep_url = \
        f"{endpoint_info['url']}/player_api.php?" \
        f"username={endpoint_info['user']}&" \
        f"password={endpoint_info['pass']}&" \
        "action="

    def _fetch(url, action):
        response = requests.get(url + action)
        if response.status_code == 200:
            return response.json()
        return []

    print(f"Fetching data from endpoint {endpoint_info['url']}...")
    result = {
        "live_categories": _fetch(ep_url, "get_live_categories"),
        "live_streams": _fetch(ep_url, "get_live_streams"),
        "movie_categories": _fetch(ep_url, "get_vod_categories"),
        "movie_streams": _fetch(ep_url, "get_vod_streams"),
        "series_categories": _fetch(ep_url, "get_series_categories"),
        "series_streams": _fetch(ep_url, "get_series"),
    }

    print(
        f"Fetched {len(result['live_streams'])} live streams with "
        f"{len(result['live_categories'])} categories and "
        f"{len(result['movie_streams'])} movies with "
        f"{len(result['movie_categories'])} categories "
        f"from {endpoint_info['url']}."
    )

    return result


def filter_data(ep_data, whitelisted_grups=[]):
    result = {}

    for ep_name in ep_data:
        data = ep_data[ep_name]
        result[ep_name] = {}

        # filter live categories and streams
        result[ep_name]["live_categories"] = [
            cat for cat in data["live_categories"]
            if any(
                cat["category_name"].lower().startswith(w.lower())
                for w in whitelisted_grups
            )
        ]

        result[ep_name]["live_streams"] = [
            stream for stream in data["live_streams"]
            if any(
                stream["category_id"] == cat["category_id"]
                for cat in result[ep_name]["live_categories"]
            )
        ]

        # filter movie categories and streams
        result[ep_name]["movie_categories"] = [
            cat for cat in data["movie_categories"]
            if any(
                cat["category_name"].lower().startswith(w.lower())
                for w in whitelisted_grups
            )
        ]
        result[ep_name]["movie_streams"] = [
            stream for stream in data["movie_streams"]
            if any(
                stream["category_id"] == cat["category_id"]
                for cat in result[ep_name]["movie_categories"]
            )
        ]
        result[ep_name]["series_streams"] = []
        result[ep_name]["series_categories"] = []
        print(
            f"Got {len(result[ep_name]['live_streams'])} live streams with "
            f"{len(result[ep_name]['live_categories'])} categories and "
            f"{len(result[ep_name]['movie_streams'])} movies with "
            f"{len(result[ep_name]['movie_categories'])} categories "
            f"after filtering data for ep {ep_name}."
        )

    return result


def process_data(data, endpoints_info, custom_live_categories):
    def live_url(stream_id, endpoint_info):
        return \
            f"{endpoint_info['url']}/" \
            f"{endpoint_info['user']}/{endpoint_info['pass']}/{stream_id}"

    def movie_url(stream_id, endpoint_info, ext="mp4"):
        return \
            f"{endpoint_info['url']}/movie/" \
            f"{endpoint_info['user']}/{endpoint_info['pass']}/" \
            f"{stream_id}.{ext}"

    result = {
        "live_categories": [
            {"category_id": k, "category_name": k}
            for k in custom_live_categories.keys()
        ],
        "live_streams": [],
        "movie_categories": [],
        "movie_streams": [],
        "series_categories": [],
        "series_streams": []
    }

    global_live_stream_id = 1
    global_movie_stream_id = 1

    # live
    for ep_name, ep_data in data.items():
        for live_cat in ep_data['live_categories']:
            new_cat_id = f"{ep_name}_{live_cat['category_id']}"
            live_cat['category_id'] = new_cat_id
            new_parent_id = f"{ep_name}_{live_cat.get('parent_id', '')}"
            live_cat['parent_id'] = new_parent_id
            result["live_categories"].append(live_cat)

        for live_stream in ep_data['live_streams']:
            new_cat_id = f"{ep_name}_{live_stream['category_id']}"
            # search if thats one of our custom categories
            for category, match_names in custom_live_categories.items():
                if any(
                    live_stream["name"].startswith(match_name)
                    for match_name in match_names
                ):
                    new_cat_id = category
            live_stream['category_id'] = new_cat_id
            live_stream["direct_source"] = \
                live_url(live_stream['stream_id'], endpoints_info[ep_name])
            live_stream["stream_id"] = global_live_stream_id
            live_stream["num"] = global_live_stream_id
            global_live_stream_id += 1
            result["live_streams"].append(live_stream)

        for movie_cat in ep_data['movie_categories']:
            new_cat_id = f"{ep_name}_{movie_cat['category_id']}"
            movie_cat['category_id'] = new_cat_id
            result["movie_categories"].append(movie_cat)

        for movie_stream in ep_data['movie_streams']:
            new_cat_id = f"{ep_name}_{movie_stream['category_id']}"
            movie_stream['category_id'] = new_cat_id
            ext = movie_stream.get("container_extension", "mp4")
            movie_stream["direct_source"] = \
                movie_url(
                    movie_stream['stream_id'], endpoints_info[ep_name], ext)
            movie_stream["stream_id"] = global_movie_stream_id
            movie_stream["num"] = global_movie_stream_id
            global_movie_stream_id += 1
            result["movie_streams"].append(movie_stream)

    return result


def retrieve_logos(data, base_url):
    for live_stream in data['live_streams']:
        logo_url = live_stream.get("stream_icon", None)
        if not logo_url:
            continue
        name = live_stream.get("name", "Unknown")
        print(f"Retrieving logo for channel: {name}")
        filename = generate_channel_logo(name, logo_url)
        live_stream["stream_icon"] = f"{base_url}/logos/{filename}"

    return data


def text_to_filename(text):
    filename = hashlib.md5(text.encode('utf-8')).hexdigest()
    filename += ".png"
    return filename


def generate_channel_logo(text, logo_url):
    filename = text_to_filename(text)

    # if it already exists just return the path
    if os.path.exists('./logos/' + filename):
        if os.path.getsize('./logos/' + filename) > 95:
            return filename
        else:
            os.remove('./logos/' + filename)

    if logo_url and logo_url.startswith("http"):
        try:
            response = requests.get(logo_url, timeout=10)
            if response.status_code == 200:
                with open('./logos/' + filename, 'wb') as f:
                    f.write(response.content)
                if os.path.getsize('./logos/' + filename) > 95:
                    return filename
                else:
                    os.remove('./logos/' + filename)
        except Exception as e:
            print(f"Error downloading logo from {logo_url}: {e}")
            print(f"Generating custom file {filename} instead.")

    # Reached that point, we're creating a custom image
    if os.path.exists('./logos/custom_' + filename):
        return 'custom_' + filename

    padding = 20
    font_size = 48
    text_color = (255, 255, 255)
    bg_color = (30, 30, 30)

    try:
        font = ImageFont.truetype("arial.ttf", font_size)
    except Exception:
        font = ImageFont.load_default()

    # Crear imagen con tamaño dinámico
    dummy = Image.new("RGB", (1, 1))
    draw_dummy = ImageDraw.Draw(dummy)

    bbox = draw_dummy.textbbox((0, 0), text, font=font)
    text_width = bbox[2] - bbox[0]
    text_height = bbox[3] - bbox[1]

    width = text_width + padding * 2
    height = text_height + padding * 2

    # Crear imagen real
    img = Image.new("RGB", (width, height), color=bg_color)
    draw = ImageDraw.Draw(img)

    # Escribir texto centrado
    text_x = (width - text_width) // 2
    text_y = (height - text_height) // 2

    draw.text((text_x, text_y), text, font=font, fill=text_color)

    filename = f"custom_{filename}"
    img.save('./logos/' + filename, "PNG")
    return filename


if __name__ == "__main__":

    if len(sys.argv) < 2:
        print("Usage: python create_data.py config.json [steps_from]")
        sys.exit(1)

    config_file = sys.argv[1]
    with open(config_file) as f:
        CONFIG = json.load(f)

    json_data_file = CONFIG.get('json_data_file', 'final_data.json')
    steps_from = int(sys.argv[2]) if len(sys.argv) > 2 else 0

    if steps_from <= 0:
        upstream_data = {}
        for ep_name, ep_info in CONFIG['endpoints'].items():
            print(f"Processing endpoint: {ep_name}")
            # raw info from upstream endpoint
            upstream_data[ep_name] = fetch_from_endpoint(ep_info)
        with open("0_upstream_data.json", "w") as f:
            json.dump(upstream_data, f, indent=4)
    else:
        with open("0_upstream_data.json", "r") as f:
            upstream_data = json.load(f)

    # filtered data after applying whitelists/blacklists
    if steps_from <= 1:
        filtered_data = filter_data(
            upstream_data,
            whitelisted_grups=CONFIG.get("whitelisted_grups", [])
        )
        with open("1_filtered_data.json", "w") as f:
            json.dump(filtered_data, f, indent=4)
    else:
        with open("1_filtered_data.json", "r") as f:
            filtered_data = json.load(f)

    # processed data with direct source URLs and reordered categories
    if steps_from <= 2:
        processed_data = process_data(
            filtered_data, CONFIG['endpoints'],
            CONFIG.get("custom_live_categories", {})
        )
        with open("2_processed_data.json", "w") as f:
            json.dump(processed_data, f, indent=4)
    else:
        with open("2_processed_data.json", "r") as f:
            processed_data = json.load(f)

    if steps_from <= 3:
        # Download icons and create missing ones
        final_data = retrieve_logos(processed_data, CONFIG['base_url'])
        with open(json_data_file, "w") as f:
            json.dump(final_data, f, indent=4)
