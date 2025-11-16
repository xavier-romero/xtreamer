from flask import Flask, request, jsonify, Response, send_from_directory
from PIL import Image, ImageDraw, ImageFont
import requests
import hashlib
import os
import re
import json
import time
import sys


CONFIG = {}
app = Flask(__name__)


def parse_m3u():
    live_streams = []
    movie_streams = []
    live_categories_dict = {}
    movie_categories_dict = {}

    with open(CONFIG["file"], "r", encoding="utf-8") as f:
        lines = f.readlines()

    for i, line in enumerate(lines):
        line = line.strip()
        if line.startswith("#EXTINF:"):
            name_match = re.search(r'tvg-name="([^"]+)"', line)
            name = name_match.group(1) if name_match else "Unknown"
            # id_match = re.search(r'tvg-id="([^"]+)"', line)
            group_match = re.search(r'group-title="([^"]+)"', line)
            group = group_match.group(1) if group_match else "Other"
            logo_match = re.search(r'tvg-logo="([^"]+)"', line)
            logo = logo_match.group(1) if logo_match else ""
            url = lines[i + 1].strip() if i + 1 < len(lines) else None

            if group not in CONFIG['whitelisted_grups']:
                if any(
                    group.startswith(prefix)
                    for prefix in CONFIG['blacklisted_grup_prefixes']
                ):
                    continue

            entry = {
                "stream_id": i + 1,
                "name": name,
                "category_id": group,
                # Wrong movie icons on m3u, let the client handle it
                "stream_icon": None,
                "direct_source": url,
                "added": int(time.time()),
            }
            if '/movie/' in url:
                entry["stream_type"] = "movie"
                movie_streams.append(entry)
                movie_categories_dict[group] = {
                    "category_id": group, "category_name": group
                }
            else:
                # Many live channels lack logos or point to dead URLs
                logo_filename = generate_channel_logo(name, logo)
                entry["stream_icon"] = \
                    f"{CONFIG['base_url']}/logos/{logo_filename}"
                entry["stream_type"] = "live"
                entry["tv_archive"] = 0
                live_streams.append(entry)
                live_categories_dict[group] = {
                    "category_id": group, "category_name": group
                }

    CONFIG["live_streams"] = live_streams
    CONFIG["live_categories"] = list(live_categories_dict.values())
    CONFIG["movie_streams"] = movie_streams
    CONFIG["movie_categories"] = list(movie_categories_dict.values())
    CONFIG["series_streams"] = []
    CONFIG["series_categories"] = []
    print(
        f"Loaded {len(CONFIG['live_streams'])} live streams with "
        f"{len(CONFIG['live_categories'])} categories and "
        f"{len(CONFIG['movie_streams'])} movies with "
        f"{len(CONFIG['movie_categories'])} categories from {CONFIG['file']}."
    )


def parse_from_endpoint():
    ep_url = \
        f"{CONFIG['endpoint']['host']}/player_api.php?" \
        f"username={CONFIG['endpoint']['user']}&" \
        f"password={CONFIG['endpoint']['pass']}&" \
        "action="

    def _fetch(url, action):
        response = requests.get(url + action)
        if response.status_code == 200:
            return response.json()
        return []

    CONFIG["live_categories"] = [
        {"category_id": k, "category_name": k}
        for k in CONFIG["custom_live_categories"]
    ]
    CONFIG["live_categories"].extend([
        cat for cat in _fetch(ep_url, "get_live_categories")
        if cat["category_name"] in CONFIG['whitelisted_grups']
        or not any(
            cat["category_name"].startswith(prefix)
            for prefix in CONFIG['blacklisted_grup_prefixes']
        )
    ])
    CONFIG["live_streams"] = [
        stream for stream in _fetch(ep_url, "get_live_streams")
        if any(
            stream["category_id"] == cat["category_id"]
            for cat in CONFIG["live_categories"]
        )
    ]
    for stream in CONFIG["live_streams"]:
        for category, match_names in CONFIG["custom_live_categories"].items():
            if any(
                stream["name"].startswith(match_name)
                for match_name in match_names
            ):
                stream["category_id"] = category

    CONFIG["movie_categories"] = [
        cat for cat in _fetch(ep_url, "get_vod_categories")
        if cat["category_name"] in CONFIG['whitelisted_grups']
        or not any(
            cat["category_name"].startswith(prefix)
            for prefix in CONFIG['blacklisted_grup_prefixes']
        )
    ]
    CONFIG["movie_streams"] = [
        stream for stream in _fetch(ep_url, "get_vod_streams")
        if any(
            stream["category_id"] == cat["category_id"]
            for cat in CONFIG["movie_categories"]
        )
    ]
    CONFIG["series_streams"] = []
    CONFIG["series_categories"] = []
    print(
        f"Loaded {len(CONFIG['live_streams'])} live streams with "
        f"{len(CONFIG['live_categories'])} categories and "
        f"{len(CONFIG['movie_streams'])} movies with "
        f"{len(CONFIG['movie_categories'])} categories "
        f"from {CONFIG['endpoint']}."
    )


def text_to_filename(text):
    filename = hashlib.md5(text.encode('utf-8')).hexdigest()
    filename += ".png"
    return filename


def retrieve_logos():
    for live_stream in CONFIG['live_streams']:
        logo_url = live_stream.get("stream_icon", "")
        name = live_stream.get("name", "Unknown")
        filename = generate_channel_logo(name, logo_url)
        live_stream["stream_icon"] = f"{CONFIG['base_url']}/logos/{filename}"


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


def check_login(user, pwd):
    for cred in CONFIG.get("credentials", []):
        if user == cred["username"] and pwd == cred["password"]:
            return True
    return False


@app.route("/player_api.php")
def player_api():
    username = request.args.get("username")
    password = request.args.get("password")
    action = request.args.get("action")
    vod_id = int(request.args.get("vod_id", 0))

    if not check_login(username, password):
        return jsonify({"user_info": {"auth": 0}})

    if action is None:
        return jsonify({
            "user_info": {"auth": 1},

            "available_channels": CONFIG['live_streams'],
            "available_categories": CONFIG['live_categories'],

            "movie_data": CONFIG['movie_streams'],
            "movie_categories": CONFIG['movie_categories'],
        })

    if action == "get_live_categories":
        return jsonify(CONFIG['live_categories'])

    if action == "get_live_streams":
        return jsonify(CONFIG['live_streams'])

    if action == "get_vod_categories":
        return jsonify(CONFIG['movie_categories'])

    if action == "get_vod_streams":
        return jsonify(CONFIG['movie_streams'])

    if action == "get_series_categories":
        return jsonify(CONFIG['series_categories'])

    if action == "get_series":
        return jsonify(CONFIG['series_streams'])

    if action == "get_vod_info":
        vod = next(
            (m for m in CONFIG['movie_streams'] if m["stream_id"] == vod_id),
            None
        )
        if not vod:
            return jsonify({"error": "vod not found"})

        return jsonify({
            "info": {
                "name": vod["name"],
                "movie_id": vod_id,
                "stream_type": "movie",
                "director": "",
                "cast": "",
                "rating": "",
                "description": "",
                "genre": vod.get("category_id", ""),
                "cover": vod.get("stream_icon", "")
            },
            "movie_data": {
                "stream_id": vod_id,
                "name": vod["name"],
                "container_extension": vod.get("container_extension", "mp4"),
                "stream_source": [vod["direct_source"]],
                "custom_sid": "",
                "direct_source": vod["direct_source"],
                "stream_link": vod["direct_source"]
            }
        })

    return jsonify({"error": "Unknown action"}), 400


@app.route("/<username>/<password>/<int:stream_id>")
@app.route("/live/<username>/<password>/<int:stream_id>.ts")
@app.route("/movie/<username>/<password>/<int:stream_id>.<extension>")
@app.route("/movie/<username>/<password>/<int:stream_id>")
def proxy_stream(username, password, stream_id, extension=None):
    if not check_login(username, password):
        return "Unauthorized", 401

    redirect_url = request.url
    redirect_url = redirect_url.replace(username, CONFIG['endpoint']['user'])
    redirect_url = redirect_url.replace(password, CONFIG['endpoint']['pass'])
    redirect_url = \
        redirect_url.replace(CONFIG['base_url'], CONFIG['endpoint']['host'])

    return Response(
        f"Redirecting to {redirect_url}", status=302,
        headers={"Location": redirect_url}
    )

    return "Stream not found", 404


@app.route("/xmltv.php")
def xmltv():
    epg = '<?xml version="1.0" encoding="UTF-8"?><tv></tv>'
    return Response(epg, content_type="application/xml")


@app.route("/logos/<path:filename>")
def logos(filename):
    if not re.match(r"^[a-f0-9]{32}\.[a-z]{3,4}$", filename):
        return "400 Invalid filename", 400
    else:
        return send_from_directory("logos", filename)


if __name__ == "__main__":
    config_file = sys.argv[1] if len(sys.argv) > 1 else "config.json"
    with open(config_file) as f:
        CONFIG = json.load(f)

    if not CONFIG:
        print("Error: config.json is empty or invalid!")
        exit(1)

    if CONFIG["credentials"] == [] or any(
        cred["username"] == "" or cred["password"] == ""
        for cred in CONFIG["credentials"]
    ):
        print("Error: No credentials set in config.json!")
        exit(1)

    if CONFIG.get("endpoint"):
        print("Parsing from endpoint...")
        parse_from_endpoint()
    elif CONFIG.get("file"):
        print("Parsing from M3U file...")
        parse_m3u()
    else:
        print("Error: No valid source (file or endpoint) in config.json!")
        exit(1)

    retrieve_logos()

    port = \
        int(CONFIG['base_url'].split(":")[-1]) \
        if ":" in CONFIG['base_url'] else 8080
    app.run(host="0.0.0.0", port=port, debug=False)
